#!/usr/bin/env python3
"""GPIO pin-assignment study for U2 (CP2102N) on the USB-C HIL Control board.

Answers two questions, with measurements rather than opinions:

  1. Does the assignment in architecture_final.md match the board?
  2. Would shuffling the seven signals onto different GPIO pins improve routing?

It enumerates every one of the 7! = 5040 assignments and scores each on first-hop
length and on fan-out crossings. A crossing forces a via or a detour, so it is
weighted more heavily than a millimetre.

All seven QFN28 GPIO pins are electrically interchangeable: the CP2102N pin table
calls each one "Digital Input/Output. General Purpose I/O", and section 4.3.3
states "Each pin has two options for the output mode: push-pull and open-drain".
So there is no direction constraint on the permutation -- only the per-pin *mode*
in the configuration image has to follow the signal.

Geometry note (this bit is easy to get wrong): pad numbers are NOT unique within
a footprint. A USB-C receptacle has four pads numbered "SH", and an exposed pad
with thermal vias is repeated many times under one number. Keying pads by number
keeps only the last instance and yields mirrored geometry. This script keeps them
all and validates its transform against kicad-cli's own absolute pad coordinates.

Usage:
    kicad-cli pcb drc --format report -o /tmp/drc.rpt llm-kicad/llm-kicad.kicad_pcb
    python3 tools/gpio_assignment_study.py
"""
import itertools
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PCB = os.path.join(ROOT, "llm-kicad", "llm-kicad.kicad_pcb")
DRC = "/tmp/drc.rpt"

# signal -> (direction, drive) required at the CP2102N pin
REQUIRED = {
    "HIL_VBUS_EN":   ("output", "push-pull"),
    "HIL_DATA_EN_N": ("output", "open-drain"),
    "HIL_CC_EN_N":   ("output", "open-drain"),
    "HIL_FLT_N":     ("input", "-"),
    "VBUS_PWR_SNS":  ("input", "-"),
    "VBUS_HIL_SNS":  ("input", "-"),
    "VBUS_UP_SNS":   ("input", "-"),
}


# --------------------------------------------------------------------------- #
# s-expression parsing
# --------------------------------------------------------------------------- #
def sexp_block(txt, start):
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


def load_board(path):
    """ref -> {name, at:(X,Y,rot), pads:{pad:[(lx,ly), ...]}, nets:{pad:net}}"""
    txt = open(path, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r'\n\t\(footprint\s+"([^"]+)"', txt):
        body = sexp_block(txt, txt.index("(", m.start()))
        ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', body)
        at = re.search(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\)", body)
        if not ref or not at:
            continue
        pads, nets = {}, {}
        for pm in re.finditer(r'\(pad\s+"([^"]+)"', body):
            pbody = sexp_block(body, pm.start())
            pat = re.search(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\)",
                            pbody)
            if not pat:
                continue
            pads.setdefault(pm.group(1), []).append(
                (float(pat.group(1)), float(pat.group(2))))
            net = re.search(r'\(net\s+(?:\d+\s+)?"([^"]*)"', pbody)
            if net:
                nets[pm.group(1)] = net.group(1)
        out[ref.group(1)] = dict(
            name=m.group(1),
            at=(float(at.group(1)), float(at.group(2)), float(at.group(3) or 0)),
            pads=pads, nets=nets)
    return out


def abs_pad(fp, pad):
    """All absolute positions of a pad number.

    Convention validated 23/23 against `kicad-cli pcb drc` absolute pad
    coordinates:  abs = fp_pos + (lx*cos + ly*sin, -lx*sin + ly*cos)
    """
    X, Y, R = fp["at"]
    t = math.radians(R)
    c, s = math.cos(t), math.sin(t)
    return [(X + lx * c + ly * s, Y - lx * s + ly * c)
            for lx, ly in fp["pads"].get(pad, [])]


def validate_transform(board):
    """Cross-check the transform against DRC's own absolute pad coordinates."""
    if not os.path.exists(DRC):
        return None
    txt = open(DRC, encoding="utf-8").read()
    obs = re.findall(r"@\(([\d.]+) mm, ([\d.]+) mm\):\s+(?:PTH )?pad ([^ ]+) "
                     r"\[([^\]]*)\] of (\S+)", txt)
    ok = bad = 0
    for x, y, pad, _net, ref in obs:
        if ref not in board or pad not in board[ref]["pads"]:
            continue
        cand = abs_pad(board[ref], pad)
        if any(math.hypot(a - float(x), b - float(y)) < 0.01 for a, b in cand):
            ok += 1
        else:
            bad += 1
    return ok, bad


