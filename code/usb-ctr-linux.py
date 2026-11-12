#!/usr/bin/env python3
"""
usb-ctr-linux.py - drive and verify the HIL USB-C Control board on Linux.

Writes go through the kernel's cp210x gpiochip (GPIO character device v2), i.e.
the same path libgpiod uses, so a pass here validates the Linux contract in
architecture_final.md section 6. Reads of identity and the NVM image use plain
usbfs control transfers. Standard library only: no pyusb, no libgpiod.

This script is self-contained: the signal maps, NVM expectations and fleet
record format are defined here, so it does not need usb-ctr-mac.py (the macOS
implementation). Both scripts support the same three board generations:

    v2.0   CP2102N QFN28, seven lines, Release Version 2.00
    v1.1   CP2102N QFN24, four lines, Release Version 1.01 (CC_EN inverted)
    v1.0   CP2102N QFN24, four lines, Release Version 1.00

Commands
    info      identity, gpiochip, NVM image summary
    check     read the NVM back and compare it to the board's image
    list      connected boards with port id, board generation, serial, gpiochip
    status    current state of every line (read-only; every board if several
              are connected and none is selected)
    safe      force the power-up safe state
    on        run the section 6.4 connect sequence
    off       run the section 6.4 disconnect sequence
    toggle    connect if disconnected, else disconnect
    ext       drive the J4 opto-isolated line (V2.0 only)
    watch     poll the lines until interrupted
    verify    full per-unit acceptance check, including DUT enumeration

check and verify both accept --record code/fleet-record.tsv. On a legacy board
there is no VBUS sense, source select or opto line, so on/off substitute a
settle delay for the VBUS confirmation, ext is refused and --high-current
cannot be checked. --board auto|v2.0|v1.1|v1.0 overrides the detection.

Several boards: with more than one attached, commands other than list/status
refuse to guess. Select one by row index or USB port id from `list`, by
--serial, or act on all of them. The previous board's syntax works too:
    code/usb-ctr-linux.py list
    code/usb-ctr-linux.py 0 on                  (row index; shifts on unplug)
    code/usb-ctr-linux.py 1-9.1.1.2.1 toggle    (port id; stable per hub port)
    code/usb-ctr-linux.py --serial 0003 off
    code/usb-ctr-linux.py all-off               (same as --all off)

Needs read/write access to /dev/gpiochipN (group plugdev or an ACL) and to
/dev/bus/usb/BBB/DDD. Examples
    code/usb-ctr-linux.py verify --expect-serial 0005
    code/usb-ctr-linux.py on
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import fcntl
import glob
import os
import struct
import sys
import time
from pathlib import Path

USB_SYSFS = "/sys/bus/usb/devices"

# --------------------------------------------------------------------------
# CP210x protocol and board profiles
#
# The vendor requests mirror drivers/usb/serial/cp210x.c. Line index == GPIO
# number in the gpiochip, so the profile pin numbers below are also the GPIO
# line offsets.
# --------------------------------------------------------------------------

VID_SILABS = 0x10C4
DEFAULT_PIDS = (0xEA60, 0xEA61, 0xEA63, 0xEA70)

REQ_VENDOR_SPECIFIC = 0xFF
REQTYPE_HOST_TO_DEVICE = 0x40
REQTYPE_DEVICE_TO_HOST = 0xC0

VAL_READ_LATCH = 0x00C2
VAL_WRITE_LATCH = 0x37E1
VAL_GET_PARTNUM = 0x370B
VAL_GET_FW_VER_2N = 0x0010
VAL_READ_2NCONFIG = 0x000E

PARTNUM_CP2102N_QFN28 = 0x20
PARTNUM_CP2102N_QFN24 = 0x21
PARTNUM_CP2102N_QFN20 = 0x22
PARTNUM_NAMES = {
    0x01: "CP2101",
    0x02: "CP2102",
    0x03: "CP2103",
    0x04: "CP2104",
    0x05: "CP2105",
    0x08: "CP2108",
    PARTNUM_CP2102N_QFN28: "CP2102N QFN28",
    PARTNUM_CP2102N_QFN24: "CP2102N QFN24",
    PARTNUM_CP2102N_QFN20: "CP2102N QFN20",
    0xFF: "unknown",
}

CFG_SIZE = 678
IDX_CFG_VERSION = 2
IDX_RELEASE_VERSION = 17
IDX_GPIO_MODE = 581
IDX_GPIO_RSTLATCH = 587
IDX_GPIO_CONTROL = 600
IDX_SERIAL_DESC = 450

EXPECT_RELEASE_VERSION = 0x0200
LEGACY_RELEASE_V10 = 0x0100
LEGACY_RELEASE_V11 = 0x0101

# --expect-release overrides the profile default for this run.
EXPECT_RELEASE_OVERRIDE = None

# V2.0 map - architecture_final.md section 6.1.
HIL_FLT_N = 0
VBUS_SNS = 1
CC_EN_N = 2
U14_ST = 3
DATA_EN_N = 4
EXT_IO0 = 5
VBUS_EN = 6

OUTPUT_PINS = (CC_EN_N, DATA_EN_N, EXT_IO0, VBUS_EN)
SAFE_STATE = {CC_EN_N: 1, DATA_EN_N: 1, EXT_IO0: 1, VBUS_EN: 0}
PIN_NAMES = {
    HIL_FLT_N: "hil_flt_n",
    VBUS_SNS: "vbus_hil_sns",
    CC_EN_N: "cc_en_n",
    U14_ST: "u14_st",
    DATA_EN_N: "data_en_n",
    EXT_IO0: "ext_io0",
    VBUS_EN: "vbus_en",
}

EXPECT_PUSHPULL = 1 << VBUS_EN
EXPECT_RSTLATCH = 0x7F & ~(1 << VBUS_EN)
EXPECT_ALTFUNC_GPIO0_3 = 0

# Legacy usb-passthrough map.
L_CC_EN = 0
L_USB_SEL = 1
L_VBUS_EN = 2
L_VBUS_FLT = 3

LEGACY_OUTPUT_PINS = (L_CC_EN, L_USB_SEL, L_VBUS_EN)
LEGACY_PIN_NAMES = {
    L_CC_EN: "cc_en",
    L_USB_SEL: "usb_sel",
    L_VBUS_EN: "vbus_en",
    L_VBUS_FLT: "vbus_flt",
}

LEGACY_EXPECT_PUSHPULL = 0
LEGACY_EXPECT_RSTLATCH = 0x0F
LEGACY_EXPECT_ALTFUNC = 0


class BoardProfile:
    """Pin map, polarity and sense lines of one board generation."""

    def __init__(self, key, label, partnum, pin_names, outputs, safe_state,
                 cc_pin, data_pin, vbus_pin, fault_pin,
                 cc_connected_level, data_connected_level, vbus_on_level,
                 pushpull_pins=(), sense_pin=None, source_pin=None,
                 ext_pin=None):
        self.key = key
        self.label = label
        self.partnum = partnum
        self.pin_names = pin_names
        self.outputs = outputs
        self.safe_state = safe_state
        self.cc_pin = cc_pin
        self.data_pin = data_pin
        self.vbus_pin = vbus_pin
        self.fault_pin = fault_pin
        self.sense_pin = sense_pin
        self.source_pin = source_pin
        self.ext_pin = ext_pin
        self.cc_connected_level = cc_connected_level
        self.cc_cut_level = 1 - cc_connected_level
        self.data_connected_level = data_connected_level
        self.data_open_level = 1 - data_connected_level
        self.vbus_on_level = vbus_on_level
        self.vbus_off_level = 1 - vbus_on_level
        self.pushpull_pins = tuple(pushpull_pins)
        self.gpio_count = len(pin_names)
        self.qfn28 = partnum == PARTNUM_CP2102N_QFN28

    def _bit(self, snap: dict[str, int], pin: int) -> int:
        return snap[self.pin_names[pin]]

    def cc_connected(self, snap) -> bool:
        return self._bit(snap, self.cc_pin) == self.cc_connected_level

    def data_connected(self, snap) -> bool:
        return self._bit(snap, self.data_pin) == self.data_connected_level

    def vbus_on(self, snap) -> bool:
        return self._bit(snap, self.vbus_pin) == self.vbus_on_level

    def fault(self, snap) -> bool:
        return self._bit(snap, self.fault_pin) == 0

    def vbus_present(self, snap):
        if self.sense_pin is None:
            return None
        return self._bit(snap, self.sense_pin) == 1

    def source(self, snap):
        if self.source_pin is None:
            return None
        return "vbus_up" if self._bit(snap, self.source_pin) else "vbus_pwr"

    def ext_asserted(self, snap):
        if self.ext_pin is None:
            return None
        return self._bit(snap, self.ext_pin) == 0


BOARD_V2 = BoardProfile(
    key="v2.0",
    label="usb-c-one V2.0 (CP2102N QFN28, 7 lines)",
    partnum=PARTNUM_CP2102N_QFN28,
    pin_names=PIN_NAMES,
    outputs=OUTPUT_PINS,
    safe_state=SAFE_STATE,
    cc_pin=CC_EN_N, data_pin=DATA_EN_N, vbus_pin=VBUS_EN,
    fault_pin=HIL_FLT_N, sense_pin=VBUS_SNS, source_pin=U14_ST, ext_pin=EXT_IO0,
    cc_connected_level=0, data_connected_level=0, vbus_on_level=1,
    pushpull_pins=(VBUS_EN,),
)

BOARD_V11 = BoardProfile(
    key="v1.1",
    label="usb-passthrough v1.1 (CP2102N QFN24, 4 lines, CC_EN inverted)",
    partnum=PARTNUM_CP2102N_QFN24,
    pin_names=LEGACY_PIN_NAMES,
    outputs=LEGACY_OUTPUT_PINS,
    safe_state={L_CC_EN: 1, L_USB_SEL: 0, L_VBUS_EN: 0},
    cc_pin=L_CC_EN, data_pin=L_USB_SEL, vbus_pin=L_VBUS_EN,
    fault_pin=L_VBUS_FLT,
    cc_connected_level=0, data_connected_level=1, vbus_on_level=1,
)

BOARD_V10 = BoardProfile(
    key="v1.0",
    label="usb-passthrough v1.0 (CP2102N QFN24, 4 lines)",
    partnum=PARTNUM_CP2102N_QFN24,
    pin_names=LEGACY_PIN_NAMES,
    outputs=LEGACY_OUTPUT_PINS,
    safe_state={L_CC_EN: 0, L_USB_SEL: 0, L_VBUS_EN: 0},
    cc_pin=L_CC_EN, data_pin=L_USB_SEL, vbus_pin=L_VBUS_EN,
    fault_pin=L_VBUS_FLT,
    cc_connected_level=1, data_connected_level=1, vbus_on_level=1,
)

PROFILES = {profile.key: profile for profile in (BOARD_V2, BOARD_V11, BOARD_V10)}
LEGACY_DEFAULT = BOARD_V11


def expected_release(board: BoardProfile) -> int:
    if EXPECT_RELEASE_OVERRIDE is not None:
        return EXPECT_RELEASE_OVERRIDE
    if board is BOARD_V2:
        return EXPECT_RELEASE_VERSION
    return LEGACY_RELEASE_V10 if board is BOARD_V10 else LEGACY_RELEASE_V11


def expected_image(board: BoardProfile):
    if board is BOARD_V2:
        return EXPECT_PUSHPULL, EXPECT_RSTLATCH, EXPECT_ALTFUNC_GPIO0_3
    return LEGACY_EXPECT_PUSHPULL, LEGACY_EXPECT_RSTLATCH, LEGACY_EXPECT_ALTFUNC


def bcd_u16(value: int) -> str:
    hi = ((value >> 12) & 0xF) * 10 + ((value >> 8) & 0xF)
    lo = ((value >> 4) & 0xF) * 10 + (value & 0xF)
    return f"{hi}.{lo:02d}"


def decode_serial(buf: bytes) -> str:
    """The serial-number string descriptor stored in the config array."""
    if len(buf) < IDX_SERIAL_DESC + 2:
        return ""
    length = buf[IDX_SERIAL_DESC]
    desc_type = buf[IDX_SERIAL_DESC + 1]
    if desc_type != 0x03 or length < 2 or length % 2 or length > 64:
        return ""
    try:
        return buf[IDX_SERIAL_DESC + 2:IDX_SERIAL_DESC + length].decode("utf-16-le")
    except UnicodeDecodeError:
        return ""


def fletcher16(data: bytes) -> int:
    """AN978 Fletcher-16 (mod 255, init 0xFF), as the CP2102N stores it."""
    s1 = s2 = 0xFF
    for i in range(0, len(data), 20):
        for value in data[i:i + 20]:
            s1 += value
            s2 += s1
        s1 = (s1 & 0xFF) + (s1 >> 8)
        s2 = (s2 & 0xFF) + (s2 >> 8)
    s1 = (s1 & 0xFF) + (s1 >> 8)
    s2 = (s2 & 0xFF) + (s2 >> 8)
    return (s2 << 8) | s1


def config_checksum_ok(buf: bytes) -> bool:
    if len(buf) < CFG_SIZE:
        return False
    stored = int.from_bytes(buf[CFG_SIZE - 2:CFG_SIZE], "big")
    return fletcher16(buf[:CFG_SIZE - 2]) == stored


def decode_config(buf: bytes, qfn28: bool = True) -> dict:
    """Split the 678-byte CP2102N configuration into the fields we care about."""
    if len(buf) < CFG_SIZE:
        raise ValueError(f"short config read: {len(buf)} of {CFG_SIZE} bytes")

    version = buf[IDX_CFG_VERSION]
    release = int.from_bytes(
        buf[IDX_RELEASE_VERSION:IDX_RELEASE_VERSION + 2], "little")
    mode = buf[IDX_GPIO_MODE]
    rstlatch = buf[IDX_GPIO_RSTLATCH]
    control = buf[IDX_GPIO_CONTROL]

    pushpull = (mode >> 3) & 0x0F
    latch = (rstlatch >> 3) & 0x0F
    if qfn28:
        pushpull |= (mode & 0x07) << 4
        latch |= (rstlatch & 0x07) << 4
    altfunc = (control >> 2) & 0x0F

    return {
        "version": version,
        "release_version": release,
        "serial": decode_serial(buf),
        "checksum_ok": config_checksum_ok(buf),
        "raw_mode": mode,
        "raw_rstlatch": rstlatch,
        "raw_control": control,
        "pushpull": pushpull,
        "rstlatch": latch,
        "altfunc": altfunc,
    }


# --------------------------------------------------------------------------
# Fleet records - one line per read-back or verified unit, tab-separated
# --------------------------------------------------------------------------

FLEET_FIELDS = ("utc", "serial", "result", "release", "bcd_device", "config",
                "pushpull", "rstlatch", "altfunc", "vbus_up_ms", "dut",
                "failed", "notes", "board")


def fleet_load(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t", restval=""))


def fleet_append(path: str, record: dict) -> None:
    fresh = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FLEET_FIELDS, delimiter="\t",
                                extrasaction="ignore", lineterminator="\n")
        if fresh:
            writer.writeheader()
        writer.writerow(record)


def fleet_already_seen(path: str, serial: str):
    for rec in fleet_load(path):
        if rec.get("serial", "") == serial:
            return rec
    return None


# --------------------------------------------------------------------------
# ioctl plumbing
# --------------------------------------------------------------------------


def _ioc(direction: int, typ: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (typ << 8) | nr


_IOR, _IOWR = 2, 3

# linux/gpio.h, uapi v2
GPIO_CHIPINFO = _ioc(_IOR, 0xB4, 0x01, 68)
GPIO_V2_LINEINFO = _ioc(_IOWR, 0xB4, 0x05, 256)
GPIO_V2_GET_LINE = _ioc(_IOWR, 0xB4, 0x07, 592)
GPIO_V2_GET_VALUES = _ioc(_IOWR, 0xB4, 0x0E, 16)
GPIO_V2_SET_VALUES = _ioc(_IOWR, 0xB4, 0x0F, 16)

FLAG_INPUT = 1 << 2
FLAG_OUTPUT = 1 << 3
FLAG_OPEN_DRAIN = 1 << 6
ATTR_FLAGS, ATTR_OUTPUT_VALUES = 1, 2

# linux/usbdevice_fs.h
USBDEVFS_CONTROL = _ioc(_IOWR, ord("U"), 0, 24)


class _CtrlTransfer(ctypes.Structure):
    _fields_ = [("bRequestType", ctypes.c_uint8), ("bRequest", ctypes.c_uint8),
                ("wValue", ctypes.c_uint16), ("wIndex", ctypes.c_uint16),
                ("wLength", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


# --------------------------------------------------------------------------
# Board discovery
# --------------------------------------------------------------------------


def _sysfs(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


class Board:
    """One CP2102N: its usb device, usbfs node and gpiochip node."""

    def __init__(self, name: str, board_override: str = "auto"):
        self.name = name
        self.board_override = board_override
        self._profile: BoardProfile | None = None
        self.path = os.path.join(USB_SYSFS, name)
        self.serial = _sysfs(f"{self.path}/serial")
        self.product = _sysfs(f"{self.path}/product")
        self.bcd_device = int(_sysfs(f"{self.path}/bcdDevice").replace(".", "") or "0", 16)
        self.pid = int(_sysfs(f"{self.path}/idProduct"), 16)
        self.usbfs = "/dev/bus/usb/%03d/%03d" % (
            int(_sysfs(f"{self.path}/busnum")), int(_sysfs(f"{self.path}/devnum")))
        chips = glob.glob(f"{self.path}/{name}:1.0/gpiochip*")
        self.gpiochip = "/dev/" + os.path.basename(chips[0]) if chips else None
        ttys = glob.glob(f"{self.path}/{name}:1.0/ttyUSB*")
        self.tty = "/dev/" + os.path.basename(ttys[0]) if ttys else None

    @property
    def profile(self) -> BoardProfile:
        """Board generation, from the part number and NVM Release Version."""
        if self._profile is None:
            self._profile = detect_profile(self, self.board_override)
        return self._profile

    def ctrl(self, reqtype: int, request: int, value: int, index: int,
             length: int = 0, data: bytes | None = None) -> bytes:
        buf = ctypes.create_string_buffer(data or bytes(length), max(length, len(data or b"")))
        xfer = _CtrlTransfer(reqtype, request, value, index, length, 2000,
                             ctypes.cast(buf, ctypes.c_void_p).value)
        fd = os.open(self.usbfs, os.O_RDWR)
        try:
            n = fcntl.ioctl(fd, USBDEVFS_CONTROL, bytearray(bytes(xfer)))
        finally:
            os.close(fd)
        return buf.raw[:max(int(n), 0)] if length else b""

    def read_latch(self) -> int:
        """Raw GPIO latch over the vendor request; no side effects."""
        return self.ctrl(REQTYPE_DEVICE_TO_HOST, REQ_VENDOR_SPECIFIC,
                         VAL_READ_LATCH, 0, 1)[0]

    def part_number(self) -> int:
        return self.ctrl(REQTYPE_DEVICE_TO_HOST, REQ_VENDOR_SPECIFIC,
                         VAL_GET_PARTNUM, 0, 1)[0]

    def firmware(self) -> str:
        return ".".join(str(b) for b in self.ctrl(
            REQTYPE_DEVICE_TO_HOST, REQ_VENDOR_SPECIFIC,
            VAL_GET_FW_VER_2N, 0, 3))

    def read_config(self) -> bytes:
        return self.ctrl(REQTYPE_DEVICE_TO_HOST, REQ_VENDOR_SPECIFIC,
                         VAL_READ_2NCONFIG, 0, CFG_SIZE)

    def snapshot(self) -> dict:
        latch = self.read_latch()
        return {n: (latch >> p) & 1 for p, n in self.profile.pin_names.items()}


def detect_profile(board: Board, override: str | None = None) -> BoardProfile:
    """Identify the board generation.

    The part number decides the pin map: QFN28 = V2.0, QFN24 = legacy. On a
    QFN24 the NVM Release Version names the revision (1.00 = v1.0, 1.01 =
    v1.1). --board overrides the detection when the silkscreen is the better
    authority.
    """
    if override and override != "auto":
        return PROFILES[override]

    partnum = board.part_number()
    release = decode_config(
        board.read_config(),
        partnum == PARTNUM_CP2102N_QFN28)["release_version"]

    if partnum == PARTNUM_CP2102N_QFN28:
        return BOARD_V2
    if partnum == PARTNUM_CP2102N_QFN24:
        return BOARD_V10 if release == LEGACY_RELEASE_V10 else BOARD_V11
    return BOARD_V2 if release == EXPECT_RELEASE_VERSION else LEGACY_DEFAULT


def _port_key(name: str) -> list[int]:
    """Numeric ordering of '1-9.1.1.2' so '1-10' sorts after '1-9'."""
    return [int(n) for n in name.replace("-", ".").split(".") if n.isdigit()]


def discover(args) -> list[Board]:
    """Every matching board, ordered by USB port path (stable across replugs)."""
    pids = (args.pid,) if args.pid else DEFAULT_PIDS
    found = []
    for name in sorted(os.listdir(USB_SYSFS), key=_port_key):
        if ":" in name or _sysfs(f"{USB_SYSFS}/{name}/idVendor") != "10c4":
            continue
        if int(_sysfs(f"{USB_SYSFS}/{name}/idProduct") or "0", 16) in pids:
            found.append(Board(name, getattr(args, "board", "auto")))
    return found


def _board_table(boards: list[Board]) -> str:
    rows = [f"  {'IDX':<4}{'PORT':<16}{'BOARD':<6}{'SERIAL':<10}"
            f"{'GPIOCHIP':<16}TTY"]
    for i, b in enumerate(boards):
        try:
            board = b.profile.key
        except (OSError, IndexError):
            board = "?"
        rows.append(f"  {i:<4}{b.name:<16}{board:<6}{b.serial or '-':<10}"
                    f"{b.gpiochip or '-':<16}{b.tty or '-'}")
    return "\n".join(rows)


def select_boards(args, allow_all: bool) -> list[Board]:
    """Resolve the board selection. With no selector and several boards
    attached this refuses to guess, because the wrong board means the wrong DUT
    gets power."""
    boards = discover(args)
    if not boards:
        sys.exit("No CP2102x found (VID 10c4).\n"
                 "  - check `lsusb -d 10c4:` and that the board is on J1 of this host\n"
                 "  - after programming, the PID may change: pass --pid 0x????")
    if args.serial:
        boards = [b for b in boards if b.serial == args.serial]
        if not boards:
            sys.exit(f"No board with serial {args.serial!r}. Connected boards:\n"
                     + _board_table(discover(args)))
    everything = boards
    if args.port is not None:
        # all digits = row index from `list`, anything else = port id like 1-9.1.1.2.1
        if args.port.isdigit():
            boards = everything[int(args.port):int(args.port) + 1]
        else:
            boards = [b for b in everything if b.name == args.port]
        if not boards:
            sys.exit(f"No board matches port/index {args.port!r}. Connected boards:\n"
                     + _board_table(everything))
    elif args.index is not None:
        boards = everything[args.index:args.index + 1]
        if not boards:
            sys.exit(f"No board at index {args.index}. Connected boards:\n"
                     + _board_table(everything))
    elif args.all:
        if not allow_all:
            sys.exit(f"--all is not supported for '{args.command}': it acts on one board.")
    elif args.command == "status":
        pass  # read-only: report every board rather than refuse
    elif len(boards) > 1:
        sys.exit(f"{len(boards)} boards connected - choose one with a port id or index "
                 "(or --all):\n" + _board_table(boards)
                 + "\n  e.g. usb-ctr-linux.py 0 on   |   usb-ctr-linux.py "
                 f"{boards[0].name} on   |   usb-ctr-linux.py --all off")
    return boards


def find_board(args) -> Board:
    return args.selected


# --------------------------------------------------------------------------
# gpiochip line request (libgpiod equivalent)
# --------------------------------------------------------------------------


class Lines:
    """Every line of one board requested in one go: inputs as inputs, outputs
    driven to `initial`. Output flags follow the NVM image: open-drain except
    the profile's push-pull pins."""

    def __init__(self, chip_path: str, profile: BoardProfile,
                 initial: dict[int, int], verbose=False):
        self.profile = profile
        self.chip_fd = os.open(chip_path, os.O_RDWR)
        info = bytearray(68)
        fcntl.ioctl(self.chip_fd, GPIO_CHIPINFO, info)
        self.label = bytes(info[32:64]).rstrip(b"\0").decode()
        self.lines = struct.unpack_from("I", info, 64)[0]
        if self.label != "cp210x" or self.lines != profile.gpio_count:
            sys.exit(f"{chip_path}: label {self.label!r} with {self.lines} lines, "
                     f"expected 'cp210x' with {profile.gpio_count} "
                     f"(use --board if this is a different generation)")

        pp_mask = sum(1 << p for p in profile.pushpull_pins)
        od_mask = sum(1 << p for p in profile.outputs) & ~pp_mask
        out_mask = od_mask | pp_mask
        values = sum((initial[p] & 1) << p for p in profile.outputs)

        # gpio_v2_line_request: offsets[64], consumer[32], config, ...
        req = bytearray(592)
        for i in range(profile.gpio_count):
            struct.pack_into("I", req, 4 * i, i)
        req[256:256 + 12] = b"hil-usb-ctrl"
        cfg = 288
        struct.pack_into("Q", req, cfg, FLAG_INPUT)       # default for every line
        attrs = []
        if od_mask:
            attrs.append((ATTR_FLAGS, FLAG_OUTPUT | FLAG_OPEN_DRAIN, od_mask))
        if pp_mask:
            attrs.append((ATTR_FLAGS, FLAG_OUTPUT, pp_mask))
        attrs.append((ATTR_OUTPUT_VALUES, values, out_mask))
        struct.pack_into("I", req, cfg + 8, len(attrs))
        for i, (attr_id, payload, mask) in enumerate(attrs):
            struct.pack_into("IIQQ", req, cfg + 32 + 24 * i, attr_id, 0, payload, mask)
        struct.pack_into("I", req, 560, profile.gpio_count)   # num_lines
        fcntl.ioctl(self.chip_fd, GPIO_V2_GET_LINE, req)
        self.fd = struct.unpack_from("i", req, 588)[0]
        if verbose:
            print(f"  [gpio] requested {chip_path} outputs=0x{values:02X}",
                  file=sys.stderr)

    def get(self) -> int:
        mask = (1 << self.profile.gpio_count) - 1
        buf = bytearray(struct.pack("QQ", 0, mask))
        fcntl.ioctl(self.fd, GPIO_V2_GET_VALUES, buf)
        return struct.unpack("QQ", buf)[0]

    def snapshot(self) -> dict:
        v = self.get()
        return {n: (v >> p) & 1 for p, n in self.profile.pin_names.items()}

    def set(self, pin: int, value: int) -> None:
        if pin not in self.profile.outputs:
            raise ValueError(f"GPIO.{pin} is not an output on this board")
        fcntl.ioctl(self.fd, GPIO_V2_SET_VALUES,
                    bytearray(struct.pack("QQ", (value & 1) << pin, 1 << pin)))

    def close(self) -> None:
        os.close(self.fd)
        os.close(self.chip_fd)


