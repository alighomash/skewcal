"""End-to-end checks: render synthetic images with known skew/scale and confirm
skewcal recovers them. Run:  python3 tests/run_tests.py"""
import io
import math
import os
import sys
import contextlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import synth  # noqa: E402
from skewcal.plate import PlateSpec  # noqa: E402
import skewcal as _pkg  # noqa: E402,F401

sys.path.insert(0, os.path.join(HERE, ".."))
import importlib.util  # noqa: E402
spec_ = importlib.util.spec_from_file_location("skewcal_cli", os.path.join(HERE, "..", "skewcal.py"))
cli = importlib.util.module_from_spec(spec_); spec_.loader.exec_module(cli)

OUT = os.path.join(HERE, "_out")
os.makedirs(OUT, exist_ok=True)
spec = PlateSpec()
failures = []


def run(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = cli.main(args)
    return res, buf.getvalue()


def check(name, res, theta, sx, sy, tol_th, tol_s, need_reversal=True):
    dth = res["theta"] - theta
    dsx, dsy = res["scale_x"] - sx, res["scale_y"] - sy
    ok = abs(dth) < tol_th and abs(dsx) < tol_s and abs(dsy) < tol_s
    if need_reversal:
        ok = ok and res["reversal"]
    print(f"{'PASS' if ok else 'FAIL'}  {name}: theta err {dth*1000:+.1f} mdeg (+/-{res['theta_std']*1000:.1f}),"
          f" scale err X {dsx*1e4:+.1f} Y {dsy*1e4:+.1f} (x1e-4)"
          + (f", frame bias {res['frame_bias_deg']:+.4f}" if res.get("frame_bias_deg") is not None else ""))
    if not ok:
        failures.append(name)


# ---- 1. scanner, 600 dpi, mirrored + rotated, scanner squareness error ----------
th, sx, sy = 90.25, 0.9965, 1.0025
M = synth.printer_matrix(th, sx, sy)
a = os.path.join(OUT, "scan_a.png"); b = os.path.join(OUT, "scan_b.png")
synth.render_scan(a, spec, M, dpi=600, rot=3.0, mirror=True, frame_e=0.06, kx=1.001, ky=0.999, seed=1)
synth.render_scan(b, spec, M, dpi=600, rot=93.5, mirror=True, frame_e=0.06, kx=1.001, ky=0.999, seed=2)
res, txt = run(["scan", a, b, "--out", OUT])
check("scan 600dpi mirrored, reversal", res, th, sx, sy, 0.01, 4e-4)

# single image (no reversal) should be biased by ~the scanner error
res1, _ = run(["scan", a, "--out", OUT, "--no-annotate"])
print(f"      single-scan bias {1000*(res1['theta']-th):+.1f} mdeg (expected ~ +/-60 from scanner error)")

# ---- 2. 1200 dpi (downsampled path), light plate on dark background --------------
th2, sx2, sy2 = 89.82, 1.0012, 0.9978
M2 = synth.printer_matrix(th2, sx2, sy2)
a2 = os.path.join(OUT, "scan_c.png"); b2 = os.path.join(OUT, "scan_d.png")
synth.render_scan(a2, spec, M2, dpi=1200, rot=-1.0, dark_plate=False, seed=3)
synth.render_scan(b2, spec, M2, dpi=1200, rot=-91.0, dark_plate=False, seed=4)
res, _ = run(["scan", a2, b2, "--out", OUT, "--no-annotate"])
check("scan 1200dpi light plate", res, th2, sx2, sy2, 0.01, 4e-4)

# ---- 3. phone photos: perspective, lens distortion, skewed paper, parallax ------------
th3, sx3, sy3 = 89.85, 1.002, 0.998
M3 = synth.printer_matrix(th3, sx3, sy3)
paper = np.array([[1.003, 1.003 * math.cos(math.radians(90.07))],
                  [0, 0.998 * math.sin(math.radians(90.07))]])
p1 = os.path.join(OUT, "photo_1.jpg"); p2 = os.path.join(OUT, "photo_2.jpg")
synth.render_photo(p1, spec, M3, cam=(20, -60, 320), plate_rot=1.5, plate_off=(2, -1), k1=-0.04,
                   paper=paper, seed=5)
synth.render_photo(p2, spec, M3, cam=(-30, -45, 340), plate_rot=91.0, plate_off=(-1, 2), k1=-0.04,
                   paper=paper, seed=6)
res, txt = run(["photo", p1, p2, "--out", OUT, "--focal-px", "3000",
                "--span-x", f"{174*1.003:.2f}", "--span-y", f"{234*0.998:.2f}"])
check("phone, tilt + distortion + paper skew, reversal", res, th3, sx3, sy3, 0.02, 8e-4)

# without focal length (no EXIF): scale relies on estimated/assumed focal
res, _ = run(["photo", p1, p2, "--out", OUT, "--no-annotate",
              "--span-x", f"{174*1.003:.2f}", "--span-y", f"{234*0.998:.2f}"])
check("phone, focal unknown", res, th3, sx3, sy3, 0.02, 1.5e-3)

# ---- 4. phone, light plate on the dark-window sheet --------------------------------
p3 = os.path.join(OUT, "photo_3.jpg"); p4 = os.path.join(OUT, "photo_4.jpg")
synth.render_photo(p3, spec, M3, cam=(0, -30, 300), plate_rot=-2, dark_plate=False, dark_window=True,
                   k1=0.02, seed=7)
synth.render_photo(p4, spec, M3, cam=(10, -20, 300), plate_rot=88, dark_plate=False, dark_window=True,
                   k1=0.02, seed=8)
res, _ = run(["photo", p3, p4, "--out", OUT, "--no-annotate", "--focal-px", "3000"])
check("phone, light plate / dark window", res, th3, sx3, sy3, 0.02, 8e-4)

# ---- 5. phone, marker holes printed mostly closed + cupped plate ---------------------
p5 = os.path.join(OUT, "photo_5.jpg"); p6 = os.path.join(OUT, "photo_6.jpg")
synth.render_photo(p5, spec, M3, cam=(25, -50, 250), plate_rot=1.0, k1=-0.02, paper=paper,
                   marker_scale=0.3, warp=1.5, seed=9)
synth.render_photo(p6, spec, M3, cam=(-20, -40, 260), plate_rot=-89.0, k1=-0.02, paper=paper,
                   marker_scale=0.3, warp=1.5, seed=10)
res, _ = run(["photo", p5, p6, "--out", OUT, "--no-annotate", "--focal-px", "3000",
              "--span-x", f"{174*1.003:.2f}", "--span-y", f"{234*0.998:.2f}"])
check("phone, closed marker holes + 1.5 mm cupped plate", res, th3, sx3, sy3, 0.02, 1.5e-3)
res, _ = run(["photo", p5, p6, "--out", OUT, "--no-annotate", "--focal-px", "3000", "--no-warp",
              "--span-x", f"{174*1.003:.2f}", "--span-y", f"{234*0.998:.2f}"])
print(f"      same without warp model: theta err {1000*(res['theta']-th3):+.1f} mdeg,"
      f" scale err X {1e4*(res['scale_x']-sx3):+.1f} Y {1e4*(res['scale_y']-sy3):+.1f} (x1e-4)")

print()
print("ALL PASSED" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)
