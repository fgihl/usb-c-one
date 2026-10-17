#!/usr/bin/env python3
"""
usb-ctr-linux.py - drive and verify the HIL USB-C Control board on Linux.

Writes go through the kernel's cp210x gpiochip (GPIO character device v2), i.e.
the same path libgpiod uses, so a pass here validates the Linux contract in
architecture_final.md section 6. Reads of identity and the NVM image use plain
usbfs control transfers. Standard library only: no pyusb, no libgpiod.

Signal map, safe state, NVM expectations and the fleet record format are
imported from usb-ctr-mac.py so there is a single source of truth.

Commands
    info      identity, gpiochip, NVM image summary
    check     read the NVM back and compare it to the section 6.3 image
    list      connected boards with port id, serial, gpiochip and tty
    status    current state of all seven lines (read-only; every board if
              several are connected and none is selected)
    safe      force the power-up safe state
    on        run the section 6.4 connect sequence
    off       run the section 6.4 disconnect sequence
    toggle    connect if disconnected, else disconnect
    ext       drive the J4 opto-isolated line
    watch     poll the lines until interrupted
    verify    full per-unit acceptance check, including DUT enumeration

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
import ctypes
import fcntl
import glob
import importlib.util
import os
import struct
import sys
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "usbctr_mac", Path(__file__).with_name("usb-ctr-mac.py"))
mac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mac)

USB_SYSFS = "/sys/bus/usb/devices"

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

    def __init__(self, name: str):
        self.name = name
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
        return self.ctrl(mac.REQTYPE_DEVICE_TO_HOST, mac.REQ_VENDOR_SPECIFIC,
                         mac.VAL_READ_LATCH, 0, 1)[0]

    def part_number(self) -> int:
        return self.ctrl(mac.REQTYPE_DEVICE_TO_HOST, mac.REQ_VENDOR_SPECIFIC,
                         mac.VAL_GET_PARTNUM, 0, 1)[0]

    def firmware(self) -> str:
        return ".".join(str(b) for b in self.ctrl(
            mac.REQTYPE_DEVICE_TO_HOST, mac.REQ_VENDOR_SPECIFIC,
            mac.VAL_GET_FW_VER_2N, 0, 3))

    def read_config(self) -> bytes:
        return self.ctrl(mac.REQTYPE_DEVICE_TO_HOST, mac.REQ_VENDOR_SPECIFIC,
                         mac.VAL_READ_2NCONFIG, 0, mac.CFG_SIZE)

    def snapshot(self) -> dict:
        latch = self.read_latch()
        return {n: (latch >> p) & 1 for p, n in mac.PIN_NAMES.items()}


def _port_key(name: str) -> list[int]:
    """Numeric ordering of '1-9.1.1.2' so '1-10' sorts after '1-9'."""
    return [int(n) for n in name.replace("-", ".").split(".") if n.isdigit()]


def discover(args) -> list[Board]:
    """Every matching board, ordered by USB port path (stable across replugs)."""
    pids = (args.pid,) if args.pid else mac.DEFAULT_PIDS
    found = []
    for name in sorted(os.listdir(USB_SYSFS), key=_port_key):
        if ":" in name or _sysfs(f"{USB_SYSFS}/{name}/idVendor") != "10c4":
            continue
        if int(_sysfs(f"{USB_SYSFS}/{name}/idProduct") or "0", 16) in pids:
            found.append(Board(name))
    return found


def _board_table(boards: list[Board]) -> str:
    rows = [f"  {'IDX':<4}{'PORT':<16}{'SERIAL':<10}{'GPIOCHIP':<16}TTY"]
    for i, b in enumerate(boards):
        rows.append(f"  {i:<4}{b.name:<16}{b.serial or '-':<10}"
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
    return args.board


# --------------------------------------------------------------------------
# gpiochip line request (libgpiod equivalent)
# --------------------------------------------------------------------------


class Lines:
    """All seven lines requested in one go: inputs as inputs, outputs driven
    to `initial`. Output flags follow the NVM image: open-drain except VBUS_EN."""

    def __init__(self, chip_path: str, initial: dict[int, int], verbose=False):
        self.chip_fd = os.open(chip_path, os.O_RDWR)
        info = bytearray(68)
        fcntl.ioctl(self.chip_fd, GPIO_CHIPINFO, info)
        self.label = bytes(info[32:64]).rstrip(b"\0").decode()
        self.lines = struct.unpack_from("I", info, 64)[0]
        if self.label != "cp210x" or self.lines != mac.GPIO_COUNT:
            sys.exit(f"{chip_path}: label {self.label!r} with {self.lines} lines, "
                     f"expected 'cp210x' with {mac.GPIO_COUNT}")

        od_mask = sum(1 << p for p in mac.OUTPUT_PINS if p != mac.VBUS_EN)
        pp_mask = 1 << mac.VBUS_EN
        out_mask = od_mask | pp_mask
        values = sum((initial[p] & 1) << p for p in mac.OUTPUT_PINS)

        # gpio_v2_line_request: offsets[64], consumer[32], config, ...
        req = bytearray(592)
        for i in range(mac.GPIO_COUNT):
            struct.pack_into("I", req, 4 * i, i)
        req[256:256 + 12] = b"hil-usb-ctrl"
        cfg = 288
        struct.pack_into("Q", req, cfg, FLAG_INPUT)       # default for every line
        attrs = [(ATTR_FLAGS, FLAG_OUTPUT | FLAG_OPEN_DRAIN, od_mask),
                 (ATTR_FLAGS, FLAG_OUTPUT, pp_mask),
                 (ATTR_OUTPUT_VALUES, values, out_mask)]
        struct.pack_into("I", req, cfg + 8, len(attrs))
        for i, (attr_id, payload, mask) in enumerate(attrs):
            struct.pack_into("IIQQ", req, cfg + 32 + 24 * i, attr_id, 0, payload, mask)
        struct.pack_into("I", req, 560, mac.GPIO_COUNT)   # num_lines
        fcntl.ioctl(self.chip_fd, GPIO_V2_GET_LINE, req)
        self.fd = struct.unpack_from("i", req, 588)[0]
        if verbose:
            print(f"  [gpio] requested {chip_path} outputs=0x{values:02X}",
                  file=sys.stderr)

    def get(self) -> int:
        buf = bytearray(struct.pack("QQ", 0, 0x7F))
        fcntl.ioctl(self.fd, GPIO_V2_GET_VALUES, buf)
        return struct.unpack("QQ", buf)[0]

    def snapshot(self) -> dict:
        v = self.get()
        return {n: (v >> p) & 1 for p, n in mac.PIN_NAMES.items()}

    def set(self, pin: int, value: int) -> None:
        if pin not in mac.OUTPUT_PINS:
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
    if not board.gpiochip:
        sys.exit("No gpiochip for this board - is the cp210x driver loaded?\n"
                 "  (CONFIG_GPIO_CP210X support is part of cp210x since kernel 5.3; "
                 "check `modprobe cp210x` and the gpiochip under "
                 f"{board.path}/{board.name}:1.0/)")
    if safe:
        initial = dict(mac.SAFE_STATE)
    else:
        latch = board.read_latch()
        initial = {p: (latch >> p) & 1 for p in mac.OUTPUT_PINS}
    return Lines(board.gpiochip, initial, args.verbose)


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
    print(f"device      : {b.product}  serial={b.serial or '(none)'}")
    print(f"sysfs / usbfs: {b.name}  {b.usbfs}")
    print(f"VID:PID     : 0x10C4:0x{b.pid:04X}")
    print(f"gpiochip    : {b.gpiochip or '(none)'}")
    part = b.part_number()
    print(f"part number : 0x{part:02X}  ({mac.PARTNUM_NAMES.get(part, 'unknown')})")
    print(f"firmware    : {b.firmware()}")
    cfg = mac.decode_config(b.read_config())
    print(f"release ver : 0x{cfg['release_version']:04X}  (bcdDevice 0x{b.bcd_device:04X})")
    ok = (cfg["pushpull"] == mac.EXPECT_PUSHPULL
          and cfg["rstlatch"] == mac.EXPECT_RSTLATCH
          and cfg["altfunc"] == mac.EXPECT_ALTFUNC_GPIO0_3
          and cfg["release_version"] == mac.EXPECT_RELEASE_VERSION)
    print(f"image       : {'matches section 6.3' if ok else 'DOES NOT match section 6.3 - see `check`'}")
    return 0


def cmd_check(args) -> int:
    b = find_board(args)
    cfg = mac.decode_config(b.read_config())
    ok = True
    print("CP2102N configuration read-back vs architecture_final.md section 6.3")

    def line(label, got, want, fmt):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  {label:28s} {fmt(got):>12s}  want {fmt(want):>10s}"
              + ("" if good else "   <-- MISMATCH"))

    line("config version", cfg["version"], 0x01, lambda v: f"0x{v:02X}")
    line("Release Version (NVM)", cfg["release_version"],
         mac.EXPECT_RELEASE_VERSION, lambda v: f"0x{v:04X}")
    line("bcdDevice (live)", b.bcd_device, cfg["release_version"],
         lambda v: f"0x{v:04X}")
    line("GPIO push-pull mask", cfg["pushpull"], mac.EXPECT_PUSHPULL,
         lambda v: f"0b{v:07b}")
    line("GPIO reset latch", cfg["rstlatch"], mac.EXPECT_RSTLATCH,
         lambda v: f"0b{v:07b}")
    line("GPIO.0-3 alt functions", cfg["altfunc"], mac.EXPECT_ALTFUNC_GPIO0_3,
         lambda v: f"0b{v:04b}")
    print("\n  note: GPIO.5 HIGH DRIVE is not in these bytes; confirm in Xpress "
          "Configurator.")
    print("\nRESULT: " + ("PASS - the NVM image matches section 6.3." if ok else
                          "FAIL - blank part or wrong image."))
    return 0 if ok else 1


def _print_status(snap: dict) -> None:
    print("HIL USB-C Control - line state")
    print(f"  VSYS source      : {'vbus_up (J1)' if snap['u14_st'] else 'vbus_pwr (J2)'}")
    print(f"  VBUS at J3       : {'present' if snap['vbus_hil_sns'] else 'absent'}")
    print(f"  eFuse fault      : {'ok' if snap['hil_flt_n'] else 'FAULT'}")
    print(f"  CC / Rp          : {'disabled' if snap['cc_en_n'] else 'enabled'}")
    print(f"  VBUS enable      : {'on' if snap['vbus_en'] else 'off'}")
    print(f"  data path        : {'open' if snap['data_en_n'] else 'closed'}")
    print(f"  J4 opto line     : {'released' if snap['ext_io0'] else 'asserted'}")


def cmd_status(args) -> int:
    _print_status(find_board(args).snapshot())
    return 0


def cmd_safe(args) -> int:
    b = find_board(args)
    open_lines(b, args, safe=True).close()
    print("safe state applied via gpiochip: CC off, data off, VBUS off, opto released")
    return 0


def cmd_off(args) -> int:
    b = find_board(args)
    lines = open_lines(b, args, safe=False)
    t0 = time.monotonic()
    lines.set(mac.DATA_EN_N, 1)
    time.sleep(0.020)
    lines.set(mac.CC_EN_N, 1)
    time.sleep(0.020)
    lines.set(mac.VBUS_EN, 0)
    if not _wait_until(lines.snapshot, lambda s: not s["vbus_hil_sns"], 0.150):
        print("error: VBUS did not discharge within 150 ms", file=sys.stderr)
        return 1
    lines.set(mac.EXT_IO0, 1)
    print(f"DUT disconnected ({(time.monotonic() - t0) * 1000:.0f} ms); VBUS collapsed")
    return 0


def cmd_on(args) -> int:
    b = find_board(args)
    lines = open_lines(b, args, safe=False)
    t0 = time.monotonic()
    snap = lines.snapshot()
    if getattr(args, "high_current", False) and snap["u14_st"]:
        print("error: high-current requested but U14 has selected VBUS_UP (J1).",
              file=sys.stderr)
        return 1
    lines.set(mac.CC_EN_N, 0)
    time.sleep(0.010)
    lines.set(mac.VBUS_EN, 1)
    if not _wait_until(lines.snapshot,
                       lambda s: s["vbus_hil_sns"] == 1 and s["hil_flt_n"] == 1, 1.0):
        lines.set(mac.CC_EN_N, 1)
        lines.set(mac.VBUS_EN, 0)
        print("error: VBUS_HIL did not come up clean within 1 s; CC and VBUS off.",
              file=sys.stderr)
        return 1
    time.sleep(0.040)
    lines.set(mac.DATA_EN_N, 0)
    print(f"DUT connected ({(time.monotonic() - t0) * 1000:.0f} ms); "
          f"source={'vbus_up' if snap['u14_st'] else 'vbus_pwr'}")
    return 0


def cmd_list(args) -> int:
    boards = discover(args)
    if not boards:
        print("No CP2102x boards found.")
        return 1
    print(_board_table(boards).replace("  IDX", "IDX", 1).replace("\n  ", "\n"))
    return 0


def cmd_toggle(args) -> int:
    """Invert the board's connection state, read from the VBUS_EN latch."""
    if find_board(args).snapshot()["vbus_en"]:
        return cmd_off(args)
    return cmd_on(args)


