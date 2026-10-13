#!/usr/bin/env python3
"""Apply XY skew (and optional scale) correction to a G-code file.

Standard library only, so it can run as a PrusaSlicer post-processing script:

  Print Settings -> Output options -> Post-processing scripts:
    /usr/bin/python3 "/path/to/skew_gcode.py" --xy-angle 90.123;

PrusaSlicer appends the G-code path; the file is rewritten in place.

--xy-angle is the measured angle between the printer's X and Y axes (the
"XY angle" printed by skewcal.py). 90 means perfectly square. --xz-angle and
--yz-angle are the angles between X and Z, and between Y and Z (from the
skewcal cube's walls).

Model: commanded (x, y) lands physically at  M @ (x, y)  with
    M = [[sx, sy*cos(t)], [0, sy*sin(t)]]
so we send  M^-1 @ (x, y)  instead (about a pivot, default bed centre).
A leaning Z axis moves the nozzle sideways by z * (ezx, ezy) as it rises (ez is
the Z axis' unit direction), so that is subtracted first. Z values themselves
are left unchanged: the lean shortens the true height by only ~1e-5.
Arcs (G2/G3) are converted to short line segments because a sheared arc is
no longer circular.
"""
import argparse
import math
import os
import re
import sys

WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


def fmt(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class Corrector:
    def __init__(self, angle_deg, sx=1.0, sy=1.0, cx=90.0, cy=90.0, seg=0.5, xz=90.0, yz=90.0):
        t = math.radians(angle_deg)
        self.cot = math.cos(t) / math.sin(t)
        self.isin = 1.0 / math.sin(t)
        # Z axis direction from its angles to the X axis and to the (possibly skewed) Y axis
        cxz, cyz = math.cos(math.radians(xz)), math.cos(math.radians(yz))
        self.zx = cxz
        self.zy = (cyz - math.cos(t) * cxz) / math.sin(t)
        self.shear_z = abs(self.zx) > 1e-12 or abs(self.zy) > 1e-12
        self.sx, self.sy = sx, sy
        self.cx, self.cy = cx, cy
        self.seg = seg
        # state (logical = slicer coordinates)
        self.abs_xy = True
        self.abs_e = True
        self.x = self.y = None
        self.z = 0.0
        self.e = 0.0
        self.warn_unknown = 0
        self.arcs = 0

    # linear part of the correction (for deltas)
    def lin(self, dx, dy):
        return (dx - dy * self.cot) / self.sx, dy * self.isin / self.sy

    def tf(self, x, y, z=None):
        z = self.z if z is None else z
        dx, dy = self.lin(x - self.cx - self.zx * z, y - self.cy - self.zy * z)
        return self.cx + dx, self.cy + dy

    def lin_z(self, dx, dy, dz):
        """Correction of a relative move that also changes Z."""
        return self.lin(dx - self.zx * dz, dy - self.zy * dz)

    def process_line(self, line):
        raw = line.rstrip("\r\n")
        code, sep, comment = raw.partition(";")
        stripped = code.strip()
        if not stripped:
            return [line]
        words = WORD.findall(stripped)
        if not words:
            return [line]
        cmd = (words[0][0].upper() + str(int(float(words[0][1])))) if words[0][0].upper() in "GM" else None
        params = {k.upper(): float(v) for k, v in words[1:]}
        tail = (sep + comment) if sep else ""
        nl = line[len(raw):] or "\n"

        if cmd == "G90":
            self.abs_xy = True; self.abs_e = True
        elif cmd == "G91":
            self.abs_xy = False; self.abs_e = False
        elif cmd == "M82":
            self.abs_e = True
        elif cmd == "M83":
            self.abs_e = False
        elif cmd in ("G28", "G29"):
            self.x = self.y = None
        elif cmd == "G92":
            if "E" in params:
                self.e = params["E"]
            if "X" in params or "Y" in params:
                nx = params.get("X", self.x); ny = params.get("Y", self.y)
                self.x, self.y = nx, ny
                if nx is not None and ny is not None:
                    px, py = self.tf(nx, ny)
                    params["X"], params["Y"] = px, py
                    return [self.emit("G92", params, tail, nl)]
        elif cmd in ("G0", "G1"):
            return self.move(cmd, params, tail, nl, line)
        elif cmd in ("G2", "G3"):
            return self.arc(cmd, params, tail, nl)
        return [line]

    def emit(self, cmd, params, tail, nl):
        order = ["X", "Y", "Z", "I", "J", "E", "F"]
        parts = [cmd]
        for k in order:
            if k in params:
                parts.append(k + (fmt(params[k]) if k != "E" else f"{params[k]:.5f}".rstrip("0").rstrip(".")))
        for k, v in params.items():
            if k not in order:
                parts.append(k + fmt(v))
        return " ".join(parts) + (" " + tail if tail else "") + nl

    def track_ze(self, params):
        if "Z" in params:
            self.z = params["Z"] if self.abs_xy else self.z + params["Z"]
        if "E" in params:
            self.e = params["E"] if self.abs_e else self.e + params["E"]

    def move(self, cmd, params, tail, nl, line):
        has_xy = "X" in params or "Y" in params
        dz = 0.0
        if "Z" in params:
            dz = params["Z"] - self.z if self.abs_xy else params["Z"]
        if not has_xy:
            # with a leaning Z, a pure Z move must also nudge X/Y to stay on target
            if self.shear_z and dz and (not self.abs_xy or self.x is not None and self.y is not None):
                self.track_ze(params)
                if self.abs_xy:
                    params["X"], params["Y"] = self.tf(self.x, self.y)
                else:
                    params["X"], params["Y"] = self.lin_z(0.0, 0.0, dz)
                return [self.emit(cmd, params, tail, nl)]
            self.track_ze(params)
            return [line]
        if "Z" in params:
            self.z += dz                   # X/Y are corrected for the height they end at
        if self.abs_xy:
            nx = params.get("X", self.x)
            ny = params.get("Y", self.y)
            if nx is None or ny is None:
                self.warn_unknown += 1
                if "X" in params: self.x = params["X"]
                if "Y" in params: self.y = params["Y"]
                if "E" in params:
                    self.e = params["E"] if self.abs_e else self.e + params["E"]
                return [line]
            self.x, self.y = nx, ny
            params["X"], params["Y"] = self.tf(nx, ny)
        else:
            dx, dy = params.get("X", 0.0), params.get("Y", 0.0)
            if self.x is not None and self.y is not None:
                self.x += dx; self.y += dy
            params["X"], params["Y"] = self.lin_z(dx, dy, dz)
        if "E" in params:
            self.e = params["E"] if self.abs_e else self.e + params["E"]
        return [self.emit(cmd, params, tail, nl)]

    def arc(self, cmd, params, tail, nl):
        if "R" in params:
            raise SystemExit("skew_gcode: G2/G3 with R is not supported; disable arc fitting.")
        if self.x is None or self.y is None:
            raise SystemExit("skew_gcode: arc before XY position is known.")
        self.arcs += 1
        x0, y0, z0, e0 = self.x, self.y, self.z, self.e
        if self.abs_xy:
            x1, y1 = params.get("X", x0), params.get("Y", y0)
            z1 = params.get("Z", z0)
        else:
            x1, y1 = x0 + params.get("X", 0.0), y0 + params.get("Y", 0.0)
            z1 = z0 + params.get("Z", 0.0)
        ox, oy = x0 + params.get("I", 0.0), y0 + params.get("J", 0.0)
        r = math.hypot(x0 - ox, y0 - oy)
        a0 = math.atan2(y0 - oy, x0 - ox)
        a1 = math.atan2(y1 - oy, x1 - ox)
        cw = cmd == "G2"
        sweep = a1 - a0
        if cw:
            if sweep >= -1e-9: sweep -= 2 * math.pi
        else:
            if sweep <= 1e-9: sweep += 2 * math.pi
        n = max(1, int(math.ceil(abs(sweep) * r / self.seg)))
        de_total = None
        if "E" in params:
            de_total = params["E"] - e0 if self.abs_e else params["E"]
        out = []
        px_prev, py_prev = x0, y0
        for k in range(1, n + 1):
            f = k / n
            if k == n:
                xk, yk = x1, y1
            else:
                a = a0 + sweep * f
                xk, yk = ox + r * math.cos(a), oy + r * math.sin(a)
            zk = z0 + (z1 - z0) * f
            p = {}
            if self.abs_xy:
                p["X"], p["Y"] = self.tf(xk, yk, zk)
                if "Z" in params: p["Z"] = zk
            else:
                p["X"], p["Y"] = self.lin_z(xk - px_prev, yk - py_prev, (z1 - z0) / n)
                if "Z" in params: p["Z"] = (z1 - z0) / n
            if de_total is not None:
                p["E"] = (e0 + de_total * f) if self.abs_e else de_total / n
            if k == 1 and "F" in params:
                p["F"] = params["F"]
            out.append(self.emit("G1", p, tail if k == 1 else "", nl))
            px_prev, py_prev = xk, yk
        self.x, self.y, self.z = x1, y1, z1
        if de_total is not None:
            self.e = e0 + de_total
        return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gcode")
    ap.add_argument("--xy-angle", type=float, default=90.0,
                    help="measured angle between X and Y axes in degrees (90 = square)")
    ap.add_argument("--scale-x", type=float, default=1.0,
                    help="measured/nominal X scale to correct (default 1 = no change)")
    ap.add_argument("--scale-y", type=float, default=1.0)
    ap.add_argument("--xz-angle", type=float, default=90.0,
                    help="measured angle between X and Z in degrees (skewcal cube front/back walls)")
    ap.add_argument("--yz-angle", type=float, default=90.0,
                    help="measured angle between Y and Z in degrees (skewcal cube left/right walls)")
    ap.add_argument("--center", default="90,90", help="pivot point x,y in mm (default MINI bed centre 90,90)")
    ap.add_argument("--seg", type=float, default=0.5, help="max arc segment length (mm)")
    ap.add_argument("-o", "--output", help="write here instead of in place")
    a = ap.parse_args(argv)

    for name in ("xy_angle", "xz_angle", "yz_angle"):
        if abs(getattr(a, name) - 90) > 3:
            raise SystemExit(f"skew_gcode: --{name.replace('_', '-')} should be close to 90 degrees.")
    with open(a.gcode, "rb") as f:
        head = f.read(4)
    if head == b"GCDE":
        raise SystemExit("skew_gcode: this is binary G-code. Disable 'Supports binary G-code' in "
                         "Printer Settings > General so post-processing gets plain text.")
    cx, cy = (float(v) for v in a.center.split(","))
    c = Corrector(a.xy_angle, a.scale_x, a.scale_y, cx, cy, a.seg, a.xz_angle, a.yz_angle)
    with open(a.gcode, "r", newline="") as f:
        lines = f.readlines()
    out = [f"; skew_gcode: XY angle {a.xy_angle:.4f} deg, XZ {a.xz_angle:.4f}, YZ {a.yz_angle:.4f}, "
           f"scale {a.scale_x:.5f}/{a.scale_y:.5f}, pivot {cx},{cy}\n"]
    for ln in lines:
        out.extend(c.process_line(ln))
    dst = a.output or a.gcode
    tmp = dst + ".skewtmp"
    with open(tmp, "w", newline="") as f:
        f.writelines(out)
    os.replace(tmp, dst)
    msg = f"skew_gcode: corrected {len(lines)} lines"
    if c.arcs:
        msg += f", linearised {c.arcs} arcs"
    if c.warn_unknown:
        msg += f", {c.warn_unknown} moves passed through (position unknown after homing)"
    print(msg, file=sys.stderr)


if __name__ == "__main__":
    main()
