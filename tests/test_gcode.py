"""Checks for skew_gcode.py: corrected G-code, replayed through a skewed printer,
must reproduce the intended geometry. Run:  python3 tests/test_gcode.py"""
import math
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "skew_gcode.py")
THETA, SX, SY = 90.3, 0.996, 1.004
CX, CY = 90.0, 90.0

SRC = """; test
G90
M83
G28 ; home
G1 Z0.2 F600
G1 X10 Y10 F3000
G1 X60 Y10 E2.0
G1 Y60 E2.0 ; Y only
G1 X10 E2.0
G2 X40 Y60 I15 J0 E1.5 F1800
G3 X40 Y30 I0 J-15 E3.0
G91
G1 X5 Y5 E0.2
G90
G1 X100 Y150
"""


def printer(x, y):
    """Where a skewed/scaled printer physically puts commanded (x, y)."""
    t = math.radians(THETA)
    dx, dy = x - CX, y - CY
    return CX + SX * dx + SY * math.cos(t) * dy, CY + SY * math.sin(t) * dy


def replay(text):
    pos = [None, None]; absm = True; pts = []; e = 0.0
    for ln in text.splitlines():
        code = ln.split(";")[0].strip()
        if not code:
            continue
        w = dict((k, float(v)) for k, v in re.findall(r"([A-Z])([-\d.]+)", code[code.find(" "):] if " " in code else ""))
        cmd = code.split()[0]
        if cmd == "G90": absm = True
        if cmd == "G91": absm = False
        if cmd == "G28": pos = [None, None]
        if cmd in ("G0", "G1") and ("X" in w or "Y" in w):
            if absm:
                pos = [w.get("X", pos[0]), w.get("Y", pos[1])]
            else:
                pos = [pos[0] + w.get("X", 0), pos[1] + w.get("Y", 0)]
            pts.append(tuple(pos))
        if "E" in w:
            e += w["E"]
    return pts, e


def main():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "t.gcode")
        open(src, "w").write(SRC)
        subprocess.run([sys.executable, SCRIPT, src, "--xy-angle", str(THETA),
                        "--scale-x", str(SX), "--scale-y", str(SY)], check=True)
        out = open(src).read()
    pts, e_out = replay(out)
    phys = [printer(x, y) for x, y in pts]
    targets = {(10, 10), (60, 10), (60, 60), (10, 60), (40, 60), (40, 30), (45, 35), (100, 150)}
    hit = {t: min(math.hypot(p[0] - t[0], p[1] - t[1]) for p in phys) for t in targets}
    worst = max(hit.values())
    # arc points must lie on the intended circle (centre 25,60 r 15 for first arc)
    arc1 = [p for p in phys if abs(math.hypot(p[0] - 25, p[1] - 60) - 15) < 0.01]
    e_in = 2 + 2 + 2 + 1.5 + 3.0 + 0.2
    ok = worst < 1e-3 and len(arc1) >= 10 and abs(e_out - e_in) < 1e-3
    print(f"{'PASS' if ok else 'FAIL'}  gcode: worst endpoint error {worst*1000:.3f} um, "
          f"{len(arc1)} arc points on intended circle, E {e_out:.4f} vs {e_in:.4f}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