def cmd_ext(args) -> int:
    b = find_board(args)
    lines = open_lines(b, args, safe=False)
    lines.set(mac.EXT_IO0, 0 if args.action == "on" else 1)
    print(f"J4 opto line {'asserted (LED driven)' if args.action == 'on' else 'released'}")
    return 0


def cmd_watch(args) -> int:
    b = find_board(args)
    keys = ["hil_flt_n", "vbus_hil_sns", "u14_st", "cc_en_n", "vbus_en",
            "data_en_n", "ext_io0"]
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
    checks = []

    def row(label, value, ok=None):
        print(f"   {label:34s} {value}" + ("" if ok is None else ("   ok" if ok else "   FAIL")))
        if ok is not None:
            checks.append((label, ok))

    print("1. identity")
    row("serial", repr(b.serial) if b.serial else "(none)", bool(b.serial))
    row("VID:PID", f"0x10C4:0x{b.pid:04X}")
    part = b.part_number()
    row("part number", f"0x{part:02X} ({mac.PARTNUM_NAMES.get(part, '?')})",
        part == mac.PARTNUM_CP2102N_QFN28)
    if args.expect_serial is not None and b.serial != args.expect_serial:
        print(f"\nRESULT: FAIL - wrong unit: expected serial {args.expect_serial!r}, "
              f"this board reports {b.serial!r}.")
        return 1

    print("\n2. configuration image vs section 6.3")
    cfg = mac.decode_config(b.read_config())
    rel = cfg["release_version"]
    row("Release Version (NVM)", f"0x{rel:04X}", rel == mac.EXPECT_RELEASE_VERSION)
    row("Release Version (bcdDevice)", f"0x{b.bcd_device:04X}", b.bcd_device == rel)
    row("config version", f"0x{cfg['version']:02X}", cfg["version"] == 0x01)
    row("GPIO push-pull mask", f"0b{cfg['pushpull']:07b}", cfg["pushpull"] == mac.EXPECT_PUSHPULL)
    row("GPIO reset latch", f"0b{cfg['rstlatch']:07b}", cfg["rstlatch"] == mac.EXPECT_RSTLATCH)
    row("GPIO.0-3 alt functions", f"0b{cfg['altfunc']:04b}", cfg["altfunc"] == mac.EXPECT_ALTFUNC_GPIO0_3)

    print("\n3. Linux gpiochip")
    row("gpiochip node", b.gpiochip or "(none)", bool(b.gpiochip))
    if not b.gpiochip:
        print("\nRESULT: FAIL - no gpiochip")
        return 1
    snap = b.snapshot()
    row("state before enabling: VBUS", "absent" if not snap["vbus_hil_sns"] else "PRESENT",
        snap["vbus_hil_sns"] == 0)
    row("eFuse fault", "ok" if snap["hil_flt_n"] else "FAULT", snap["hil_flt_n"] == 1)
    row("VSYS source", "vbus_up (J1)" if snap["u14_st"] else "vbus_pwr (J2)")

    lines = open_lines(b, args, safe=True)    # service-start: safe state first
    time.sleep(0.100)
    gp, raw = lines.get(), b.read_latch()
    row("gpiochip read == raw latch", f"gpio=0x{gp:02X} latch=0x{raw & 0x7F:02X}",
        gp == raw & 0x7F)
    after_safe = b.snapshot()
    row("safe state written (cc/data/ext/vbus)",
        f"{after_safe['cc_en_n']}/{after_safe['data_en_n']}/{after_safe['ext_io0']}/{after_safe['vbus_en']}",
        (after_safe["cc_en_n"], after_safe["data_en_n"], after_safe["ext_io0"],
         after_safe["vbus_en"]) == (1, 1, 1, 0))

    print("\n4. function: connect")
    before = usb_devices()
    t0 = time.monotonic()
    lines.set(mac.CC_EN_N, 0)
    time.sleep(0.010)
    lines.set(mac.VBUS_EN, 1)
    up = _wait_until(lines.snapshot,
                     lambda s: s["vbus_hil_sns"] == 1 and s["hil_flt_n"] == 1, 1.0)
    t_up = (time.monotonic() - t0) * 1000
    row("VBUS rises", f"{t_up:.0f} ms" if up else "TIMEOUT", up)
    t_data = None
    if up:
        time.sleep(0.040)
        t_data = time.monotonic()
        lines.set(mac.DATA_EN_N, 0)
    mid = b.snapshot()
    row("gpiochip state matches raw latch",
        f"cc={mid['cc_en_n']} vbus_en={mid['vbus_en']} data={mid['data_en_n']}",
        (mid["cc_en_n"], mid["vbus_en"], mid["data_en_n"]) == (0, 1, 0))

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
    row("eFuse fault while loaded", "ok" if snap["hil_flt_n"] else "FAULT", snap["hil_flt_n"] == 1)
    row("VBUS present while loaded", "yes" if snap["vbus_hil_sns"] else "NO", snap["vbus_hil_sns"] == 1)

    print("\n6. function: disconnect")
    lines.set(mac.DATA_EN_N, 1)
    time.sleep(0.020)
    lines.set(mac.CC_EN_N, 1)
    time.sleep(0.020)
    lines.set(mac.VBUS_EN, 0)
    down = _wait_until(lines.snapshot, lambda s: s["vbus_hil_sns"] == 0, 0.150)
    row("VBUS falls", "collapsed" if down else "TIMEOUT", down)
    gone = wait_for(lambda: not new_devs(), args.dut_wait)
    left = new_devs()
    row("DUT removed again", "(clean)" if not left else "; ".join(left.values()), gone is not None)
    final = b.snapshot()
    row("final state is safe",
        f"cc={final['cc_en_n']} data={final['data_en_n']} ext={final['ext_io0']} vbus_en={final['vbus_en']}",
        (final["cc_en_n"], final["data_en_n"], final["ext_io0"], final["vbus_en"]) == (1, 1, 1, 0))
    lines.close()

    failed = [n for n, ok in checks if not ok]
    result = "FAIL" if failed else "PASS"
    if args.record:
        note = "platform=linux gpiochip " + (b.gpiochip or "")
        if mac.fleet_already_seen(args.record, b.serial):
            note += "; serial already recorded"
        mac.fleet_append(args.record, {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "serial": b.serial, "result": result,
            "release": f"0x{rel:04X}", "bcd_device": f"0x{b.bcd_device:04X}",
            "config": f"0x{cfg['version']:02X}",
            "pushpull": f"0b{cfg['pushpull']:07b}", "rstlatch": f"0b{cfg['rstlatch']:07b}",
            "altfunc": f"0b{cfg['altfunc']:04b}",
            "vbus_up_ms": f"{t_up:.0f}" if up else "",
            "dut": "; ".join(joined.values())[:80], "failed": "; ".join(failed),
            "notes": note})
        print(f"\nrecorded to {args.record}")

    print()
    if failed:
        print(f"RESULT: FAIL - {len(failed)} check(s): " + "; ".join(failed))
        return 1
    print("RESULT: PASS - identity, image, gpiochip control, function and DUT "
          "enumeration verified on Linux")
    return 0


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
    p.set_defaults(command="status")
    sub = p.add_subparsers(dest="command", required=False)
    for name, helptext in (("info", "identity and image summary"),
                           ("check", "NVM read-back vs section 6.3"),
                           ("status", "decoded state of all seven lines"),
                           ("safe", "force the power-up safe state"),
                           ("off", "disconnect the DUT (section 6.4)")):
        sub.add_parser(name, help=helptext)
    v = sub.add_parser("verify", help="full per-unit acceptance check")
    v.add_argument("--dut-wait", type=float, default=8.0,
                   help="seconds to wait for DUT enumerate/removal (default 8)")
    v.add_argument("--expect-serial", metavar="SERIAL", default=None)
    v.add_argument("--expect-storage", action="store_true",
                   help="fail unless the DUT exposes a block device (e.g. an SSD)")
    v.add_argument("--record", metavar="PATH", default=None,
                   help="append a record to this fleet TSV")
    sub.add_parser("list", help="show connected boards, their port ids and serials")
    sub.add_parser("toggle", help="connect if disconnected, else disconnect")
    on = sub.add_parser("on", help="connect the DUT (section 6.4)")
    on.add_argument("--high-current", action="store_true")
    ext = sub.add_parser("ext", help="drive the J4 opto-isolated line")
    ext.add_argument("action", choices=("on", "off"))
    w = sub.add_parser("watch", help="poll the lines and print changes")
    w.add_argument("--interval", type=float, default=0.05)
    return p


COMMANDS = {"list": cmd_list, "toggle": cmd_toggle, "info": cmd_info, "check": cmd_check, "status": cmd_status,
            "safe": cmd_safe, "on": cmd_on, "off": cmd_off, "ext": cmd_ext,
            "watch": cmd_watch, "verify": cmd_verify}
ALL_CAPABLE = {"info", "check", "status", "safe", "on", "off", "toggle", "ext"}
VALUE_OPTS = {"--pid", "--serial", "--index", "--port"}


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
    args = build_parser().parse_args(_preprocess(sys.argv[1:] if argv is None else argv))
    try:
        if args.command == "list":
            return cmd_list(args)
        boards = select_boards(args, args.command in ALL_CAPABLE)
        if len(boards) == 1 and not args.all:
            args.board = boards[0]
            return COMMANDS[args.command](args)
        rc = 0
        for b in boards:
            args.board = b
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
