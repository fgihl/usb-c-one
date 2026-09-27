#!/usr/bin/env python3
"""Schematic audit for the USB-C HIL Control project (llm-kicad).

Run:  python3 tools/audit_schematic.py

Checks, highest value first:

  1. symbol pin numbers vs placed footprint pad numbers
     -> the M1 / H6 defect class (silent pin-to-pad mismatch).
        Nothing in KiCad checks this: ERC's `footprint_filter` compares only
        name filters, never pin numbers. Re-run after ANY symbol or footprint
        change.
  2. schematic `Footprint` field vs the footprint actually placed on the board
     -> detects a schematic/PCB that are out of sync.
  3. `Value` text vs the JLCPCB catalogue part for each LCSC code
     -> the H5 / M11 defect class (BOM part contradicting the schematic text).
        Compares both the voltage rating and the capacitance.
  4. `Datasheet` property coverage, and flags values that cannot resolve.

Exit code is non-zero if check 1 or 2 finds anything, so it can gate a commit.
"""
import os
import re
import sqlite3
import sys
from collections import Counter

PRJ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "llm-kicad")
SHEETS = ["01_power_cc", "02_usb_hub_control", "03_dut_interface", "04_test_validation"]
DB = os.path.expanduser("~/.konnect/jlcpcb.db")


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def sexp_block(txt, start):
    """Return the balanced s-expression beginning at txt[start] (which is '(')."""
    depth, instr, i = 0, False, start
    while i < len(txt):
        ch = txt[i]
        if ch == '"' and txt[i - 1] != "\\":
            instr = not instr
        elif not instr:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return txt[start:i + 1]
        i += 1
    raise ValueError("unbalanced s-expression")


def load_schematic():
    """ref -> {sheet, lib_id, value, footprint, lcsc, datasheet, pins:set}"""
    comps = {}
    for sheet in SHEETS:
        path = os.path.join(PRJ, sheet + ".kicad_sch")
        if not os.path.exists(path):
            continue
        txt = open(path, encoding="utf-8").read()
        for m in re.finditer(r"\n\t\(symbol\n", txt):
            body = sexp_block(txt, txt.index("(", m.start()))
            props = dict(re.findall(r'\(property\s+"([^"]+)"\s+"((?:[^"\\]|\\.)*)"', body))
            ref = props.get("Reference", "").split("_")[0]
            if not ref or ref.startswith("#"):
                continue
            lib = re.search(r'\(lib_id\s+"([^"]+)"', body)
            comps[ref] = dict(
                sheet=sheet,
                lib_id=lib.group(1) if lib else "",
                value=props.get("Value", ""),
                footprint=props.get("Footprint", ""),
                lcsc=props.get("LCSC", ""),
                datasheet=props.get("Datasheet", ""),
                pins=set(re.findall(r'\(pin\s+"([^"]+)"', body)),
            )
    return comps


def load_board():
    """ref -> (footprint name, set of pad numbers) from the placed board."""
    path = os.path.join(PRJ, "llm-kicad.kicad_pcb")
    txt = open(path, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r'\n\t\(footprint\s+"([^"]+)"', txt):
        body = sexp_block(txt, txt.index("(", m.start()))
        ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', body)
        if not ref:
            continue
        out[ref.group(1)] = (m.group(1), set(re.findall(r'\(pad\s+"([^"]+)"', body)))
    return out


def load_netlist(path="/tmp/net.net"):
    """ref -> {pin: pinfunction} from a kicadsexpr netlist, if one is available."""
    if not os.path.exists(path):
        return {}
    txt = open(path, encoding="utf-8").read()
    out = {}
    for ref, pin, fn in re.findall(
            r'\(ref "([^"]+)"\)\s*\n\s*\(pin "([^"]+)"\)\s*\n\s*\(pinfunction "([^"]*)"\)', txt):
        out.setdefault(ref, {})[pin] = fn
    return out


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def check_pin_pad(comps, board):
    print("=" * 78)
    print("1. SYMBOL PIN NUMBERS vs PLACED FOOTPRINT PAD NUMBERS   (M1 / H6 class)")
    print("=" * 78)
    bad = []
    for ref in sorted(board):
        if ref not in comps:
            print(f"  !! on the board but not in the schematic: {ref}")
            continue
        pins, pads = comps[ref]["pins"], board[ref][1]
        if not pins or not pads or pins == pads:
            continue
        bad.append(ref)
        print(f"  {ref}  {board[ref][0]}")
        if pins - pads:
            print(f"        PINS WITH NO PAD: {sorted(pins - pads, key=sortkey)}")
        if pads - pins:
            print(f"        PADS WITH NO PIN: {sorted(pads - pins, key=sortkey)}")
    print(f"  -> {len(bad)} mismatching component(s) of {len(board)} placed\n")
    return bad


