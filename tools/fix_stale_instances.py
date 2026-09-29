#!/usr/bin/env python3
"""Remove the stray per-project instance records that block Konnect's edit tools.

THE PROBLEM (review finding M10)
--------------------------------
KiCad records, inside every placed symbol, one ``(project "<name>" (path ...))``
entry per project that uses that sheet. A sub-sheet can legitimately be shared by
several projects, so those entries are a feature, not junk -- which is why KiCad
has no "clean up" command for them.

When a ``.kicad_sch`` is opened *standalone* instead of through its project, KiCad
treats the file as its own project, named after the file, and writes an extra
entry:

    (instances
        (project "llm-kicad"          <-- correct: root uuid / sheet uuid
            (path "/359235dd-.../63913550-..." (reference "R27") (unit 1)))
        (project "01_power_cc"        <-- stray: the sheet file as its own root
            (path "/5ebe429a-..."            (reference "R27") (unit 1)))
    )

The stray entry makes Konnect's field resolver see a symbol belonging to two
projects and refuse with ``ambiguous_target``, which blocks scripted edits on
U1, U2, U5, U6, U9, U10, J1 and J3.

THE FIX
-------
Delete exactly the ``(project "<sheetfile>" ...)`` entries. That is safe here
because every affected symbol also carries its correct ``llm-kicad`` entry, with
the same reference and unit -- so no annotation is lost. This script proves that
before touching anything: if any symbol would be left with no valid record, it
refuses and changes nothing.

SAFETY
------
* Dry run by default -- nothing is written unless you pass --apply.
* Refuses if any symbol would lose its only instance record.
* Writes a timestamped ``.bak`` next to each sheet before modifying it.
* Re-reads the files afterwards and re-verifies reference designators and the
  absence of stray records.

USAGE
-----
    # ALWAYS: close KiCad first. A save from a running editor overwrites this.
    python3 tools/fix_stale_instances.py            # dry run, shows the plan
    python3 tools/fix_stale_instances.py --apply    # perform it
"""
import argparse
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRJ = os.path.join(ROOT, "llm-kicad")
PROJECT = "llm-kicad"
SHEETS = ["01_power_cc", "02_usb_hub_control", "03_dut_interface", "04_test_validation"]


def match_block(txt, start):
    """Index just past the ')' matching the '(' at txt[start]."""
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
                    return i + 1
        i += 1
    raise ValueError("unbalanced s-expression")


def plan_sheet(path, sheet):
    """Return (edits, refs_kept, problems).

    edits: list of (start, end) character spans to delete, extended backwards
    over the preceding newline and indentation so no blank line is left behind.
    """
    txt = open(path, encoding="utf-8").read()
    edits, refs, problems = [], [], []

    for m in re.finditer(r"\n\t\(symbol\n", txt):
        sym_start = txt.index("(", m.start())
        sym_end = match_block(txt, sym_start)
        block = txt[sym_start:sym_end]

        ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block)
        ref = ref.group(1) if ref else "?"

        inst = block.find("(instances")
        if inst < 0:
            continue
        inst_end = match_block(block, inst)

        good, targets = 0, []
        for pm in re.finditer(r'\(project\s+"([^"]+)"', block[inst:inst_end]):
            name = pm.group(1)
            if name == PROJECT:
                good += 1
            elif name == sheet:
                s = inst + pm.start()
                e = match_block(block, s)
                targets.append((s, e))

        if not targets:
            continue
        if good == 0:
            problems.append(f"{sheet}:{ref} has ONLY a stray record - refusing")
            continue

        refs.append(ref)
        for s, e in targets:
            # extend back over the newline and indentation preceding the record
            a = sym_start + s
            while a > sym_start and txt[a - 1] in " \t":
                a -= 1
            if a > sym_start and txt[a - 1] == "\n":
                a -= 1
            edits.append((a, sym_start + e))

    edits.sort()
    return edits, refs, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually write the files (default is a dry run)")
    args = ap.parse_args()

    if not os.path.isdir(PRJ):
        print(f"project folder not found: {PRJ}")
        return 2

    total, all_refs, all_problems, per_file = 0, [], [], {}
    for sheet in SHEETS:
        path = os.path.join(PRJ, sheet + ".kicad_sch")
        if not os.path.exists(path):
            continue
        edits, refs, problems = plan_sheet(path, sheet)
        per_file[sheet] = (path, edits, refs)
        total += len(edits)
        all_refs += refs
        all_problems += problems

    print(f"project            : {PRJ}")
    print(f"stray records found: {total}")
    for sheet, (path, edits, refs) in per_file.items():
        if edits:
            print(f"  {sheet:22s} {len(edits):2d} records on {len(refs):2d} symbols")
    print()

    if all_problems:
        print("REFUSING - these symbols would lose their only instance record:")
        for p in all_problems:
            print("   " + p)
        print("\nNothing was written. Annotate those symbols in KiCad first.")
        return 1

    if not total:
        print("Nothing to do - no stray instance records present.")
        return 0

    if not args.apply:
        print("Dry run: no files changed. Re-run with --apply to perform the edit.")
        print("\nBefore applying:\n"
              "  1. Close KiCad (a save from a running editor would overwrite this).\n"
              "  2. Make sure the project is committed, or rely on the .bak files.")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    for sheet, (path, edits, refs) in per_file.items():
        if not edits:
            continue
        shutil.copy2(path, f"{path}.{stamp}.bak")
        txt = open(path, encoding="utf-8").read()
        for a, b in sorted(edits, reverse=True):
            txt = txt[:a] + txt[b:]
        open(path, "w", encoding="utf-8").write(txt)
        print(f"  rewrote {sheet}.kicad_sch ({len(edits)} records removed), "
              f"backup .{stamp}.bak")

    print("\nverifying...")
    ok = True
    for sheet, (path, _edits, before_refs) in per_file.items():
        txt = open(path, encoding="utf-8").read()
        left = len(re.findall(r'\(project\s+"' + re.escape(sheet) + r'"', txt))
        refs_now = re.findall(r'\(property\s+"Reference"\s+"([^"]+)"', txt)
        missing = [r for r in before_refs if r not in refs_now]
        print(f"  {sheet:22s} stray records left: {left}"
              f" | references preserved: {'yes' if not missing else 'NO ' + str(missing)}")
        ok &= (left == 0 and not missing)

    print("\n" + ("OK - now reopen llm-kicad.kicad_pro and check:\n"
                  "  * reference designators unchanged\n"
                  "  * ERC unchanged (currently 6 violations: 2 power_pin_not_driven,\n"
                  "    2 pin_not_connected, 2 lib_symbol_mismatch)\n"
                  "  * python3 tools/audit_schematic.py still reports 0 / 0 / 0\n"
                  "  * Konnect can now edit U1, U2, U5, U6, U9, U10, J1 and J3"
                  if ok else "VERIFY FAILED - restore the .bak files"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
