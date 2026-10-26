"""Firmware skew-compensation settings for a measured printer (standard library only).

The printer model is the one skew_gcode.py corrects: commanded (x, y, z) lands at
    x * (1, 0, 0) + y * (cos t, sin t, 0) + z * (ezx, ezy, ezz)
with t the X-Y angle and ez the Z axis direction from the X-Z / Y-Z angles.

Each firmware applies its own linear transform to the requested position before
moving (taken from the firmware sources, see tests/test_firmware.py):

  Klipper  (skew_correction.py) and Marlin (planner.h skew()):
      x_cmd = x - y*xy - z*(xz - xy*yz)        y_cmd = y - z*yz
  RepRapFirmware (Move::AxisTransform, default "XY" mode):
      x_cmd = x + y*tanXY + z*tanXZ            y_cmd = y + z*tanYZ

Matching those to the printer model gives exact X/Y corrections. What no firmware
term covers is the ~1e-5 shortening of Y and Z along a leaning axis, which is
far below printing precision.
"""
import math
import re


def z_direction(xy_angle=90.0, xz_angle=90.0, yz_angle=90.0):
    """Unit Z-axis direction (ezx, ezy, ezz). The Y-Z angle is measured against the
    printer's (possibly skewed) Y axis, so X-Y skew enters ezy."""
    t = math.radians(xy_angle)
    ezx = math.cos(math.radians(xz_angle))
    ezy = (math.cos(math.radians(yz_angle)) - math.cos(t) * ezx) / math.sin(t)
    ezz = math.sqrt(max(0.0, 1.0 - ezx * ezx - ezy * ezy))
    return ezx, ezy, ezz


def klipper_marlin_factors(xy_angle=90.0, xz_angle=90.0, yz_angle=90.0):
    """(xy, xz, yz) factors for Klipper's skew_correction and Marlin's M852 I/J/K."""
    t = math.radians(xy_angle)
    ezx, ezy, _ = z_direction(xy_angle, xz_angle, yz_angle)
    return math.cos(t), ezx, ezy / math.sin(t)


def reprap_tangents(xy_angle=90.0, xz_angle=90.0, yz_angle=90.0):
    """(tanXY, tanXZ, tanYZ) for RepRapFirmware's M556 (default XY compensation)."""
    t = math.radians(xy_angle)
    ezx, ezy, _ = z_direction(xy_angle, xz_angle, yz_angle)
    tan_yz = -ezy / math.sin(t)
    tan_xz = -ezx - math.cos(t) * tan_yz
    return -math.cos(t), tan_xz, tan_yz


def klipper_lengths(factor, side=100.0):
    """AC, BD, AD of a virtual square of the given side that makes Klipper's
    calc_skew_factor(ac, bd, ad) return `factor` (= cot of the angle at A)."""
    psi = math.atan2(1.0, factor)                      # angle between the square's sides
    ac = side * math.sqrt(2.0 + 2.0 * math.cos(psi))
    bd = side * math.sqrt(2.0 - 2.0 * math.cos(psi))
    return ac, bd, side


def firmware_commands(xy_angle=90.0, xz_angle=90.0, yz_angle=90.0, length=100.0):
    """Ready-to-use command lines. Klipper and RepRapFirmware get all three planes,
    so values left over from an earlier calibration are replaced; Marlin gets J/K
    only when a Z angle was measured, because they need SKEW_CORRECTION_FOR_Z."""
    xy, xz, yz = klipper_marlin_factors(xy_angle, xz_angle, yz_angle)
    txy, txz, tyz = reprap_tangents(xy_angle, xz_angle, yz_angle)
    has_z = abs(xz_angle - 90) > 1e-9 or abs(yz_angle - 90) > 1e-9

    def num(v, d):
        return f"{v + 0.0:.{d}f}".replace("-0." + "0" * d, "0." + "0" * d)

    planes = []
    for name, f in (("XY", xy), ("XZ", xz), ("YZ", yz)):
        ac, bd, ad = klipper_lengths(f, length)
        planes.append(f"{name}={ac:.4f},{bd:.4f},{ad:.4f}")
    klipper = "SET_SKEW " + " ".join(planes)
    marlin = f"M852 I{num(xy, 6)}" + (f" J{num(xz, 6)} K{num(yz, 6)}" if has_z else "")
    reprap = f"M556 S{length:g} X{num(length * txy, 4)} Y{num(length * tyz, 4)} Z{num(length * txz, 4)}"
    return dict(klipper=klipper, marlin=marlin, reprap=reprap,
                factors=dict(xy=xy, xz=xz, yz=yz), tangents=dict(xy=txy, xz=txz, yz=tyz))


def plane_commands(plane, angle, length=100.0):
    """Commands that set only one measured plane (others are left as the firmware has
    them), for the per-measurement report. Use firmware_commands() with all measured
    angles for the exact combined setting."""
    angles = {"xy": (angle, 90.0, 90.0), "xz": (90.0, angle, 90.0), "yz": (90.0, 90.0, angle)}[plane]
    c = firmware_commands(*angles, length=length)
    up = plane.upper()
    klipper = "SET_SKEW " + re.search(up + r"=[\d.,-]+", c["klipper"]).group(0)
    letter = {"xy": "I", "xz": "J", "yz": "K"}[plane]
    marlin = "M852 " + letter + f"{c['factors'][plane] + 0.0:.6f}"
    rletter = {"xy": "X", "xz": "Z", "yz": "Y"}[plane]
    reprap = f"M556 S{length:g} " + re.search(rletter + r"-?[\d.]+", c["reprap"].split(" ", 2)[2]).group(0)
    return dict(klipper=klipper, marlin=marlin, reprap=reprap)


NOTES = dict(
    klipper="needs [skew_correction] in printer.cfg; then SKEW_PROFILE SAVE=skewcal, SAVE_CONFIG, "
            "and SKEW_PROFILE LOAD=skewcal in your print start macro",
    marlin="needs a firmware build with SKEW_CORRECTION_GCODE (and SKEW_CORRECTION_FOR_Z for J/K); then M500",
    reprap="put it in config.g",
)