def check_sync(comps, board):
    print("=" * 78)
    print("2. SCHEMATIC 'Footprint' FIELD vs PLACED FOOTPRINT")
    print("=" * 78)
    bad = []
    for ref in sorted(board):
        if ref in comps and (comps[ref]["footprint"] or "") != board[ref][0]:
            bad.append(ref)
            print(f"  {ref}  schematic: {comps[ref]['footprint']}")
            print(f"       pcb      : {board[ref][0]}")
    print(f"  -> {len(bad)} mismatch(es) of {len(board)} placed\n")
    return bad


def sortkey(s):
    return (len(s), s)


def nums(text, unit):
    """All <number><unit> occurrences in a description, e.g. ('100','uF')."""
    return [float(m.group(1)) for m in
            re.finditer(r'(\d+(?:\.\d+)?)\s*' + unit + r'\b', text, re.I)]


def check_values(comps):
    print("=" * 78)
    print("3. 'Value' TEXT vs JLCPCB CATALOGUE PART                     (H5 / M11 class)")
    print("=" * 78)
    if not os.path.exists(DB):
        print(f"  catalogue not found at {DB} -- skipped\n")
        return []
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    seen, flagged = set(), []
    for ref in sorted(comps, key=lambda r: (r[0], sortkey(r))):
        lcsc = (comps[ref]["lcsc"] or "").strip()
        if not lcsc or lcsc in seen:
            continue
        seen.add(lcsc)
        row = conn.execute(
            "SELECT MFR_Part, Package, Library_Type, Description, Stock, Price"
            " FROM components WHERE LCSC=?", (lcsc,)).fetchone()
        if not row:
            print(f"  {ref:5s} {lcsc:9s} *** NOT IN CATALOGUE ***")
            continue
        mfr, pkg, lib, desc = row[0], row[1], row[2], row[3] or ""
        value = comps[ref]["value"] or ""
        volts = nums(value, "V")
        cap_v = nums(desc, "V")
        cap_f = nums(desc, "uF") + [x / 1000 for x in nums(desc, "nF")]
        val_f = nums(value, "uF") + [x / 1000 for x in nums(value, "nF")]
        notes = []
        # only compare a voltage when the value text declares one and the part is a cap/diode
        if volts and cap_v and abs(volts[0] - min(cap_v)) > 0.5:
            notes.append(f"VOLTAGE: value {volts[0]:g} V vs part {min(cap_v):g} V")
        if val_f and cap_f:
            part_f = cap_f[0]
            if part_f and abs(val_f[0] - part_f) / part_f > 0.25:
                notes.append(f"CAPACITANCE: value {val_f[0]:g} uF vs part {part_f:g} uF")
        if notes:
            flagged.append(ref)
            print(f"  {ref:5s} {lcsc:9s} {value[:15]:15s} {pkg[:16]:16s} {lib:9s} "
                  f"{mfr[:20]:20s} {desc[:34]}")
            for n in notes:
                print(f"        >>> {n}")
    print(f"  -> {len(flagged)} flagged component(s) of {len(seen)} unique LCSC codes\n")
    return flagged


def check_datasheets(comps):
    print("=" * 78)
    print("4. 'Datasheet' PROPERTY COVERAGE")
    print("=" * 78)
    have = [r for r in comps if comps[r]["datasheet"]]
    print(f"  {len(have)} of {len(comps)} components carry a Datasheet value")
    for ref in sorted(have):
        v = comps[ref]["datasheet"]
        warn = ""
        if not re.match(r"^https?://", v):
            warn = "   <<< does not resolve (no scheme / relative path)"
        elif not os.path.exists(v) and "://" not in v:
            warn = "   <<< local path missing"
        print(f"    {ref:5s} {v[:86]}{warn}")
    print()


def main():
    comps, board = load_schematic(), load_board()
    print(f"project : {PRJ}")
    print(f"parsed  : {len(comps)} schematic components, {len(board)} placed footprints\n")
    bad = check_pin_pad(comps, board) + check_sync(comps, board)
    check_values(comps)
    check_datasheets(comps)
    nl = load_netlist()
    if nl:
        print("netlist pin functions available at /tmp/net.net "
              f"({len(nl)} components) -- exported with:")
        print("  kicad-cli sch export netlist --format kicadsexpr -o /tmp/net.net "
              "llm-kicad.kicad_sch\n")
    print("resolve differences with:")
    print("  kicad-cli sch erc --severity-all --format report -o /tmp/erc.rpt llm-kicad.kicad_sch")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