def proper_cross(p1, p2, p3, p4):
    """True only for a proper (interior) crossing of two segments."""
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = p4[0] - p3[0], p4[1] - p3[1]
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-12:
        return False
    ex, ey = p3[0] - p1[0], p3[1] - p1[1]
    t = (ex * d2y - ey * d2x) / den
    u = (ex * d1y - ey * d1x) / den
    return 0.0 < t < 1.0 and 0.0 < u < 1.0


def main():
    board = load_board(PCB)
    print(f"board: {PCB}")
    print(f"footprints: {len(board)}")

    v = validate_transform(board)
    if v:
        print(f"transform validation against {DRC}: matched {v[0]}/{v[0] + v[1]}")
    else:
        print(f"(no {DRC} -- skipping transform validation)")
    print()

    u2 = board.get("U2")
    if not u2:
        print("U2 not found")
        return 1
    gpio = [p for p in sorted(u2["pads"], key=lambda s: (len(s), s))
            if p.isdigit() and 16 <= int(p) <= 22]
    gpio = [p for p in gpio]
    signals = [u2["nets"][p] for p in gpio]
    pin_pos = {p: abs_pad(u2, p)[0] for p in gpio}

    net_pos = {}
    for ref, fp in board.items():
        if ref == "U2":
            continue
        for pad, pos in fp["pads"].items():
            net = fp["nets"].get(pad)
            if net:
                net_pos.setdefault(net, []).extend(abs_pad(fp, pad))

    def cost(mapping):
        segs, total = [], 0.0
        for p in gpio:
            s = mapping[p]
            a = pin_pos[p]
            b = min(net_pos[s], key=lambda q: math.hypot(a[0] - q[0], a[1] - q[1]))
            segs.append((a, b))
            total += math.hypot(b[0] - a[0], b[1] - a[1])
        cr = sum(1 for i in range(len(segs)) for j in range(i + 1, len(segs))
                 if proper_cross(segs[i][0], segs[i][1], segs[j][0], segs[j][1]))
        return total, cr

    cur = {p: u2["nets"][p] for p in gpio}
    results = [(cost(dict(zip(gpio, perm))), perm)
               for perm in itertools.permutations(signals)]
    best = min(results, key=lambda r: (r[0][1], r[0][0]))
    cur_cost = cost(cur)

    print(f"{'assignment':42s} {'length':>9s} {'crossings':>10s}")
    print(f"  {'as built (board)':40s} {cur_cost[0]:9.2f} {cur_cost[1]:10d}")
    print(f"  {'best of all 5040':40s} {best[0][0]:9.2f} {best[0][1]:10d}")
    print()
    delta = cur_cost[0] - best[0][0]
    print(f"potential saving from the best re-shuffle: {delta:.2f} mm "
          f"({delta / cur_cost[0] * 100:.1f}%), crossings {cur_cost[1]} -> {best[0][1]}")
    print()
    print("as-built map, with the mode each signal needs:")
    print(f"  {'pin':>4s}  {'GPIO':>7s}  {'net':14s}  mode")
    for p in gpio:
        s = u2["nets"][p]
        gpio_no = f"GPIO.{ {16:3,17:2,18:1,19:0,20:6,21:5,22:4}[int(p)] }"
        mode, drive = REQUIRED[s]
        m = f"{mode}, {drive}" if mode == "output" else mode
        print(f"  {int(p):4d}  {gpio_no:>7s}  {s:14s}  {m}")
    print()
    if delta < 1.0 and best[0][1] >= cur_cost[1]:
        print("CONCLUSION: re-shuffling offers no meaningful gain -- keep the "
              "existing wiring and fix the documentation instead.")
    else:
        print("CONCLUSION: a re-shuffle is worth considering; see the best map below.")
        for p, s in zip(gpio, best[1]):
            if u2["nets"][p] != s:
                print(f"   pin {p}: {u2['nets'][p]:14s} -> {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