def open_lines(board: Board, args, safe: bool) -> Lines:
    """Request the lines. Outputs start at the safe state, or - when `safe` is
    False - at whatever the latch holds now, so an invocation never disturbs a
    connection that is already up."""
    profile = board.profile
    if not board.gpiochip:
        sys.exit("No gpiochip for this board - is the cp210x driver loaded?\n"
                 "  (CONFIG_GPIO_CP210X support is part of cp210x since kernel 5.3; "
                 "check `modprobe cp210x` and the gpiochip under "
                 f"{board.path}/{board.name}:1.0/)")
    if safe:
        initial = dict(profile.safe_state)
    else:
        latch = board.read_latch()
        initial = {p: (latch >> p) & 1 for p in profile.outputs}
    return Lines(board.gpiochip, profile, initial, args.verbose)


# --------------------------------------------------------------------------
# DUT detection via sysfs
# --------------------------------------------------------------------------


def usb_devices() -> dict:
    """{sysfs name: 'VID:PID product'} for every USB device (not interface)."""
    out = {}
    for name in os.listdir(USB_SYSFS):
        if ":" in name or name.startswith("usb"):
            continue
        out[name] = "%s:%s %s" % (_sysfs(f"{USB_SYSFS}/{name}/idVendor"),
                                  _sysfs(f"{USB_SYSFS}/{name}/idProduct"),
                                  _sysfs(f"{USB_SYSFS}/{name}/product"))
    return out


