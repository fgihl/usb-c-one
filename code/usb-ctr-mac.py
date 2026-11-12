#!/usr/bin/env python3
"""
usb-ctr-mac.py - drive the HIL USB-C Control board's CP2102N GPIOs from macOS.

The board is normally driven from Linux via libgpiod on the cp210x gpiochip.
macOS has no gpiochip, so this talks to the CP2102N's vendor requests directly
over libusb (pyusb). It implements the same signal map and the same sequencing
as architecture_final.md section 6.1 and 6.4.

    pip install pyusb

Only the output pins of the detected board are ever written. Inputs are left
alone, so a stray write can never fight a sense divider.

Three board generations answer here, identified from the CP2102N part number
and, on a QFN24, the NVM Release Version:

    v2.0   CP2102N QFN28, seven lines, Release Version 2.00
    v1.1   CP2102N QFN24, four lines, Release Version 1.01 (CC_EN inverted)
    v1.0   CP2102N QFN24, four lines, Release Version 1.00

The legacy boards have no VBUS sense, no source select and no opto line, so
on/off substitute a settle delay for the VBUS confirmation, ext is refused,
and --high-current cannot be checked. --board auto|v2.0|v1.1|v1.0 overrides the
detection.

Commands
    info      what is on the other end: part number, firmware, blank-or-programmed
    check     read the NVM config back and compare it to the board's image
    verify    full per-unit acceptance check: identity, image, function, DUT
    status    current state of every line, decoded
    safe      force the power-up safe state (CC off, data off, VBUS off, opto off)
    on        run the section 6.4 connect sequence
    off       run the section 6.4 disconnect sequence
    ext       drive the J4 opto-isolated line (V2.0 only)
    watch     poll the lines until interrupted - handy while plugging things in

Examples
    ./usb-ctr-mac.py info
    ./usb-ctr-mac.py check
    ./usb-ctr-mac.py check --record code/fleet-record.tsv
    ./usb-ctr-mac.py --board v1.0 on
    ./usb-ctr-mac.py status
    ./usb-ctr-mac.py on
    ./usb-ctr-mac.py on --high-current      # requires J2 to be the active source
    ./usb-ctr-mac.py off
    ./usb-ctr-mac.py ext on
    ./usb-ctr-mac.py watch
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import time

try:
    import usb.core
    import usb.util
    USB_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover
    usb = None
    USB_IMPORT_ERROR = exc

# --------------------------------------------------------------------------
# CP210x protocol constants
#
# These mirror drivers/usb/serial/cp210x.c. For a CP2102N (the "default" case
# in that driver, i.e. not CP2105/CP2108) the runtime GPIO latch is addressed
# with a plain vendor request:
#
#     read : bmRequestType 0xC0, bRequest 0xFF, wValue 0x00C2, wIndex iface
#     write: bmRequestType 0x40, bRequest 0xFF, wValue 0x37E1,
#            wIndex = (state << 8) | mask
#
# There is no data stage on the write. One bit per GPIO, bit i = GPIO.i.
# --------------------------------------------------------------------------

VID_SILABS = 0x10C4

# 0xEA60 is the factory-default PID. The rest are other SiLabs defaults, in
# case the programmed image picks a different one. Override with --pid.
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

# CP2102N NVM configuration array
CFG_SIZE = 678
IDX_CFG_VERSION = 2
IDX_RELEASE_VERSION = 17  # 2 bytes, LITTLE-endian: [17] = minor, [18] = major.
                          # The host reads this pair back as bcdDevice.
IDX_GPIO_MODE = 581       # 0 = open-drain, 1 = push-pull
IDX_GPIO_RSTLATCH = 587   # default output state after reset
IDX_GPIO_CONTROL = 600    # 0 = GPIO, 1 = alternate function (GPIO.0-GPIO.3)

# Release Version as programmed in the shipped V2.0 configuration image, per
# architecture_final.md section 6.3: major 0x02, minor 0x00 -> bcdDevice 0x0200
# (i.e. 2.00). It identifies the *configuration revision*, not the unit - the
# serial descriptor does that. It is only visible to the host after the device
# re-enumerates, so a freshly programmed part reports the previous value until
# it is power-cycled. Override per run with --expect-release.
EXPECT_RELEASE_VERSION = 0x0200

# The legacy usb-passthrough boards use the Release Version to name the pin
# map, because the part itself cannot tell v1.0 from v1.1: 1.00 is the v1.0
# image and 1.01 the v1.1 image. A blank part reports the factory 1.00, so a
# programmed legacy board is indistinguishable from a blank one by release
# alone - the serial descriptor is what evidences programming.
LEGACY_RELEASE_V10 = 0x0100
LEGACY_RELEASE_V11 = 0x0101

USB_TIMEOUT_MS = 2000

# USB string descriptor for the serial number inside the config array. It is a
# standard string descriptor: bLength, bDescriptorType = 3, then UTF-16LE.
IDX_SERIAL_DESC = 450

# --------------------------------------------------------------------------
# V2.0 board signal map - architecture_final.md section 6.1.
# Line index == GPIO number, which the Linux driver confirms (BIT(gpio)).
# --------------------------------------------------------------------------

GPIO_COUNT = 7

HIL_FLT_N = 0    # in,  low  = U4 reports a fault
VBUS_SNS = 1     # in,  high = VBUS_HIL present at J3
CC_EN_N = 2      # out, low  = Rp presented  (open-drain)
U14_ST = 3       # in,  low  = VBUS_PWR (J2) is the active source
DATA_EN_N = 4    # out, low  = data path closed  (open-drain)
EXT_IO0 = 5      # out, low  = opto LED on, J4 line asserted  (open-drain)
VBUS_EN = 6      # out, high = eFuse enabled  (push-pull)

OUTPUT_PINS = (CC_EN_N, DATA_EN_N, EXT_IO0, VBUS_EN)

# Power-up safe state per section 6.1: every output in its "off" position.
SAFE_STATE = {
    CC_EN_N: 1,
    DATA_EN_N: 1,
    EXT_IO0: 1,
    VBUS_EN: 0,
}

PIN_NAMES = {
    HIL_FLT_N: "hil_flt_n",
    VBUS_SNS: "vbus_hil_sns",
    CC_EN_N: "cc_en_n",
    U14_ST: "u14_st",
    DATA_EN_N: "data_en_n",
    EXT_IO0: "ext_io0",
    VBUS_EN: "vbus_en",
}

# NVM expectation for the section 6.3 image, as decoded 7-bit values
# (bit i = GPIO.i).
EXPECT_PUSHPULL = 1 << VBUS_EN                       # only GPIO.6 is push-pull

# Reset latch covers EVERY pin, not just the outputs. GPIO.0/.1/.3 are inputs,
# and an open-drain pin released (latch = 1) is exactly how the CP2102N emulates
# an input - see cp2102n_gpioconf_init() in the Linux driver, which derives the
# direction from "open-drain AND reset value 1". So every pin is latched 1
# except the push-pull VBUS enable, which must be 0 so the eFuse stays off at
# power-up. Decoded value 0x3F -> NVM byte 0x7B.
EXPECT_RSTLATCH = 0x7F & ~(1 << VBUS_EN)

EXPECT_ALTFUNC_GPIO0_3 = 0                           # no TXT/RXT/RS485/WAKEUP

# --------------------------------------------------------------------------
# Legacy "USB passthrough" boards (usb-passthrough v1.0 / v1.1).
#
# The CP2102N there is the QFN24 part, so only GPIO.0-GPIO.3 exist and only
# three of them are outputs. There is no VBUS sense, no source select and no
# opto line, so a few commands can only report "not sensed on this board".
#
# v1.1 inverted the CC enable: high now cuts the CC path, where v1.0 had high
# = connected. That is the only difference between the two revisions, and the
# NVM Release Version names it (1.00 = v1.0, 1.01 = v1.1). The revision is also
# printed on the silkscreen, so --board v1.1 / --board v1.0 overrides it.
# --------------------------------------------------------------------------

L_CC_EN = 0      # out, v1.1: high = CC cut     v1.0: high = CC connected
L_USB_SEL = 1    # out, high = data path closed (TS3USB221 port 2)
L_VBUS_EN = 2    # out, high = eFuse enabled
L_VBUS_FLT = 3   # in,  low  = U4 reports a fault

LEGACY_OUTPUT_PINS = (L_CC_EN, L_USB_SEL, L_VBUS_EN)

LEGACY_PIN_NAMES = {
    L_CC_EN: "cc_en",
    L_USB_SEL: "usb_sel",
    L_VBUS_EN: "vbus_en",
    L_VBUS_FLT: "vbus_flt",
}

# The legacy image programs every pin open-drain and latched 1, exactly as the
# factory image does. Xpress Configurator must not have changed that: a
# push-pull CC_EN or USB_SEL would fight the transceiver.
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
        # Only the QFN28 packs GPIO.4-6 into the low bits of the NVM bytes.
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
        """True/False for the VBUS sense line, or None when it does not exist."""
        if self.sense_pin is None:
            return None
        return self._bit(snap, self.sense_pin) == 1

    def source(self, snap):
        """'vbus_pwr' / 'vbus_up', or None when there is no source select."""
        if self.source_pin is None:
            return None
        return "vbus_up" if self._bit(snap, self.source_pin) else "vbus_pwr"

    def ext_asserted(self, snap):
        """True/False for the J4 opto line, or None when it does not exist."""
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
    # Disconnected: CC cut, data floating, VBUS off.
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

# --expect-release overrides the profile default for this run.
EXPECT_RELEASE_OVERRIDE = None


def expected_release(board: BoardProfile) -> int:
    if EXPECT_RELEASE_OVERRIDE is not None:
        return EXPECT_RELEASE_OVERRIDE
    if board is BOARD_V2:
        return EXPECT_RELEASE_VERSION
    return LEGACY_RELEASE_V10 if board is BOARD_V10 else LEGACY_RELEASE_V11


def expected_image(board: BoardProfile):
    """(push-pull mask, reset latch, alt-function mask) for `board`."""
    if board is BOARD_V2:
        return EXPECT_PUSHPULL, EXPECT_RSTLATCH, EXPECT_ALTFUNC_GPIO0_3
    return LEGACY_EXPECT_PUSHPULL, LEGACY_EXPECT_RSTLATCH, LEGACY_EXPECT_ALTFUNC

# --------------------------------------------------------------------------
# Device access
# --------------------------------------------------------------------------


class Cp210x:
    def __init__(self, dev, interface: int = 0, verbose: bool = False):
        self.dev = dev
        self.interface = interface
        self.verbose = verbose
        self.board: BoardProfile | None = None   # set by detect_board()

    # -- low level ---------------------------------------------------------

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"  [usb] {msg}", file=sys.stderr)

    def read_latch(self) -> int:
        """Read the 8-bit GPIO latch. Bit i is GPIO.i (0-6 used, 7 spare)."""
        data = self.dev.ctrl_transfer(
            REQTYPE_DEVICE_TO_HOST, REQ_VENDOR_SPECIFIC,
            VAL_READ_LATCH, self.interface, 1, timeout=USB_TIMEOUT_MS)
        value = int(data[0])
        self._log(f"read latch -> 0x{value:02X}")
        return value

    def write_latch(self, mask: int, state: int) -> None:
        """Set the bits in 'mask' to the corresponding bits of 'state'."""
        mask &= 0xFF
        state &= 0xFF
        w_index = ((state & mask) << 8) | mask
        self._log(f"write latch mask=0x{mask:02X} state=0x{(state & mask):02X} "
                  f"wIndex=0x{w_index:04X}")
        self.dev.ctrl_transfer(
            REQTYPE_HOST_TO_DEVICE, REQ_VENDOR_SPECIFIC,
            VAL_WRITE_LATCH, w_index, None, timeout=USB_TIMEOUT_MS)

    def write_outputs(self, values: dict[int, int]) -> None:
        """Write only the given output pins; every other bit is left alone."""
        outputs = self.board.outputs if self.board else OUTPUT_PINS
        mask = 0
        state = 0
        for pin, value in values.items():
            if pin not in outputs:
                raise ValueError(f"GPIO.{pin} is not an output on this board")
            mask |= 1 << pin
            if value:
                state |= 1 << pin
        self.write_latch(mask, state)

    def set_pin(self, pin: int, value: int) -> None:
        self.write_outputs({pin: value})

    # -- identity / configuration -----------------------------------------

    def part_number(self) -> int:
        data = self.dev.ctrl_transfer(
            REQTYPE_DEVICE_TO_HOST, REQ_VENDOR_SPECIFIC,
            VAL_GET_PARTNUM, 0, 1, timeout=USB_TIMEOUT_MS)
        return int(data[0])

    def firmware_version(self) -> str | None:
        try:
            data = self.dev.ctrl_transfer(
                REQTYPE_DEVICE_TO_HOST, REQ_VENDOR_SPECIFIC,
                VAL_GET_FW_VER_2N, 0, 3, timeout=USB_TIMEOUT_MS)
        except usb.core.USBError:
            return None
        return ".".join(str(b) for b in data)

    def read_config(self) -> bytes:
        data = self.dev.ctrl_transfer(
            REQTYPE_DEVICE_TO_HOST, REQ_VENDOR_SPECIFIC,
            VAL_READ_2NCONFIG, 0, CFG_SIZE, timeout=USB_TIMEOUT_MS)
        return bytes(data)

    def host_serial(self) -> str:
        """The serial string the host sees in the USB descriptor."""
        try:
            return usb.util.get_string(self.dev, self.dev.iSerialNumber) or ""
        except Exception:
            return ""

    # -- decoded state -----------------------------------------------------

    def snapshot(self) -> dict[str, int]:
        latch = self.read_latch()
        names = self.board.pin_names if self.board else PIN_NAMES
        return {name: (latch >> pin) & 1 for pin, name in names.items()}


def bcd_u16(value: int) -> str:
    """Decode a 16-bit BCD version, e.g. 0x0200 -> '2.00'."""
    hi = ((value >> 12) & 0xF) * 10 + ((value >> 8) & 0xF)
    lo = ((value >> 4) & 0xF) * 10 + (value & 0xF)
    return f"{hi}.{lo:02d}"


def decode_serial(buf: bytes) -> str:
    """The serial-number string descriptor stored in the config array.

    The device only re-presents the string after a power-on reset, so this is
    the programmed truth even while the host descriptor still shows the factory
    value.
    """
    if len(buf) < IDX_SERIAL_DESC + 2:
        return ""
    length = buf[IDX_SERIAL_DESC]
    desc_type = buf[IDX_SERIAL_DESC + 1]
    if desc_type != 0x03 or length < 2 or length % 2 or length > 64:
        return ""
    raw = buf[IDX_SERIAL_DESC + 2:IDX_SERIAL_DESC + length]
    try:
        return raw.decode("utf-16-le")
    except UnicodeDecodeError:
        return ""


def fletcher16(data: bytes) -> int:
    """AN978 Fletcher-16 (mod 255, init 0xFF), as the CP2102N stores it."""
    s1 = s2 = 0xFF
    for i in range(0, len(data), 20):        # the spec folds every 20 bytes
        for value in data[i:i + 20]:
            s1 += value
            s2 += s1
        s1 = (s1 & 0xFF) + (s1 >> 8)
        s2 = (s2 & 0xFF) + (s2 >> 8)
    s1 = (s1 & 0xFF) + (s1 >> 8)
    s2 = (s2 & 0xFF) + (s2 >> 8)
    return (s2 << 8) | s1


def config_checksum_ok(buf: bytes) -> bool:
    """True when the last two bytes hold the Fletcher-16 of the rest.

    The CP2102N stores it high byte first; Xpress Configurator writes it that
    way, so the byte order is part of the check.
    """
    if len(buf) < CFG_SIZE:
        return False
    stored = int.from_bytes(buf[CFG_SIZE - 2:CFG_SIZE], "big")
    return fletcher16(buf[:CFG_SIZE - 2]) == stored


def decode_config(buf: bytes, qfn28: bool = True) -> dict:
    """
    Split the 678-byte CP2102N configuration into the fields we care about.

    Release Version (offset 17, 2 bytes, LITTLE-endian: minor then major) is the
    value the host reads back as bcdDevice; the host may use it to pick a driver.
    Note the device only re-presents it after a power-on reset, so the NVM and the
    live descriptor can disagree until the board has been power-cycled. The GPIO
    fields live near the end of the block.

    The 7-bit rearrangement for the QFN28 package (GPIO.0-3 in bits 3-6,
    GPIO.4-6 in bits 0-2) follows the Linux cp210x driver, which notes the
    published document gets those bits wrong. A QFN24 has only GPIO.0-3, so
    `qfn28=False` must be passed for it: decoding a four-line part with the
    seven-bit map invents GPIO.4-6.
    """
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
    altfunc = (control >> 2) & 0x0F       # applies to GPIO.0-GPIO.3 only

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


def find_device(pid: int | None, serial: str | None, index: int):
    """Return the first matching CP2102N, or raise SystemExit with advice."""
    pids = (pid,) if pid is not None else DEFAULT_PIDS

    for candidate in pids:
        kwargs = {"idVendor": VID_SILABS, "idProduct": candidate}
        if serial:
            kwargs["serial_number"] = serial
        dev = usb.core.find(find_all=True, **kwargs) if not serial else \
            usb.core.find(**kwargs)
        if dev is None:
            continue
        if hasattr(dev, "__iter__"):
            dev = list(dev)
            if not dev:
                continue
            if index >= len(dev):
                continue
            dev = dev[index]
        return dev

    hint = (f"0x{pid:04X}" if pid else
            " or ".join(f"0x{p:04X}" for p in DEFAULT_PIDS))
    sys.exit(
        f"No CP2102x found (VID 0x{VID_SILABS:04X}, PID {hint}).\n"
        "  - is the board plugged in and does it appear in\n"
        "      ioreg -p IOUSB -w0 | grep -i cp210   (macOS)\n"
        "      lsusb -d 10c4:                        (Linux)\n"
        "  - after programming, the PID may change: pass --pid 0x????\n"
        "  - if another program holds the port, close it first."
    )


def detect_board(dev: Cp210x, override: str | None = None) -> BoardProfile:
    """Identify the attached board and install its profile on `dev`.

    The part number is the hardware fact and decides the pin map: the QFN28
    is the V2.0 board, the QFN24 is a legacy board. Driving a seven-line map
    onto a four-line part would aim writes at pins that do not exist, and the
    reverse would leave the V2.0 outputs undriven, so this is never guessed
    from the image alone.

    On a QFN24 the NVM Release Version names the revision: 1.01 is v1.1 and
    1.00 is v1.0. Both report the same part number, so --board is available
    when the silkscreen is the better authority.
    """
    if override and override != "auto":
        profile = PROFILES[override]
    else:
        partnum = dev.part_number()
        release = decode_config(
            dev.read_config(),
            partnum == PARTNUM_CP2102N_QFN28)["release_version"]

        if partnum == PARTNUM_CP2102N_QFN28:
            profile = BOARD_V2
        elif partnum == PARTNUM_CP2102N_QFN24:
            profile = BOARD_V10 if release == LEGACY_RELEASE_V10 else BOARD_V11
        else:
            # Unknown part number: trust the image contents instead.
            profile = (BOARD_V2 if release == EXPECT_RELEASE_VERSION
                       else LEGACY_DEFAULT)

    dev.board = profile
    if dev.verbose:
        print(f"  [usb] board {profile.key}: {profile.label}", file=sys.stderr)
    return profile


def open_device(args) -> Cp210x:
    dev = find_device(args.pid, args.serial, args.index)

    # A CP2102N reports interface 0 for its single port.
    try:
        interface = dev[0].bInterfaceNumber
    except Exception:
        interface = 0

    if args.verbose:
        try:
            print(f"  [usb] opened bus {dev.bus} address {dev.address} "
                  f"PID 0x{dev.idProduct:04X} interface {interface}",
                  file=sys.stderr)
        except Exception:
            pass

    ctrl = Cp210x(dev, interface, args.verbose)
    detect_board(ctrl, getattr(args, "board", None))
    return ctrl


def summarise(dev) -> str:
    try:
        prod = usb.util.get_string(dev, dev.iProduct) or "?"
    except Exception:
        prod = "?"
    try:
        ser = usb.util.get_string(dev, dev.iSerialNumber) or "(none)"
    except Exception:
        ser = "(none)"
    return f"{prod}  serial={ser}"


def usb_devices() -> dict:
    """Snapshot every USB device as {location-id: name}, via ioreg.

    Used to detect a DUT appearing when J3 VBUS comes up. Location IDs are
    stable for a given physical port, so a device that was not present before
    the connect sequence shows up as a new key.
    """
    if sys.platform.startswith("linux"):
        base = "/sys/bus/usb/devices"
        found = {}
        for name in os.listdir(base):
            if ":" in name or name.startswith("usb"):
                continue
            try:
                with open(f"{base}/{name}/product") as fh:
                    found[name] = fh.read().strip()
            except OSError:
                found[name] = name
        return found
    try:
        out = subprocess.run(["ioreg", "-p", "IOUSB", "-w0"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    return {m.group(2): m.group(1).strip()
            for m in re.finditer(r'\+-o (.+?)@([0-9A-Fa-f]{8})', out)}


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_info(args) -> int:
    dev = open_device(args)
    board = dev.board
    print(f"device      : {summarise(dev.dev)}")
    try:
        print(f"VID:PID     : 0x{dev.dev.idVendor:04X}:0x{dev.dev.idProduct:04X}")
    except Exception:
        pass
    print(f"board       : {board.label}")

    partnum = dev.part_number()
    print(f"part number : 0x{partnum:02X}  ({PARTNUM_NAMES.get(partnum, 'unknown')})")
    if partnum != board.partnum:
        print(f"  !! this profile expects 0x{board.partnum:02X} - pass --board")
    if partnum == PARTNUM_CP2102N_QFN24:
        print("  note: v1.0/v1.1 differ only in CC_EN polarity and the part "
              "cannot tell them\n        apart; pass --board v1.1 or --board "
              "v1.0 if the silkscreen disagrees")

    fw = dev.firmware_version()
    if fw:
        print(f"firmware    : {fw}")

    cfg = decode_config(dev.read_config(), board.qfn28)
    print(f"config ver  : 0x{cfg['version']:02X}")

    # Release Version: what the host sees as bcdDevice, read back from the NVM.
    release = cfg["release_version"]
    want = expected_release(board)
    print(f"release ver : 0x{release:04X}  (v{bcd_u16(release)})"
          + ("" if release == want
             else f"   !! expected 0x{want:04X} (v{bcd_u16(want)})"))

    # The serial descriptor is the per-unit identity. The NVM copy is what was
    # programmed; the host copy only changes after a power-on reset.
    nvm_serial = cfg["serial"]
    host_serial = dev.host_serial()
    if nvm_serial:
        print(f"NVM serial  : {nvm_serial!r}")
    print(f"host serial : {host_serial!r}"
          + ("   !! stale - power-cycle the board"
             if nvm_serial and host_serial != nvm_serial else ""))

    pushpull, rstlatch, altfunc = expected_image(board)
    programmed = (
        cfg["pushpull"] == pushpull
        and cfg["rstlatch"] == rstlatch
        and cfg["altfunc"] == altfunc
        and release == want
    )
    if not programmed:
        image = "DOES NOT match the expected image - see `check`"
    elif board is BOARD_V2:
        image = "matches section 6.3"
    elif nvm_serial:
        image = f"programmed {board.key} image, serial {nvm_serial}"
    else:
        image = "factory image (unprogrammed) - program a serial before release"
    print(f"image       : {image}")

    snap = dev.snapshot()
    print("\nlive lines  (1 = high)")
    for pin in range(board.gpio_count):
        direction = "out" if pin in board.outputs else "in "
        print(f"  GPIO.{pin}  {direction}  "
              f"{board.pin_names[pin]:13s} = {snap[board.pin_names[pin]]}")
    return 0


def cmd_check(args) -> int:
    """Read the NVM back and compare it to the image this board should carry."""
    dev = open_device(args)
    board = dev.board
    cfg = decode_config(dev.read_config(), board.qfn28)
    want_release = expected_release(board)
    want_pp, want_latch, want_alt = expected_image(board)
    width = board.gpio_count
    failed = []

    description = ("architecture_final.md section 6.3" if board is BOARD_V2
                   else f"the {board.key} legacy image")
    print(f"CP2102N configuration read-back vs {description}")
    print(f"  raw bytes: mode[0x{IDX_GPIO_MODE:X}]=0x{cfg['raw_mode']:02X}  "
          f"rstlatch[0x{IDX_GPIO_RSTLATCH:X}]=0x{cfg['raw_rstlatch']:02X}  "
          f"control[0x{IDX_GPIO_CONTROL:X}]=0x{cfg['raw_control']:02X}")
    if cfg["version"] != 0x01:
        failed.append("config version")
    print(f"  config version = 0x{cfg['version']:02X}"
          + ("" if cfg["version"] == 0x01 else "   <-- unsupported version"))

    # Release Version - the field the host reads as bcdDevice. It identifies the
    # configuration revision, not the unit; the serial descriptor does that.
    release = cfg["release_version"]
    major, minor = (release >> 8) & 0xFF, release & 0xFF
    try:
        bcd_device = dev.dev.bcdDevice
    except Exception:
        bcd_device = None

    release_ok = release == want_release
    if not release_ok:
        failed.append("release version")
    print(f"\n  Release Version NVM[0x{IDX_RELEASE_VERSION:X}:+2] LE = "
          f"0x{release:04X}  (major 0x{major:02X}, minor 0x{minor:02X})")
    print(f"  expected ({description}) = 0x{want_release:04X}  ->  "
          f"major 0x{(want_release >> 8) & 0xFF:02X}, "
          f"minor 0x{want_release & 0xFF:02X}"
          + ("" if release_ok else "   <-- MISMATCH"))
    if bcd_device is not None:
        same = bcd_device == release
        if not same:
            failed.append("bcdDevice")
        print(f"  host-visible bcdDevice                  = "
              f"0x{bcd_device:04X}  ->  v{bcd_u16(bcd_device)}"
              + ("" if same else "   <-- NVM and descriptor disagree"))
        if not same:
            print("        The device only re-presents bcdDevice after a power-on")
            print("        reset, so a freshly programmed part keeps reporting the")
            print("        old value. Power-cycle the board (replug it) and retry.")
    else:
        print("  host-visible bcdDevice                  = (unreadable)")

    print("\n  per-pin expected vs actual")
    print("  GPIO  want            got             "
          "pushpull(reset latch)")
    for pin in range(board.gpio_count):
        want_mode = "push-pull " if (want_pp >> pin) & 1 else "open-drain"
        want_state = (want_latch >> pin) & 1
        got_mode = "push-pull " if (cfg["pushpull"] >> pin) & 1 else "open-drain"
        got_state = (cfg["rstlatch"] >> pin) & 1

        match = (want_mode == got_mode) and (want_state == got_state)
        if not match:
            failed.append(f"GPIO.{pin} mode/latch")
        flag = "" if match else "   <-- MISMATCH"
        direction = "out" if pin in board.outputs else "in "
        print(f"  GPIO.{pin} {direction} {want_mode} latch={want_state}   "
              f"{got_mode} latch={got_state}{flag}")

    alt_ok = cfg["altfunc"] == want_alt
    if not alt_ok:
        failed.append("alternate functions")
    print(f"\n  alternate functions GPIO.0-GPIO.3 = 0b{cfg['altfunc']:04b} "
          f"(want 0b{want_alt:04b})"
          + ("" if alt_ok else "   <-- MISMATCH: TXT/RXT/RS485/WAKEUP still routed"))

    if not cfg["checksum_ok"]:
        failed.append("checksum")
        print("\n  AN978 Fletcher-16 checksum: MISMATCH - the NVM image is not "
              "coherent")
    else:
        print("\n  AN978 Fletcher-16 checksum: ok")

    # Serial descriptor: the NVM copy is the programmed truth; the host copy is
    # only refreshed by a power-on reset.
    nvm_serial = cfg["serial"]
    host_serial = dev.host_serial()
    stale = bool(nvm_serial) and host_serial != nvm_serial
    print(f"\n  serial (NVM)  = {nvm_serial!r}" if nvm_serial
          else "\n  serial (NVM)  = (factory, not programmed)")
    print(f"  serial (host) = {host_serial!r}"
          + ("   <-- stale: power-cycle the board" if stale else ""))
    if not nvm_serial:
        failed.append("serial not programmed")

    if board is BOARD_V2:
        print("\n  note: GPIO.5 HIGH DRIVE is not carried in these three bytes and "
              "cannot\n        be verified here - confirm it in Xpress Configurator "
              "by hand.")

    ok = not failed
    print()
    if ok:
        print(f"RESULT: PASS - the NVM image matches {description}.")
    else:
        print(f"RESULT: FAIL - {len(failed)} check(s): " + "; ".join(failed))
        print("        A blank CP2102N still enumerates and still answers these")
        print("        requests, so this check is the only way to tell.")

    if args.record:
        note = "NVM read-back (check)"
        if stale:
            note += "; host serial descriptor stale, power-cycle pending"
        if nvm_serial:
            repeat = fleet_already_seen(args.record, nvm_serial)
            if repeat:
                note += f"; serial also seen at {repeat['utc']}"
        fleet_append(args.record, {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "serial": nvm_serial or host_serial,
            "result": "PASS" if ok else "FAIL",
            "release": f"0x{release:04X}",
            "bcd_device": f"0x{bcd_device:04X}" if bcd_device is not None else "",
            "config": f"0x{cfg['version']:02X}",
            "pushpull": f"0b{cfg['pushpull']:0{width}b}",
            "rstlatch": f"0b{cfg['rstlatch']:0{width}b}",
            "altfunc": f"0b{cfg['altfunc']:04b}",
            "vbus_up_ms": "",
            "dut": "",
            "failed": "; ".join(failed),
            "notes": note,
            "board": board.key,
        })
        print(f"recorded to {args.record}")
    return 0 if ok else 1


def cmd_status(args) -> int:
    dev = open_device(args)
    board = dev.board
    snap = dev.snapshot()

    print(f"{board.label} - line state")
    if board is BOARD_V2:
        print(f"  VSYS source      : "
              f"{'vbus_pwr (J2)' if board.source(snap) == 'vbus_pwr' else 'vbus_up (J1)'}")
        print(f"  VBUS at J3       : "
              f"{'present' if board.vbus_present(snap) else 'absent'}")
    print(f"  eFuse fault      : {'FAULT' if board.fault(snap) else 'ok'}")
    print()
    print(f"  CC  / Rp         : "
          f"{'connected' if board.cc_connected(snap) else 'cut'}")
    print(f"  VBUS enable      : {'on' if board.vbus_on(snap) else 'off'}")
    print(f"  data path        : "
          f"{'closed' if board.data_connected(snap) else 'open'}")
    if board.ext_pin is not None:
        print(f"  J4 opto line     : "
              f"{'asserted' if board.ext_asserted(snap) else 'released'}")
    if board is not BOARD_V2:
        print("\n  note: this board has no VBUS sense, source select or opto line.")
    return 0


def cmd_safe(args) -> int:
    dev = open_device(args)
    board = dev.board
    # Only touch the outputs; inputs are left alone.
    dev.write_outputs(board.safe_state)
    print(f"safe state applied on {board.key}: CC cut, data open, VBUS off"
          + (", opto released" if board.ext_pin is not None else ""))
    return 0


def _wait_until(dev, predicate, timeout: float, interval: float = 0.002) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(dev.snapshot()):
            return True
        time.sleep(interval)
    return predicate(dev.snapshot())


def cmd_off(args) -> int:
    """Section 6.4 disconnect: data off, 20 ms, CC off, 20 ms, VBUS off, wait."""
    dev = open_device(args)
    board = dev.board
    t0 = time.monotonic()

    dev.set_pin(board.data_pin, board.data_open_level)     # open the data path
    time.sleep(0.020)
    dev.set_pin(board.cc_pin, board.cc_cut_level)         # then drop Rp
    time.sleep(0.020)
    dev.set_pin(board.vbus_pin, board.vbus_off_level)     # then remove VBUS

    if board.sense_pin is None:
        time.sleep(0.020)                                 # no line to confirm
        detail = "VBUS switched off (not sensed on this board)"
    else:
        if not _wait_until(dev, lambda s: not board.vbus_present(s),
                           timeout=0.150):
            print("error: VBUS did not discharge within 150 ms", file=sys.stderr)
            return 1
        detail = "VBUS collapsed"

    if board.ext_pin is not None:
        dev.set_pin(board.ext_pin, 1)                     # leave the opto inert
    print(f"DUT disconnected ({(time.monotonic() - t0) * 1000:.0f} ms); {detail}")
    return 0


# --------------------------------------------------------------------------
# Fleet records - one line per verified unit, tab-separated
# --------------------------------------------------------------------------

FLEET_FIELDS = ("utc", "serial", "result", "release", "bcd_device", "config",
                "pushpull", "rstlatch", "altfunc", "vbus_up_ms", "dut",
                "failed", "notes", "board")


def fleet_load(path: str) -> list:
    """Read an existing fleet record. A missing file reads as empty.

    `restval` keeps rows written before a column was added readable, so the
    record can grow without invalidating earlier results.
    """
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t", restval=""))


def fleet_append(path: str, record: dict) -> None:
    """Append one record, writing the header only when creating the file."""
    fresh = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FLEET_FIELDS, delimiter="\t",
                                extrasaction="ignore", lineterminator="\n")
        if fresh:
            writer.writeheader()
        writer.writerow(record)


def fleet_already_seen(path: str, serial: str):
    """Return the first record carrying this serial, if there is one.

    A board reporting a serial that already has a record means either the same
    unit was re-tested or two units were programmed alike. The host cannot tell
    those apart: the CP2102N exposes no per-die identity, and every unit carries
    a byte-identical configuration image, so the serial descriptor is the only
    thing distinguishing two boards to the Mac. The operator has to read the
    number printed on the board.
    """
    for rec in fleet_load(path):
        if rec.get("serial", "") == serial:
            return rec
    return None


def cmd_verify(args) -> int:
    """One-pass per-unit verification: identity, NVM image, then function.

    Intended to be run once per board - it is the acceptance check for a unit,
    and the record §6.3 asks for. Leaves the board in the safe state.
    """
    dev = open_device(args)
    board = dev.board
    checks = []

    def row(label, value, ok=None, indent=3):
        tag = "" if ok is None else ("   ok" if ok else "   FAIL")
        print(f"{' ' * indent}{label:32s} {value}{tag}")
        if ok is not None:
            checks.append((label, ok))

    # -- 1 identity --------------------------------------------------------
    print("1. identity")
    row("board", board.label)
    cfg = decode_config(dev.read_config(), board.qfn28)
    nvm_serial = cfg["serial"]
    host_serial = dev.host_serial()
    row("serial (host)", repr(host_serial) if host_serial else "(none)",
        bool(host_serial))
    if nvm_serial:
        row("serial (NVM)", repr(nvm_serial), nvm_serial == host_serial)
        if host_serial and host_serial != nvm_serial:
            print("      ! host descriptor is stale - power-cycle the board; "
                  "the NVM copy is the programmed unit ID")
    row("VID:PID", f"0x{dev.dev.idVendor:04X}:0x{dev.dev.idProduct:04X}")
    partnum = dev.part_number()
    row("part number", f"0x{partnum:02X} ({PARTNUM_NAMES.get(partnum, '?')})",
        partnum == board.partnum)

    # The units are physically interchangeable and the host cannot tell them
    # apart - the CP2102N has no per-die ID, and every unit's config image is
    # byte-identical, so the serial descriptor is the only handle. If the
    # operator says which board they connected, hold the board to it and stop
    # here rather than powering a DUT on a board they did not mean to test.
    expect = getattr(args, "expect_serial", None)
    if expect is not None and host_serial != expect:
        print()
        print(f"RESULT: FAIL - wrong unit: expected serial {expect!r}, this "
              f"board reports {host_serial!r}.")
        if nvm_serial == expect:
            print(f"        The NVM carries {expect!r}, so the write landed; the "
                  f"host descriptor")
            print("        is still the factory value. Power-cycle the board and "
                  "retry.")
        else:
            print(f"        Check the number printed on the board. If it reads "
                  f"{expect} then it")
            print(f"        has been programmed with the wrong serial, and so has "
                  f"whichever unit")
            print(f"        does report {host_serial!r} - two units sharing a "
                  f"serial cannot be")
            print(f"        addressed individually by the host.")
        return 1

    # -- 2 configuration image --------------------------------------------
    want_release = expected_release(board)
    want_pp, want_latch, want_alt = expected_image(board)
    width = board.gpio_count
    description = ("section 6.3" if board is BOARD_V2
                   else f"the {board.key} legacy image")
    print(f"\n2. configuration image vs {description}")
    release = cfg["release_version"]
    row("Release Version (NVM)",
        f"0x{release:04X}  major 0x{release >> 8:02X} minor 0x{release & 0xFF:02X}",
        release == want_release)
    try:
        bcd = dev.dev.bcdDevice
    except Exception:
        bcd = None
    row("Release Version (bcdDevice)",
        f"0x{bcd:04X}" if bcd is not None else "(unreadable)",
        bcd == release if bcd is not None else False)
    if bcd is not None and bcd != release:
        print("      ! descriptor is stale - power-cycle the board and re-run")
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

    # -- 3 state before enabling ------------------------------------------
    print("\n3. state before enabling")
    snap = dev.snapshot()
    if board.sense_pin is not None:
        row("VBUS at J3", "absent" if not board.vbus_present(snap) else "PRESENT",
            not board.vbus_present(snap))
    # The power-up state is whatever the reset latch left, not a controlled
    # condition. A legacy board comes up with VBUS enabled, so FLT can read low
    # for a moment; only the check after a controlled connect is a gate.
    if board.vbus_on(snap):
        row("eFuse fault (pre-enable)",
            "ok" if not board.fault(snap) else "FAULT - re-checked after connect")
    else:
        row("eFuse fault", "not judged (VBUS off)")
    if board is BOARD_V2:
        row("CC / data / opto",
            f"cc={snap['cc_en_n']} data={snap['data_en_n']} "
            f"ext={snap['ext_io0']}",
            (snap["cc_en_n"], snap["data_en_n"], snap["ext_io0"]) == (1, 1, 1))
        row("VSYS source", board.source(snap))
    else:
        row("CC", "connected" if board.cc_connected(snap) else "cut")
        row("data path", "closed" if board.data_connected(snap) else "open")
        row("VBUS enable", "on" if board.vbus_on(snap) else "off")

    # -- 4 function --------------------------------------------------------
    print("\n4. function: connect then disconnect")
    before = usb_devices()
    dev.write_outputs(board.safe_state)
    time.sleep(0.100)

    t0 = time.monotonic()
    dev.set_pin(board.cc_pin, board.cc_connected_level)
    time.sleep(0.010)
    dev.set_pin(board.vbus_pin, board.vbus_on_level)
    up = None
    t_up = 0.0
    if board.sense_pin is not None:
        up = _wait_until(
            dev, lambda s: board.vbus_present(s) and not board.fault(s), 1.0)
        t_up = (time.monotonic() - t0) * 1000
        row("VBUS rises", f"{t_up:.0f} ms" if up else "TIMEOUT", up)
    else:
        row("VBUS rises", "not sensed on this board")
    if up is not False:
        time.sleep(0.040)
        dev.set_pin(board.data_pin, board.data_connected_level)

    # -- 5 DUT -------------------------------------------------------------
    print("\n5. DUT enumeration at J3")
    time.sleep(args.dut_wait)
    after = usb_devices()
    joined = sorted(v for k, v in after.items() if k not in before)
    row("new device(s) after connect",
        "; ".join(joined) if joined else "(none - is the DUT attached and powered?)",
        bool(joined))

    snap = dev.snapshot()
    row("eFuse fault while loaded", "ok" if not board.fault(snap) else "FAULT",
        not board.fault(snap))

    # -- restore safe ------------------------------------------------------
    dev.set_pin(board.data_pin, board.data_open_level)
    time.sleep(0.020)
    dev.set_pin(board.cc_pin, board.cc_cut_level)
    time.sleep(0.020)
    dev.set_pin(board.vbus_pin, board.vbus_off_level)
    if board.ext_pin is not None:
        dev.set_pin(board.ext_pin, 1)
    if board.sense_pin is not None:
        down = _wait_until(dev, lambda s: not board.vbus_present(s), 0.150)
        row("VBUS falls", "collapsed" if down else "TIMEOUT", down)
    else:
        time.sleep(0.050)
        row("VBUS falls", "switched off (not sensed on this board)")

    # VBUS collapses in tens of ms, but the host needs longer than that to tear
    # the port down and stop reporting the device, so give it a moment before
    # deciding the DUT is gone.
    time.sleep(args.dut_wait)
    left = sorted(v for k, v in usb_devices().items() if k not in before)
    row("DUT removed again", "; ".join(left) if left else "(clean)", not left)

    print()
    failed = [name for name, ok in checks if not ok]
    result = "FAIL" if failed else "PASS"

    # -- fleet record ------------------------------------------------------
    # Section 6.3 asks for a per-unit read-back record. The duplicate check is
    # the important half: with interchangeable boards, a repeated serial is
    # easy to miss by eye and impossible to see from the host.
    if args.record:
        serial = nvm_serial or host_serial
        note = ""
        repeat = fleet_already_seen(args.record, serial) if serial else None
        if repeat:
            note = (f"serial also seen at {repeat['utc']}: same unit re-tested, "
                    f"or a duplicate serial")
            print(f"WARNING: serial {serial!r} already has a {repeat['result']} "
                  f"record from {repeat['utc']}.")
            print("         Either this is that same unit tested twice, or two "
                  "units carry the")
            print("         same serial. The host cannot tell them apart - the "
                  "CP2102N has no")
            print("         per-die ID and every unit's configuration image is "
                  "identical, so")
            print("         check the number printed on the board and "
                  "reprogram it if it repeats.\n")

        fleet_append(args.record, {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "serial": serial,
            "result": result,
            "release": f"0x{release:04X}",
            "bcd_device": f"0x{bcd:04X}" if bcd is not None else "",
            "config": f"0x{cfg['version']:02X}",
            "pushpull": f"0b{cfg['pushpull']:0{width}b}",
            "rstlatch": f"0b{cfg['rstlatch']:0{width}b}",
            "altfunc": f"0b{cfg['altfunc']:04b}",
            "vbus_up_ms": f"{t_up:.0f}" if up else "",
            "dut": "; ".join(joined)[:80],
            "failed": "; ".join(failed),
            "notes": note,
            "board": board.key,
        })
        print(f"recorded to {args.record}")

    if failed:
        print(f"RESULT: FAIL - {len(failed)} check(s): " + "; ".join(failed))
        return 1
    print("RESULT: PASS - identity, configuration image, function and DUT "
          "enumeration all verified")
    return 0


def cmd_on(args) -> int:
    """Section 6.4 connect: CC on, VBUS on, confirm, 40 ms, data on."""
    dev = open_device(args)
    board = dev.board
    t0 = time.monotonic()

    if args.high_current and board.source_pin is None:
        print("error: --high-current cannot be checked on a legacy board:\n"
              "       there is no VBUS source select to read.",
              file=sys.stderr)
        return 1

    snap = dev.snapshot()
    if args.high_current and board.source(snap) == "vbus_up":
        print("error: high-current requested but U14 has selected VBUS_UP (J1).\n"
              "       Connect and qualify J2, then retry.", file=sys.stderr)
        return 1

    dev.set_pin(board.cc_pin, board.cc_connected_level)   # present Rp first
    time.sleep(0.010)
    dev.set_pin(board.vbus_pin, board.vbus_on_level)      # then bring up VBUS

    if board.sense_pin is None:
        time.sleep(0.040)                                 # no sense to wait on
        dev.set_pin(board.data_pin, board.data_connected_level)
        print(f"DUT connected ({(time.monotonic() - t0) * 1000:.0f} ms); "
              f"VBUS and source not sensed on this board")
        return 0

    def good(s):
        return board.vbus_present(s) and not board.fault(s)

    if not _wait_until(dev, good, timeout=1.0):
        dev.write_outputs({board.cc_pin: board.cc_cut_level,
                           board.vbus_pin: board.vbus_off_level})
        print("error: VBUS_HIL did not come up clean within 1 s.\n"
              "       Check eFuse fault (GPIO.0), J2/J1 source (GPIO.3) "
              "and the J3 connection.\n"
              "       CC and VBUS have been returned to the off state.",
              file=sys.stderr)
        return 1

    time.sleep(0.040)                  # let the ramp settle
    dev.set_pin(board.data_pin, board.data_connected_level)

    print(f"DUT connected ({(time.monotonic() - t0) * 1000:.0f} ms); "
          f"source={board.source(snap)}")
    return 0


def cmd_ext(args) -> int:
    dev = open_device(args)
    board = dev.board
    if board.ext_pin is None:
        print("error: no J4 opto line on a legacy usb-passthrough board.",
              file=sys.stderr)
        return 1
    asserted = args.action == "on"
    dev.set_pin(board.ext_pin, 0 if asserted else 1)
    print(f"J4 opto line {'asserted (LED driven)' if asserted else 'released'}")
    return 0


def cmd_watch(args) -> int:
    dev = open_device(args)
    board = dev.board
    keys = [board.pin_names[p] for p in range(board.gpio_count)]
    header = f"{'time':>8}  " + "  ".join(f"{k:>12}" for k in keys)
    print(header)
    print("-" * len(header))
    start = time.monotonic()
    try:
        last = None
        while True:
            snap = dev.snapshot()
            row = tuple(snap[k] for k in keys)
            if row != last:
                print(f"{time.monotonic() - start:8.3f}  "
                      + "  ".join(f"{v:>12}" for v in row))
                last = row
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
        return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


BOARD_CHOICES = ("auto", "v2.0", "v1.1", "v1.0")


def _add_board_option(parser, subcommand=False):
    parser.add_argument(
        "--board", choices=BOARD_CHOICES,
        default=argparse.SUPPRESS if subcommand else "auto",
        help="board generation; 'auto' reads it from the CP2102N part number "
             "(QFN28 = V2.0, QFN24 = legacy v1.1/v1.0)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="usb-ctr-mac.py",
        description="Drive the HIL USB-C Control board's CP2102N GPIOs over "
                    "libusb (macOS, no gpiochip required).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Commands", 1)[-1],
    )
    p.add_argument("--pid", type=lambda s: int(s, 0), default=None,
                   help="override the USB product ID, e.g. --pid 0xEA60")
    p.add_argument("--serial", default=None,
                   help="select a specific unit by serial number")
    p.add_argument("--index", type=int, default=0,
                   help="which matching device to use if several are present")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print the USB traffic to stderr")
    _add_board_option(p)

    # A bare `usb-ctr-mac.py` shows the line state rather than an argparse
    # error - it is read-only, so it is a safe default.
    p.set_defaults(command="status")
    sub = p.add_subparsers(dest="command", required=False)

    def sub_parser(*args, **kwargs):
        parser = sub.add_parser(*args, **kwargs)
        _add_board_option(parser, subcommand=True)
        return parser

    sub_parser("info", help="identity, and whether the NVM is programmed")

    check = sub_parser("check",
                       help="read the NVM back and compare to the image "
                            "this board should carry")
    check.add_argument("--expect-release", type=lambda s: int(s, 0), default=None,
                       metavar="0xNNNN",
                       help="expected Release Version, overriding the built-in "
                            "default")
    check.add_argument("--record", metavar="PATH", default=None,
                       help="append a per-unit NVM read-back record to this TSV")

    sub_parser("status", help="decoded state of every line")
    sub_parser("safe", help="force the power-up safe state")

    verify = sub_parser("verify",
                        help="full per-unit acceptance check (identity, "
                             "image, function, DUT)")
    verify.add_argument("--dut-wait", type=float, default=3.0,
                        help="seconds to wait for the DUT to enumerate after "
                             "connect (default 3)")
    verify.add_argument("--expect-release", type=lambda s: int(s, 0), default=None,
                        metavar="0xNNNN",
                        help="expected Release Version, overriding the built-in "
                             "default")
    verify.add_argument("--record", metavar="PATH", default=None,
                        help="append a one-line acceptance record to this TSV "
                             "file and warn if that serial was already "
                             "recorded (catches a duplicated unit serial)")
    verify.add_argument("--expect-serial", metavar="SERIAL", default=None,
                        help="fail unless the attached board reports this "
                             "serial, e.g. --expect-serial 0004 when testing "
                             "unit 4")

    on = sub_parser("on", help="connect the DUT (section 6.4 sequence)")
    on.add_argument("--high-current", action="store_true",
                    help="require that J2/VBUS_PWR is the active source")

    sub_parser("off", help="disconnect the DUT (section 6.4 sequence)")

    ext = sub_parser("ext", help="drive the J4 opto-isolated line")
    ext.add_argument("action", choices=("on", "off"))

    watch = sub_parser("watch", help="poll the lines and print changes")
    watch.add_argument("--interval", type=float, default=0.05,
                       help="poll interval in seconds (default 0.05)")

    return p


COMMANDS = {
    "info": cmd_info,
    "check": cmd_check,
    "status": cmd_status,
    "safe": cmd_safe,
    "verify": cmd_verify,
    "on": cmd_on,
    "off": cmd_off,
    "ext": cmd_ext,
    "watch": cmd_watch,
}


def main(argv=None) -> int:
    global EXPECT_RELEASE_OVERRIDE

    args = build_parser().parse_args(argv)

    override = getattr(args, "expect_release", None)
    if override is not None:
        EXPECT_RELEASE_OVERRIDE = override

    if usb is None:
        print(f"pyusb is required but not importable ({USB_IMPORT_ERROR}).\n"
              "  (Linux: needs libusb-1.0 too; code/usb-ctr-linux.py has no\n"
              "  dependencies and drives the kernel gpiochip instead.)\n"
              "  Homebrew and system Python are 'externally managed' (PEP 668),\n"
              "  so a plain `pip install` will be refused. Pick one:\n"
              "      python3 -m venv ~/.venvs/usbctr\n"
              "      ~/.venvs/usbctr/bin/pip install pyusb\n"
              "      ~/.venvs/usbctr/bin/python code/usb-ctr-mac.py status\n"
              "  or, if you prefer a system-wide install:\n"
              "      python3 -m pip install --break-system-packages pyusb",
              file=sys.stderr)
        return 3

    try:
        return COMMANDS[args.command](args)
    except usb.core.USBError as exc:
        print(f"USB error: {exc}\n"
              "  - if this is a 'busy' or 'access' error, close anything that "
              "has the\n    serial port open (screen, minicom, a service).",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
