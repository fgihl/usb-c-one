#!/usr/bin/env python3
"""
usb-ctr-mac.py - drive the HIL USB-C Control board's CP2102N GPIOs from macOS.

The board is normally driven from Linux via libgpiod on the cp210x gpiochip.
macOS has no gpiochip, so this talks to the CP2102N's vendor requests directly
over libusb (pyusb). It implements the same signal map and the same sequencing
as architecture_final.md section 6.1 and 6.4.

    pip install pyusb

Only the four OUTPUT pins are ever written. GPIO.0/.1/.3 are inputs and are
left alone, so a stray write can never fight a sense divider.

Commands
    info      what is on the other end: part number, firmware, blank-or-programmed
    check     read the NVM config back and compare it to the section 6.3 image
    verify    full per-unit acceptance check: identity, image, function, DUT
    status    current state of all seven lines, decoded
    safe      force the power-up safe state (CC off, data off, VBUS off, opto off)
    on        run the section 6.4 connect sequence
    off       run the section 6.4 disconnect sequence
    ext       drive the J4 opto-isolated line
    watch     poll the lines until interrupted - handy while plugging things in

Examples
    ./usb-ctr-mac.py info
    ./usb-ctr-mac.py check
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

# Release Version as programmed in the shipped configuration image, per
# architecture_final.md section 6.3: major 0x02, minor 0x00 -> bcdDevice 0x0200
# (i.e. 2.00). This is the field the host may use to decide which driver to
# load. It identifies the *configuration revision*, not the unit - the serial
# descriptor does that. It is only visible to the host after the device
# re-enumerates, so a freshly programmed part reports the previous value until
# it is power-cycled. Override per run with --expect-release.
EXPECT_RELEASE_VERSION = 0x0200

USB_TIMEOUT_MS = 2000

# --------------------------------------------------------------------------
# Board signal map - architecture_final.md section 6.1.
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
# Device access
# --------------------------------------------------------------------------


class Cp210x:
    def __init__(self, dev, interface: int = 0, verbose: bool = False):
        self.dev = dev
        self.interface = interface
        self.verbose = verbose

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
        mask = 0
        state = 0
        for pin, value in values.items():
            if pin not in OUTPUT_PINS:
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

    # -- decoded state -----------------------------------------------------

    def snapshot(self) -> dict[str, int]:
        latch = self.read_latch()
        return {name: (latch >> pin) & 1 for pin, name in PIN_NAMES.items()}


def bcd_u16(value: int) -> str:
    """Decode a 16-bit BCD version, e.g. 0x0200 -> '2.00'."""
    hi = ((value >> 12) & 0xF) * 10 + ((value >> 8) & 0xF)
    lo = ((value >> 4) & 0xF) * 10 + (value & 0xF)
    return f"{hi}.{lo:02d}"


def decode_config(buf: bytes) -> dict:
    """
    Split the 678-byte CP2102N configuration into the fields we care about.

    Release Version (offset 17, 2 bytes, LITTLE-endian: minor then major) is the
    value the host reads back as bcdDevice; the host may use it to pick a driver.
    Note the device only re-presents it after a power-on reset, so the NVM and the
    live descriptor can disagree until the board has been power-cycled. The GPIO
    fields live near the end of the block.

    The 7-bit rearrangement for the QFN28 package (GPIO.0-3 in bits 3-6,
    GPIO.4-6 in bits 0-2) follows the Linux cp210x driver, which notes the
    published document gets those bits wrong.
    """
    if len(buf) < CFG_SIZE:
        raise ValueError(f"short config read: {len(buf)} of {CFG_SIZE} bytes")

    version = buf[IDX_CFG_VERSION]
    release = int.from_bytes(
        buf[IDX_RELEASE_VERSION:IDX_RELEASE_VERSION + 2], "little")
    mode = buf[IDX_GPIO_MODE]
    rstlatch = buf[IDX_GPIO_RSTLATCH]
    control = buf[IDX_GPIO_CONTROL]

    pushpull = ((mode >> 3) & 0x0F) | ((mode & 0x07) << 4)
    latch = ((rstlatch >> 3) & 0x0F) | ((rstlatch & 0x07) << 4)
    altfunc = (control >> 2) & 0x0F       # applies to GPIO.0-GPIO.3 only

    return {
        "version": version,
        "release_version": release,
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

    return Cp210x(dev, interface, args.verbose)


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
    print(f"device      : {summarise(dev.dev)}")
    try:
        print(f"VID:PID     : 0x{dev.dev.idVendor:04X}:0x{dev.dev.idProduct:04X}")
    except Exception:
        pass

    partnum = dev.part_number()
    print(f"part number : 0x{partnum:02X}  ({PARTNUM_NAMES.get(partnum, 'unknown')})")
    if partnum != PARTNUM_CP2102N_QFN28:
        print(f"  !! expected 0x{PARTNUM_CP2102N_QFN28:02X} "
              f"(CP2102N QFN28) - the rest of this tool assumes 7 GPIOs")

    fw = dev.firmware_version()
    if fw:
        print(f"firmware    : {fw}")

    cfg = decode_config(dev.read_config())
    print(f"config ver  : 0x{cfg['version']:02X}")

    # Release Version: what the host sees as bcdDevice, read back from the NVM.
    release = cfg["release_version"]
    print(f"release ver : 0x{release:04X}  (v{bcd_u16(release)})"
          + ("" if release == EXPECT_RELEASE_VERSION
             else f"   !! expected 0x{EXPECT_RELEASE_VERSION:04X} "
                  f"(v{bcd_u16(EXPECT_RELEASE_VERSION)})"))

    programmed = (
        cfg["pushpull"] == EXPECT_PUSHPULL
        and cfg["rstlatch"] == EXPECT_RSTLATCH
        and cfg["altfunc"] == EXPECT_ALTFUNC_GPIO0_3
        and release == EXPECT_RELEASE_VERSION
    )
    print(f"image       : {'matches section 6.3' if programmed else 'DOES NOT match section 6.3 - see `check`'}")

    snap = dev.snapshot()
    print("\nlive lines  (1 = high)")
    for pin in range(GPIO_COUNT):
        direction = "out" if pin in OUTPUT_PINS else "in "
        print(f"  GPIO.{pin}  {direction}  {PIN_NAMES[pin]:13s} = {snap[PIN_NAMES[pin]]}")
    return 0


def cmd_check(args) -> int:
    """Read the NVM back and compare it to the section 6.3 configuration image."""
    dev = open_device(args)
    cfg = decode_config(dev.read_config())

    print("CP2102N configuration read-back vs architecture_final.md section 6.3")
    print(f"  raw bytes: mode[0x{IDX_GPIO_MODE:X}]=0x{cfg['raw_mode']:02X}  "
          f"rstlatch[0x{IDX_GPIO_RSTLATCH:X}]=0x{cfg['raw_rstlatch']:02X}  "
          f"control[0x{IDX_GPIO_CONTROL:X}]=0x{cfg['raw_control']:02X}")
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

    release_ok = release == EXPECT_RELEASE_VERSION
    ok = release_ok
    print(f"\n  Release Version NVM[0x{IDX_RELEASE_VERSION:X}:+2] LE = "
          f"0x{release:04X}  (major 0x{major:02X}, minor 0x{minor:02X})")
    print(f"  expected (section 6.3)                  = "
          f"0x{EXPECT_RELEASE_VERSION:04X}  ->  "
          f"major 0x{(EXPECT_RELEASE_VERSION >> 8) & 0xFF:02X}, "
          f"minor 0x{EXPECT_RELEASE_VERSION & 0xFF:02X}"
          + ("" if release_ok else "   <-- MISMATCH"))
    if bcd_device is not None:
        same = bcd_device == release
        ok &= same
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
    for pin in range(GPIO_COUNT):
        want_pp = "push-pull " if (EXPECT_PUSHPULL >> pin) & 1 else "open-drain"
        want_latch = (EXPECT_RSTLATCH >> pin) & 1
        got_pp = "push-pull " if (cfg["pushpull"] >> pin) & 1 else "open-drain"
        got_latch = (cfg["rstlatch"] >> pin) & 1

        match = (want_pp == got_pp) and (want_latch == got_latch)
        ok &= match
        flag = "" if match else "   <-- MISMATCH"
        direction = "out" if pin in OUTPUT_PINS else "in "
        print(f"  GPIO.{pin} {direction} {want_pp} latch={want_latch}   "
              f"{got_pp} latch={got_latch}{flag}")

    alt_ok = cfg["altfunc"] == EXPECT_ALTFUNC_GPIO0_3
    ok &= alt_ok
    print(f"\n  alternate functions GPIO.0-GPIO.3 = 0b{cfg['altfunc']:04b} "
          f"(want 0b{EXPECT_ALTFUNC_GPIO0_3:04b})"
          + ("" if alt_ok else "   <-- MISMATCH: TXT/RXT/RS485/WAKEUP still routed"))

    print("\n  note: GPIO.5 HIGH DRIVE is not carried in these three bytes and "
          "cannot\n        be verified here - confirm it in Xpress Configurator "
          "by hand.")

    print()
    if ok:
        print("RESULT: PASS - the NVM image matches section 6.3.")
        return 0

    print("RESULT: FAIL - this is either a blank part or a wrong image.")
    print("        A blank CP2102N still enumerates and still answers these")
    print("        requests, so this check is the only way to tell.")
    return 1


def cmd_status(args) -> int:
    dev = open_device(args)
    snap = dev.snapshot()

    vbus = snap["vbus_hil_sns"]
    fault = not snap["hil_flt_n"]          # active low
    source = "vbus_pwr (J2)" if not snap["u14_st"] else "vbus_up (J1)"

    print("HIL USB-C Control - line state")
    print(f"  VSYS source      : {source}")
    print(f"  VBUS at J3       : {'present' if vbus else 'absent'}"
          f"   (sns={vbus})")
    print(f"  eFuse fault      : {'FAULT' if fault else 'ok'}   (flt_n={snap['hil_flt_n']})")
    print()
    print(f"  CC  / Rp         : {'enabled' if not snap['cc_en_n'] else 'disabled'}"
          f"   (cc_en_n={snap['cc_en_n']})")
    print(f"  VBUS enable      : {'on' if snap['vbus_en'] else 'off'}"
          f"   (vbus_en={snap['vbus_en']})")
    print(f"  data path        : {'closed' if not snap['data_en_n'] else 'open'}"
          f"   (data_en_n={snap['data_en_n']})")
    print(f"  J4 opto line     : {'asserted' if not snap['ext_io0'] else 'released'}"
          f"   (ext_io0={snap['ext_io0']})")
    return 0


def cmd_safe(args) -> int:
    dev = open_device(args)
    # Only touch the outputs; HIL_FLT_N/VBUS_SNS/U14_ST are inputs.
    dev.write_outputs(SAFE_STATE)
    print("safe state applied: CC off, data off, VBUS off, opto released")
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
    t0 = time.monotonic()

    dev.set_pin(DATA_EN_N, 1)          # open the data path first
    time.sleep(0.020)
    dev.set_pin(CC_EN_N, 1)            # then drop Rp
    time.sleep(0.020)
    dev.set_pin(VBUS_EN, 0)            # then remove VBUS

    if not _wait_until(dev, lambda s: not s["vbus_hil_sns"], timeout=0.150):
        print("error: VBUS did not discharge within 150 ms", file=sys.stderr)
        return 1

    dev.set_pin(EXT_IO0, 1)            # leave the opto line inert
    print(f"DUT disconnected ({(time.monotonic() - t0) * 1000:.0f} ms); "
          "VBUS collapsed")
    return 0


# --------------------------------------------------------------------------
# Fleet records - one line per verified unit, tab-separated
# --------------------------------------------------------------------------

FLEET_FIELDS = ("utc", "serial", "result", "release", "bcd_device", "config",
                "pushpull", "rstlatch", "altfunc", "vbus_up_ms", "dut",
                "failed", "notes")


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
                                extrasaction="ignore")
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
    checks = []

    def row(label, value, ok=None, indent=3):
        tag = "" if ok is None else ("   ok" if ok else "   FAIL")
        print(f"{' ' * indent}{label:32s} {value}{tag}")
        if ok is not None:
            checks.append((label, ok))

    # -- 1 identity --------------------------------------------------------
    print("1. identity")
    try:
        serial = usb.util.get_string(dev.dev, dev.dev.iSerialNumber) or ""
    except Exception:
        serial = ""
    row("serial", repr(serial) if serial else "(none)",
        bool(serial))
    row("VID:PID", f"0x{dev.dev.idVendor:04X}:0x{dev.dev.idProduct:04X}")
    partnum = dev.part_number()
    row("part number", f"0x{partnum:02X} ({PARTNUM_NAMES.get(partnum, '?')})",
        partnum == PARTNUM_CP2102N_QFN28)

    # The five units are physically interchangeable and the host cannot tell
    # them apart - the CP2102N has no per-die ID, and every unit's config image
    # is byte-identical, so the serial descriptor is the only handle. If the
    # operator says which board they connected, hold the board to it and stop
    # here rather than powering a DUT on a board they did not mean to test.
    expect = getattr(args, "expect_serial", None)
    if expect is not None and serial != expect:
        print()
        print(f"RESULT: FAIL - wrong unit: expected serial {expect!r}, this "
              f"board reports {serial!r}.")
        print(f"        Check the number printed on the board. If it reads "
              f"{expect} then it")
        print(f"        has been programmed with the wrong serial, and so has "
              f"whichever unit")
        print(f"        does report {serial!r} - two units sharing a serial "
              f"cannot be addressed")
        print(f"        individually by the host.")
        return 1

    # -- 2 configuration image --------------------------------------------
    print("\n2. configuration image vs section 6.3")
    cfg = decode_config(dev.read_config())
    release = cfg["release_version"]
    row("Release Version (NVM)",
        f"0x{release:04X}  major 0x{release >> 8:02X} minor 0x{release & 0xFF:02X}",
        release == EXPECT_RELEASE_VERSION)
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
    row("GPIO push-pull mask", f"0b{cfg['pushpull']:07b}",
        cfg["pushpull"] == EXPECT_PUSHPULL)
    row("GPIO reset latch", f"0b{cfg['rstlatch']:07b}",
        cfg["rstlatch"] == EXPECT_RSTLATCH)
    row("GPIO.0-3 alt functions", f"0b{cfg['altfunc']:04b}",
        cfg["altfunc"] == EXPECT_ALTFUNC_GPIO0_3)

    # -- 3 state before enabling ------------------------------------------
    print("\n3. state before enabling")
    snap = dev.snapshot()
    row("VBUS at J3", "absent" if not snap["vbus_hil_sns"] else "PRESENT",
        snap["vbus_hil_sns"] == 0)
    row("eFuse fault", "ok" if snap["hil_flt_n"] else "FAULT",
        snap["hil_flt_n"] == 1)
    row("CC / data / opto",
        f"cc={snap['cc_en_n']} data={snap['data_en_n']} ext={snap['ext_io0']}",
        (snap["cc_en_n"], snap["data_en_n"], snap["ext_io0"]) == (1, 1, 1))
    row("VSYS source", "vbus_pwr (J2)" if not snap["u14_st"] else "vbus_up (J1)")

    # -- 4 function --------------------------------------------------------
    print("\n4. function: connect then disconnect")
    before = usb_devices()
    dev.write_outputs(SAFE_STATE)
    time.sleep(0.100)

    t0 = time.monotonic()
    dev.set_pin(CC_EN_N, 0)
    time.sleep(0.010)
    dev.set_pin(VBUS_EN, 1)
    up = _wait_until(dev, lambda s: s["vbus_hil_sns"] == 1 and s["hil_flt_n"] == 1,
                     1.0)
    t_up = (time.monotonic() - t0) * 1000
    row("VBUS rises", f"{t_up:.0f} ms" if up else "TIMEOUT", up)
    if up:
        time.sleep(0.040)
        dev.set_pin(DATA_EN_N, 0)

    # -- 5 DUT -------------------------------------------------------------
    print("\n5. DUT enumeration at J3")
    time.sleep(args.dut_wait)
    after = usb_devices()
    joined = sorted(v for k, v in after.items() if k not in before)
    row("new device(s) after connect",
        "; ".join(joined) if joined else "(none - is the DUT attached and powered?)",
        bool(joined))

    snap = dev.snapshot()
    row("eFuse fault while loaded", "ok" if snap["hil_flt_n"] else "FAULT",
        snap["hil_flt_n"] == 1)

    # -- restore safe ------------------------------------------------------
    dev.set_pin(DATA_EN_N, 1)
    time.sleep(0.020)
    dev.set_pin(CC_EN_N, 1)
    time.sleep(0.020)
    dev.set_pin(VBUS_EN, 0)
    down = _wait_until(dev, lambda s: s["vbus_hil_sns"] == 0, 0.150)
    row("VBUS falls", "collapsed" if down else "TIMEOUT", down)

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
    # the important half: with five interchangeable boards, a repeated serial
    # is easy to miss by eye and impossible to see from the host.
    if args.record:
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
            "pushpull": f"0b{cfg['pushpull']:07b}",
            "rstlatch": f"0b{cfg['rstlatch']:07b}",
            "altfunc": f"0b{cfg['altfunc']:04b}",
            "vbus_up_ms": f"{t_up:.0f}" if up else "",
            "dut": "; ".join(joined)[:80],
            "failed": "; ".join(failed),
            "notes": note,
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
    t0 = time.monotonic()

    snap = dev.snapshot()
    if args.high_current and snap["u14_st"]:
        print("error: high-current requested but U14 has selected VBUS_UP (J1).\n"
              "       Connect and qualify J2, then retry.", file=sys.stderr)
        return 1

    dev.set_pin(CC_EN_N, 0)            # present Rp first
    time.sleep(0.010)
    dev.set_pin(VBUS_EN, 1)            # then bring up VBUS

    def good(s):
        return s["vbus_hil_sns"] == 1 and s["hil_flt_n"] == 1

    if not _wait_until(dev, good, timeout=1.0):
        dev.write_outputs({CC_EN_N: 1, VBUS_EN: 0})
        print("error: VBUS_HIL did not come up clean within 1 s.\n"
              "       Check eFuse fault (GPIO.0), J2/J1 source (GPIO.3) "
              "and the J3 connection.\n"
              "       CC and VBUS have been returned to the off state.",
              file=sys.stderr)
        return 1

    time.sleep(0.040)                  # let the ramp settle
    dev.set_pin(DATA_EN_N, 0)          # finally close the data path

    print(f"DUT connected ({(time.monotonic() - t0) * 1000:.0f} ms); "
          f"source={'vbus_pwr' if not snap['u14_st'] else 'vbus_up'}")
    return 0


def cmd_ext(args) -> int:
    dev = open_device(args)
    asserted = args.action == "on"
    dev.set_pin(EXT_IO0, 0 if asserted else 1)
    print(f"J4 opto line {'asserted (LED driven)' if asserted else 'released'}")
    return 0


def cmd_watch(args) -> int:
    dev = open_device(args)
    keys = ["hil_flt_n", "vbus_hil_sns", "u14_st", "cc_en_n",
            "vbus_en", "data_en_n", "ext_io0"]
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

    # A bare `usb-ctr-mac.py` shows the line state rather than an argparse
    # error - it is read-only, so it is a safe default.
    p.set_defaults(command="status")
    sub = p.add_subparsers(dest="command", required=False)

    sub.add_parser("info", help="identity, and whether the NVM is programmed")

    check = sub.add_parser("check",
                           help="read the NVM back and compare to section 6.3")
    check.add_argument("--expect-release", type=lambda s: int(s, 0), default=None,
                       metavar="0xNNNN",
                       help="expected Release Version, overriding the built-in "
                            "default")

    sub.add_parser("status", help="decoded state of all seven lines")
    sub.add_parser("safe", help="force the power-up safe state")

    verify = sub.add_parser("verify",
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

    on = sub.add_parser("on", help="connect the DUT (section 6.4 sequence)")
    on.add_argument("--high-current", action="store_true",
                    help="require that J2/VBUS_PWR is the active source")

    sub.add_parser("off", help="disconnect the DUT (section 6.4 sequence)")

    ext = sub.add_parser("ext", help="drive the J4 opto-isolated line")
    ext.add_argument("action", choices=("on", "off"))

    watch = sub.add_parser("watch", help="poll the lines and print changes")
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
    global EXPECT_RELEASE_VERSION

    args = build_parser().parse_args(argv)

    override = getattr(args, "expect_release", None)
    if override is not None:
        EXPECT_RELEASE_VERSION = override

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