def block_devices(names) -> list[str]:
    """Block devices hanging off the given USB devices, e.g. 'sda 1.8T SAMSUNG'."""
    found = []
    for name in names:
        real = os.path.realpath(f"{USB_SYSFS}/{name}")
        for blk in glob.glob(f"{real}/*/host*/target*/*/block/*"):
            dev = os.path.basename(blk)
            sectors = int(_sysfs(f"{blk}/size") or 0)
            model = _sysfs(f"{blk}/device/model")
            found.append(f"{dev} {sectors * 512 / 1e9:.0f} GB {model}".strip())
    return found


def wait_for(predicate, timeout: float, interval: float = 0.05) -> float | None:
    """Seconds until predicate() is true, or None on timeout."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if predicate():
            return time.monotonic() - t0
        time.sleep(interval)
    return None


def _wait_until(snap_fn, predicate, timeout: float, interval: float = 0.002) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(snap_fn()):
            return True
        time.sleep(interval)
    return predicate(snap_fn())


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_info(args) -> int:
    b = find_board(args)
    prof = b.profile
    print(f"device      : {b.product}  serial={b.serial or '(none)'}")
    print(f"sysfs / usbfs: {b.name}  {b.usbfs}")
    print(f"VID:PID     : 0x10C4:0x{b.pid:04X}")
    print(f"gpiochip    : {b.gpiochip or '(none)'}")
    print(f"board       : {prof.label}")
    part = b.part_number()
    print(f"part number : 0x{part:02X}  ({PARTNUM_NAMES.get(part, 'unknown')})")
    if part != prof.partnum:
        print(f"  !! this profile expects 0x{prof.partnum:02X} - pass --board")
    print(f"firmware    : {b.firmware()}")

    cfg = decode_config(b.read_config(), prof.qfn28)
    release = cfg["release_version"]
    want = expected_release(prof)
    print(f"release ver : 0x{release:04X}  (bcdDevice 0x{b.bcd_device:04X})"
          + ("" if release == want
             else f"   !! expected 0x{want:04X} (v{bcd_u16(want)})"))

    nvm_serial = cfg["serial"]
    if nvm_serial:
        print(f"NVM serial  : {nvm_serial!r}")
    print(f"host serial : {b.serial!r}"
          + ("   !! stale - power-cycle the board"
             if nvm_serial and b.serial != nvm_serial else ""))

    pushpull, rstlatch, altfunc = expected_image(prof)
    ok = (cfg["pushpull"] == pushpull
          and cfg["rstlatch"] == rstlatch
          and cfg["altfunc"] == altfunc
          and release == want)
    if not ok:
        image = "DOES NOT match the expected image - see `check`"
    elif prof is BOARD_V2:
        image = "matches section 6.3"
    elif nvm_serial:
        image = f"programmed {prof.key} image, serial {nvm_serial}"
    else:
        image = "factory image (unprogrammed) - program a serial before release"
    print(f"image       : {image}")
    return 0


def cmd_check(args) -> int:
    b = find_board(args)
    prof = b.profile
    cfg = decode_config(b.read_config(), prof.qfn28)
    want_release = expected_release(prof)
    want_pp, want_latch, want_alt = expected_image(prof)
    width = prof.gpio_count
    failed = []

    description = ("architecture_final.md section 6.3" if prof is BOARD_V2
                   else f"the {prof.key} legacy image")
    print(f"CP2102N configuration read-back vs {description}")

    def line(label, got, want, fmt):
        nonlocal failed
        good = got == want
        if not good:
            failed.append(label)
        print(f"  {label:28s} {fmt(got):>12s}  want {fmt(want):>10s}"
              + ("" if good else "   <-- MISMATCH"))

    line("config version", cfg["version"], 0x01, lambda v: f"0x{v:02X}")
    line("Release Version (NVM)", cfg["release_version"], want_release,
         lambda v: f"0x{v:04X}")
    line("bcdDevice (live)", b.bcd_device, cfg["release_version"],
         lambda v: f"0x{v:04X}")
    line("GPIO push-pull mask", cfg["pushpull"], want_pp,
         lambda v: f"0b{v:0{width}b}")
    line("GPIO reset latch", cfg["rstlatch"], want_latch,
         lambda v: f"0b{v:0{width}b}")
    line("GPIO.0-3 alt functions", cfg["altfunc"], want_alt,
         lambda v: f"0b{v:04b}")
    if not cfg["checksum_ok"]:
        failed.append("checksum")
    print(f"  {'AN978 checksum':28s} "
          f"{'ok' if cfg['checksum_ok'] else 'MISMATCH':>12s}")

    nvm_serial = cfg["serial"]
    stale = bool(nvm_serial) and b.serial != nvm_serial
    print(f"\n  serial (NVM)  = {nvm_serial!r}" if nvm_serial
          else "\n  serial (NVM)  = (factory, not programmed)")
    print(f"  serial (host) = {b.serial!r}"
          + ("   <-- stale: power-cycle the board" if stale else ""))
    if not nvm_serial:
        failed.append("serial not programmed")

    if prof is BOARD_V2:
        print("\n  note: GPIO.5 HIGH DRIVE is not in these bytes; confirm in "
              "Xpress Configurator.")

    ok = not failed
    print("\nRESULT: " + (f"PASS - the NVM image matches {description}." if ok
                          else f"FAIL - {len(failed)} check(s): " + "; ".join(failed)))

    if args.record:
        note = "NVM read-back (check); platform=linux"
        if stale:
            note += "; host serial descriptor stale, power-cycle pending"
        if nvm_serial and fleet_already_seen(args.record, nvm_serial):
            note += "; serial already recorded"
        fleet_append(args.record, {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "serial": nvm_serial or b.serial,
            "result": "PASS" if ok else "FAIL",
            "release": f"0x{cfg['release_version']:04X}",
            "bcd_device": f"0x{b.bcd_device:04X}",
            "config": f"0x{cfg['version']:02X}",
            "pushpull": f"0b{cfg['pushpull']:0{width}b}",
            "rstlatch": f"0b{cfg['rstlatch']:0{width}b}",
            "altfunc": f"0b{cfg['altfunc']:04b}",
            "vbus_up_ms": "",
            "dut": "",
            "failed": "; ".join(failed),
            "notes": note,
            "board": prof.key,
        })
        print(f"recorded to {args.record}")
    return 0 if ok else 1


def _print_status(prof: BoardProfile, snap: dict) -> None:
    print(f"{prof.label} - line state")
    if prof is BOARD_V2:
        print(f"  VSYS source      : "
              f"{'vbus_pwr (J2)' if prof.source(snap) == 'vbus_pwr' else 'vbus_up (J1)'}")
        print(f"  VBUS at J3       : "
              f"{'present' if prof.vbus_present(snap) else 'absent'}")
    print(f"  eFuse fault      : {'ok' if not prof.fault(snap) else 'FAULT'}")
    print(f"  CC / Rp          : "
          f"{'connected' if prof.cc_connected(snap) else 'cut'}")
    print(f"  VBUS enable      : {'on' if prof.vbus_on(snap) else 'off'}")
    print(f"  data path        : "
          f"{'closed' if prof.data_connected(snap) else 'open'}")
    if prof.ext_pin is not None:
        print(f"  J4 opto line     : "
              f"{'asserted' if prof.ext_asserted(snap) else 'released'}")
    if prof is not BOARD_V2:
        print("  note: no VBUS sense, source select or opto line on this board")


def cmd_status(args) -> int:
    b = find_board(args)
    _print_status(b.profile, b.snapshot())
    return 0


def cmd_safe(args) -> int:
    b = find_board(args)
    open_lines(b, args, safe=True).close()
    print(f"safe state applied via gpiochip on {b.profile.key}: "
          "CC cut, data open, VBUS off"
          + (", opto released" if b.profile.ext_pin is not None else ""))
    return 0


def cmd_off(args) -> int:
    b = find_board(args)
    prof = b.profile
    lines = open_lines(b, args, safe=False)
    t0 = time.monotonic()
    lines.set(prof.data_pin, prof.data_open_level)
    time.sleep(0.020)
    lines.set(prof.cc_pin, prof.cc_cut_level)
    time.sleep(0.020)
    lines.set(prof.vbus_pin, prof.vbus_off_level)
    if prof.sense_pin is None:
        time.sleep(0.020)
        detail = "VBUS switched off (not sensed on this board)"
    else:
        if not _wait_until(lines.snapshot, lambda s: not prof.vbus_present(s), 0.150):
            print("error: VBUS did not discharge within 150 ms", file=sys.stderr)
            return 1
        detail = "VBUS collapsed"
    if prof.ext_pin is not None:
        lines.set(prof.ext_pin, 1)
    print(f"DUT disconnected ({(time.monotonic() - t0) * 1000:.0f} ms); {detail}")
    return 0


def cmd_on(args) -> int:
    b = find_board(args)
    prof = b.profile
    lines = open_lines(b, args, safe=False)
    t0 = time.monotonic()
    snap = lines.snapshot()
    if getattr(args, "high_current", False):
        if prof.source_pin is None:
            print("error: --high-current cannot be checked on a legacy board: "
                  "no source select.", file=sys.stderr)
            return 1
        if prof.source(snap) == "vbus_up":
            print("error: high-current requested but U14 has selected VBUS_UP (J1).",
                  file=sys.stderr)
            return 1
    lines.set(prof.cc_pin, prof.cc_connected_level)
    time.sleep(0.010)
    lines.set(prof.vbus_pin, prof.vbus_on_level)
    if prof.sense_pin is None:
        time.sleep(0.040)
        lines.set(prof.data_pin, prof.data_connected_level)
        print(f"DUT connected ({(time.monotonic() - t0) * 1000:.0f} ms); "
              "VBUS and source not sensed on this board")
        return 0
    if not _wait_until(lines.snapshot,
                       lambda s: prof.vbus_present(s) and not prof.fault(s), 1.0):
        lines.set(prof.cc_pin, prof.cc_cut_level)
        lines.set(prof.vbus_pin, prof.vbus_off_level)
        print("error: VBUS_HIL did not come up clean within 1 s; CC and VBUS off.",
              file=sys.stderr)
        return 1
    time.sleep(0.040)
    lines.set(prof.data_pin, prof.data_connected_level)
    print(f"DUT connected ({(time.monotonic() - t0) * 1000:.0f} ms); "
          f"source={prof.source(snap)}")
    return 0


def cmd_list(args) -> int:
    boards = discover(args)
    if not boards:
        print("No CP2102x boards found.")
        return 1
    print(_board_table(boards).replace("  IDX", "IDX", 1).replace("\n  ", "\n"))
    return 0


def cmd_toggle(args) -> int:
    """Invert the board's connection state, read from the VBUS enable latch."""
    b = find_board(args)
    if b.profile.vbus_on(b.snapshot()):
        return cmd_off(args)
    return cmd_on(args)


