"""Firmware skew settings: the printed commands, applied with each firmware's own
transform (copied from its source), must make a skewed printer square.
Run:  python3 tests/test_firmware.py   (standard library only)"""
import itertools
import math
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from skewcal.firmware import firmware_commands, z_direction  # noqa: E402


# ---- firmware code, transcribed -------------------------------------------------
def klipper_calc_skew_factor(ac, bd, ad):          # klippy/extras/skew_correction.py
    side = math.sqrt(2 * ac * ac + 2 * bd * bd - 4 * ad * ad) / 2.
    return math.tan(math.pi / 2 - math.acos((ac * ac - side * side - ad * ad) / (2 * side * ad)))


def klipper_marlin_skew(pos, xy, xz, yz):           # Klipper calc_skew == Marlin Planner::skew
    x, y, z = pos
    return (x - y * xy - z * (xz - xy * yz), y - z * yz, z)


def reprap_axis_transform(pos, tan_xy, tan_xz, tan_yz):   # RRF Move::AxisTransform, compensateXY
    x, y, z = pos
    return (x + tan_xy * y + tan_xz * z, y + tan_yz * z, z)


# ---- parse the command strings exactly as the firmware would -----------------------
def parse_klipper(cmd):
    f = {}
    for plane, vals in re.findall(r"(XY|XZ|YZ)=([\d.,-]+)", cmd):
        f[plane] = klipper_calc_skew_factor(*[float(v) for v in vals.split(",")])
    return f.get("XY", 0.0), f.get("XZ", 0.0), f.get("YZ", 0.0)


def parse_marlin(cmd):
    w = dict(re.findall(r"([IJK])(-?[\d.]+)", cmd))
    return float(w.get("I", 0)), float(w.get("J", 0)), float(w.get("K", 0))


def parse_reprap(cmd):
    w = dict(re.findall(r"([SXYZ])(-?[\d.]+)", cmd))
    s = float(w["S"])                               # M556: X -> tanXY, Y -> tanYZ, Z -> tanXZ
    return float(w.get("X", 0)) / s, float(w.get("Z", 0)) / s, float(w.get("Y", 0)) / s


# ---- the printer that skew_gcode.py and skewcal model -------------------------------
def printer(cmd, xy_angle, xz_angle, yz_angle):
    t = math.radians(xy_angle)
    ezx, ezy, ezz = z_direction(xy_angle, xz_angle, yz_angle)
    x, y, z = cmd
    return (x + y * math.cos(t) + z * ezx, y * math.sin(t) + z * ezy, z * ezz)


def worst_xy_error(angles, transform):
    pts = [(x, y, z) for x, y, z in itertools.product((0, 45, 90, 135, 180), repeat=3)]
    return max(math.hypot(p[0] - q[0], p[1] - q[1]) for p in pts for q in [printer(transform(p), *angles)])


CASES = [(89.722, 90, 90), (90.25, 90, 90), (89.722, 89.82, 90.12), (90.3, 90.4, 89.7), (88.5, 91.0, 89.2)]
failures = []
for angles in CASES:
    c = firmware_commands(*angles)
    kf, mf, rt = parse_klipper(c["klipper"]), parse_marlin(c["marlin"]), parse_reprap(c["reprap"])
    if len(re.findall(r"[JK]", c["marlin"])) == 0:   # Marlin without Z skew: J/K absent means 0
        mf = (mf[0], 0.0, 0.0)
    errs = {
        "none": worst_xy_error(angles, lambda p: p),
        "Klipper": worst_xy_error(angles, lambda p: klipper_marlin_skew(p, *kf)),
        "Marlin": worst_xy_error(angles, lambda p: klipper_marlin_skew(p, *mf)),
        "RRF": worst_xy_error(angles, lambda p: reprap_axis_transform(p, *rt)),
        "RRF, sign flipped": worst_xy_error(angles, lambda p: reprap_axis_transform(p, *[-v for v in rt])),
    }
    # what no firmware term covers: Y and Z shorten by sin/cos of the lean (~1e-5 relative)
    t = math.radians(angles[0]); ezz = z_direction(*angles)[2]
    limit = 180 * ((1 - math.sin(t)) + (1 - ezz)) + 0.002
    ok = all(errs[k] < limit for k in ("Klipper", "Marlin", "RRF")) and errs["RRF, sign flipped"] > 10 * limit \
        and errs["none"] > 10 * limit
    print(f"{'PASS' if ok else 'FAIL'}  angles {angles}: worst XY error over a 180 mm cube (um) " +
          ", ".join(f"{k} {v * 1000:.1f}" for k, v in errs.items()) + f"  [limit {limit * 1000:.1f}]")
    if not ok:
        failures.append(angles)

print("ALL PASSED" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)