def cmd_ext(args) -> int:
    b = find_board(args)
    prof = b.profile
    if prof.ext_pin is None:
        print("error: no J4 opto line on a legacy usb-passthrough board.",
              file=sys.stderr)
        return 1
    lines = open_lines(b, args, safe=False)
    lines.set(prof.ext_pin, 0 if args.action == "on" else 1)
    print(f"J4 opto line {'asserted (LED driven)' if args.action == 'on' else 'released'}")
    return 0


def cmd_watch(args) -> int:
    b = find_board(args)
    keys = [b.profile.pin_names[p] for p in range(b.profile.gpio_count)]
    header = f"{'time':>8}  " + "  ".join(f"{k:>12}" for k in keys)
    print(header + "\n" + "-" * len(header))
    start, last = time.monotonic(), None
    try:
        while True:
            snap = b.snapshot()
            row = tuple(snap[k] for k in keys)
            if row != last:
                print(f"{time.monotonic() - start:8.3f}  "
                      + "  ".join(f"{v:>12}" for v in row))
                last = row
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
        return 0


def cmd_verify(args) -> int:
    """Identity, NVM image, gpiochip path, then connect/disconnect with a DUT."""
    b = find_board(args)
    prof = b.profile
    checks = []

    def row(label, value, ok=None):
        print(f"   {label:34s} {value}" + ("" if ok is None else ("   ok" if ok else "   FAIL")))
        if ok is not None:
            checks.append((label, ok))

    print("1. identity")
    row("board", prof.label)
    cfg = decode_config(b.read_config(), prof.qfn28)
    nvm_serial = cfg["serial"]
    row("serial (host)", repr(b.serial) if b.serial else "(none)", bool(b.serial))
    if nvm_serial:
        row("serial (NVM)", repr(nvm_serial), nvm_serial == b.serial)
        if b.serial and b.serial != nvm_serial:
            print("      ! host descriptor is stale - power-cycle the board; "
                  "the NVM copy is the programmed unit ID")
    row("VID:PID", f"0x10C4:0x{b.pid:04X}")
    part = b.part_number()
    row("part number", f"0x{part:02X} ({PARTNUM_NAMES.get(part, '?')})",
        part == prof.partnum)
    if args.expect_serial is not None and b.serial != args.expect_serial:
        print(f"\nRESULT: FAIL - wrong unit: expected serial {args.expect_serial!r}, "
              f"this board reports {b.serial!r}.")
        if nvm_serial == args.expect_serial:
            print("        The NVM carries it, so the write landed; power-cycle "
                  "the board and retry.")
        return 1

    want_release = expected_release(prof)
    want_pp, want_latch, want_alt = expected_image(prof)
    width = prof.gpio_count
    description = ("section 6.3" if prof is BOARD_V2
                   else f"the {prof.key} legacy image")
    print(f"\n2. configuration image vs {description}")
    rel = cfg["release_version"]
    row("Release Version (NVM)", f"0x{rel:04X}", rel == want_release)
    row("Release Version (bcdDevice)", f"0x{b.bcd_device:04X}", b.bcd_device == rel)
    row("config version", f"0x{cfg['version']:02X}", cfg["version"] == 0x01)
    row("GPIO push-pull mask", f"0b{cfg['pushpull']:0{width}b}",
        cfg["pushpull"] == want_pp)
    row("GPIO reset latch", f"0b{cfg['rstlatch']:0{width}b}",
        cfg["rstlatch"] == want_latch)
    row("GPIO.0-3 alt functions", f"0b{cfg['altfunc']:04b}",
        cfg["altfunc"] == want_alt)
    row("AN978 checksum", "ok" if cfg["checksum_ok"] else "MISMATCH",
        cfg["checksum_ok"])
    row("serial programmed", repr(nvm_serial) if nvm_serial else "(none)",
        bool(nvm_serial))

    print("\n3. Linux gpiochip")
    row("gpiochip node", b.gpiochip or "(none)", bool(b.gpiochip))
    if not b.gpiochip:
        print("\nRESULT: FAIL - no gpiochip")
        return 1
    snap = b.snapshot()
    if prof.sense_pin is not None:
        row("state before enabling: VBUS",
            "absent" if not prof.vbus_present(snap) else "PRESENT",
            not prof.vbus_present(snap))
    # The power-up state is whatever the reset latch left, not a controlled
    # condition. A legacy board comes up with VBUS enabled, so FLT can read low
    # for a moment; only the check after a controlled connect is a gate.
    if prof.vbus_on(snap):
        row("eFuse fault (pre-enable)",
            "ok" if not prof.fault(snap) else "FAULT - re-checked after connect")
    else:
        row("eFuse fault", "not judged (VBUS off)")
    if prof is BOARD_V2:
        row("VSYS source", prof.source(snap))

    lines = open_lines(b, args, safe=True)    # service-start: safe state first
    time.sleep(0.100)
    gp, raw = lines.get(), b.read_latch()
    mask = (1 << prof.gpio_count) - 1
    row("gpiochip read == raw latch", f"gpio=0x{gp:02X} latch=0x{raw & mask:02X}",
        gp == raw & mask)
    after_safe = b.snapshot()
    safe_ok = all(after_safe[prof.pin_names[p]] == v
                  for p, v in prof.safe_state.items())
    row("safe state written",
        " ".join(f"{prof.pin_names[p]}={after_safe[prof.pin_names[p]]}"
                 for p in prof.outputs),
        safe_ok)

    print("\n4. function: connect")
    before = usb_devices()
    t0 = time.monotonic()
    lines.set(prof.cc_pin, prof.cc_connected_level)
    time.sleep(0.010)
    lines.set(prof.vbus_pin, prof.vbus_on_level)
    up = None
    t_up = 0.0
    if prof.sense_pin is not None:
        up = _wait_until(lines.snapshot,
                         lambda s: prof.vbus_present(s) and not prof.fault(s), 1.0)
        t_up = (time.monotonic() - t0) * 1000
        row("VBUS rises", f"{t_up:.0f} ms" if up else "TIMEOUT", up)
    else:
        row("VBUS rises", "not sensed on this board")
    t_data = None
    if up is not False:
        time.sleep(0.040)
        t_data = time.monotonic()
        lines.set(prof.data_pin, prof.data_connected_level)
    mid = b.snapshot()
    if prof is BOARD_V2:
        row("gpiochip state matches raw latch",
            f"cc={mid['cc_en_n']} vbus_en={mid['vbus_en']} data={mid['data_en_n']}",
            (mid["cc_en_n"], mid["vbus_en"], mid["data_en_n"]) == (0, 1, 0))
    else:
        row("CC / data / VBUS",
            f"{'connected' if prof.cc_connected(mid) else 'cut'} / "
            f"{'closed' if prof.data_connected(mid) else 'open'} / "
            f"{'on' if prof.vbus_on(mid) else 'off'}",
            prof.cc_connected(mid) and prof.data_connected(mid)
            and prof.vbus_on(mid))

    print("\n5. DUT enumeration at J3")

    def new_devs():
        return {k: v for k, v in usb_devices().items() if k not in before}

    # Wait for a device, then for its block device if it is storage.
    t_dev = wait_for(new_devs, args.dut_wait)
    joined = new_devs()
    row("new USB device(s) after connect",
        "; ".join(f"{k}={v}" for k, v in joined.items()) if joined
        else "(none - is the DUT attached?)", bool(joined))
    if t_data is not None and t_dev is not None:
        row("enumeration latency after data on", f"{t_dev * 1000:.0f} ms")
    blocks: list[str] = []
    if joined:
        wait_for(lambda: bool(block_devices(joined)), args.dut_wait)
        blocks = block_devices(joined)
        row("block device (storage DUT)", "; ".join(blocks) if blocks else "(none)",
            bool(blocks) if args.expect_storage else None)
    snap = b.snapshot()
    row("eFuse fault while loaded", "ok" if not prof.fault(snap) else "FAULT",
        not prof.fault(snap))
    if prof.sense_pin is not None:
        row("VBUS present while loaded",
            "yes" if prof.vbus_present(snap) else "NO", prof.vbus_present(snap))

    print("\n6. function: disconnect")
    lines.set(prof.data_pin, prof.data_open_level)
    time.sleep(0.020)
    lines.set(prof.cc_pin, prof.cc_cut_level)
    time.sleep(0.020)
    lines.set(prof.vbus_pin, prof.vbus_off_level)
    if prof.ext_pin is not None:
        lines.set(prof.ext_pin, 1)
    if prof.sense_pin is not None:
        down = _wait_until(lines.snapshot,
                           lambda s: not prof.vbus_present(s), 0.150)
        row("VBUS falls", "collapsed" if down else "TIMEOUT", down)
    else:
        time.sleep(0.050)
        row("VBUS falls", "switched off (not sensed on this board)")
    gone = wait_for(lambda: not new_devs(), args.dut_wait)
    left = new_devs()
    row("DUT removed again", "(clean)" if not left else "; ".join(left.values()),
        gone is not None)
    final = b.snapshot()
    final_ok = all(final[prof.pin_names[p]] == v for p, v in prof.safe_state.items())
    row("final state is safe",
        " ".join(f"{prof.pin_names[p]}={final[prof.pin_names[p]]}"
                 for p in prof.outputs),
        final_ok)
    lines.close()

    failed = [n for n, ok in checks if not ok]
    result = "FAIL" if failed else "PASS"
    if args.record:
        serial = nvm_serial or b.serial
        note = "platform=linux gpiochip " + (b.gpiochip or "")
        if nvm_serial and b.serial != nvm_serial:
            note += "; host serial descriptor stale"
        if fleet_already_seen(args.record, serial):
            note += "; serial already recorded"
        fleet_append(args.record, {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "serial": serial, "result": result,
            "release": f"0x{rel:04X}", "bcd_device": f"0x{b.bcd_device:04X}",
            "config": f"0x{cfg['version']:02X}",
            "pushpull": f"0b{cfg['pushpull']:0{width}b}",
            "rstlatch": f"0b{cfg['rstlatch']:0{width}b}",
            "altfunc": f"0b{cfg['altfunc']:04b}",
            "vbus_up_ms": f"{t_up:.0f}" if up else "",
            "dut": "; ".join(joined.values())[:80], "failed": "; ".join(failed),
            "notes": note + ("; block device: " + "; ".join(blocks) if blocks else ""),
            "board": prof.key})
        print(f"\nrecorded to {args.record}")

    print()
    if failed:
        print(f"RESULT: FAIL - {len(failed)} check(s): " + "; ".join(failed))
        return 1
    print("RESULT: PASS - identity, image, gpiochip control, function and DUT "
          "enumeration verified on Linux")
    return 0


BOARD_CHOICES = ("auto", "v2.0", "v1.1", "v1.0")


def _add_board_option(parser, subcommand=False):
    parser.add_argument(
        "--board", choices=BOARD_CHOICES,
        default=argparse.SUPPRESS if subcommand else "auto",
        help="board generation; 'auto' reads it from the CP2102N part number "
             "and NVM Release Version")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="usb-ctr-linux.py", description=__doc__.split("\n\n")[0],
                                epilog=__doc__.split("Commands", 1)[-1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pid", type=lambda s: int(s, 0), default=None)
    p.add_argument("--serial", default=None, help="select a unit by serial number")
    p.add_argument("--index", type=int, default=None,
                   help="select by row number in `list` (same as a bare index)")
    p.add_argument("--port", default=None, metavar="PORT|IDX",
                   help="select by USB port id (e.g. 1-9.1.1.2.1) or `list` index")
    p.add_argument("--all", action="store_true",
                   help="apply to every connected board (info, check, status, "
                        "safe, on, off, toggle, ext)")
    p.add_argument("-v", "--verbose", action="store_true")
    _add_board_option(p)
    p.set_defaults(command="status")
    sub = p.add_subparsers(dest="command", required=False)

    def sub_parser(*args, **kwargs):
        parser = sub.add_parser(*args, **kwargs)
        _add_board_option(parser, subcommand=True)
        return parser

    for name, helptext in (("info", "identity and image summary"),
                           ("check", "NVM read-back vs the board's image"),
                           ("status", "decoded state of every line"),
                           ("safe", "force the power-up safe state"),
                           ("off", "disconnect the DUT (section 6.4)")):
        parser = sub_parser(name, help=helptext)
        if name == "check":
            parser.add_argument("--record", metavar="PATH", default=None,
                                help="append a per-unit NVM read-back record "
                                     "to this fleet TSV")
            parser.add_argument("--expect-release", type=lambda s: int(s, 0),
                                default=None, metavar="0xNNNN",
                                help="expected Release Version, overriding the "
                                     "built-in default")
    v = sub_parser("verify", help="full per-unit acceptance check")
    v.add_argument("--dut-wait", type=float, default=8.0,
                   help="seconds to wait for DUT enumerate/removal (default 8)")
    v.add_argument("--expect-serial", metavar="SERIAL", default=None)
    v.add_argument("--expect-release", type=lambda s: int(s, 0),
                   default=None, metavar="0xNNNN",
                   help="expected Release Version, overriding the built-in "
                        "default")
    v.add_argument("--expect-storage", action="store_true",
                   help="fail unless the DUT exposes a block device (e.g. an SSD)")
    v.add_argument("--record", metavar="PATH", default=None,
                   help="append a record to this fleet TSV")
    sub_parser("list", help="show connected boards, their port ids and serials")
    sub_parser("toggle", help="connect if disconnected, else disconnect")
    on = sub_parser("on", help="connect the DUT (section 6.4)")
    on.add_argument("--high-current", action="store_true")
    ext = sub_parser("ext", help="drive the J4 opto-isolated line")
    ext.add_argument("action", choices=("on", "off"))
    w = sub_parser("watch", help="poll the lines and print changes")
    w.add_argument("--interval", type=float, default=0.05)
    return p


COMMANDS = {"list": cmd_list, "toggle": cmd_toggle, "info": cmd_info, "check": cmd_check, "status": cmd_status,
            "safe": cmd_safe, "on": cmd_on, "off": cmd_off, "ext": cmd_ext,
            "watch": cmd_watch, "verify": cmd_verify}
ALL_CAPABLE = {"info", "check", "status", "safe", "on", "off", "toggle", "ext"}
VALUE_OPTS = {"--pid", "--serial", "--index", "--port", "--board"}


def _preprocess(argv: list[str]) -> list[str]:
    """Accept `<port|index> <command>` and `all-on|all-off|all-toggle`, the
    syntax of the previous board's usb-pass.py, ahead of argparse."""
    argv = list(argv)
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in VALUE_OPTS:
            i += 2
        elif tok.startswith("-"):
            i += 1
        elif tok in COMMANDS:
            break
        elif tok in ("all-on", "all-off", "all-toggle", "all-safe", "all-status"):
            argv[i:i + 1] = ["--all", tok[4:]]
            break
        else:
            argv[i:i + 1] = ["--port", tok]
            break
    return argv


def main(argv=None) -> int:
    global EXPECT_RELEASE_OVERRIDE

    args = build_parser().parse_args(_preprocess(sys.argv[1:] if argv is None else argv))
    override = getattr(args, "expect_release", None)
    if override is not None:
        EXPECT_RELEASE_OVERRIDE = override
    try:
        if args.command == "list":
            return cmd_list(args)
        boards = select_boards(args, args.command in ALL_CAPABLE)
        if len(boards) == 1 and not args.all:
            args.selected = boards[0]
            return COMMANDS[args.command](args)
        rc = 0
        for b in boards:
            args.selected = b
            print(f"== {b.name}  serial {b.serial or '-'} ==")
            try:
                rc |= COMMANDS[args.command](args)
            except SystemExit as exc:
                print(exc, file=sys.stderr)
                rc |= 1
        return rc
    except PermissionError as exc:
        print(f"permission denied: {exc.filename}\n"
              "  gpiochip needs group plugdev (or an ACL); usbfs needs a udev rule.",
              file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"I/O error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
