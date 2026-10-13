"""Glue between the browser app (web/worker.js) and the skewcal package.

Every function takes and returns plain strings / JSON so it is easy to call
from JavaScript through Pyodide. Results are kept here between calls, so the
page can analyse images one at a time (showing progress) and then combine them.
"""
import json
import math
import os
import zipfile

import numpy as np

from skewcal import analyze as an
from skewcal.plate import PlateSpec

PLATES = {"thinrim": PlateSpec(), "standard": PlateSpec.standard(), "cubewall": PlateSpec.cube_wall()}
SPEC = PLATES["thinrim"]
_results = []          # [(name, result dict | None, error | None)]


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return o if math.isfinite(o) else None
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    return o


def reset():
    _results.clear()


def analyze_one(mode, path, name, opts_json, overlay_dir="/tmp/out"):
    """Analyse one image. opts: plate, dpi, span_x, span_y, warp ("auto"|true|false), focal35."""
    global SPEC
    o = json.loads(opts_json or "{}")
    SPEC = PLATES.get(o.get("plate") or "thinrim", PLATES["thinrim"])
    os.makedirs(overlay_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(name))[0]
    ann = os.path.join(overlay_dir, stem + "_skewcal.jpg")
    try:
        if mode == "scan":
            r = an.analyze_scan(path, SPEC, dpi=o.get("dpi") or None, annotate_path=ann,
                                details=True, annotate_crop=True)
        else:
            focal_px = None
            if o.get("focal35") and o.get("width") and o.get("height"):
                focal_px = float(o["focal35"]) * math.hypot(o["width"], o["height"]) / 43.2666
            r = an.analyze_photo(path, SPEC, focal_px=focal_px, span_x=o.get("span_x") or None,
                                 span_y=o.get("span_y") or None, screen_bar=o.get("screen_bar") or None,
                                 annotate_path=ann,
                                 fit_distortion=o.get("distortion", True),
                                 correct_warp=o.get("warp", "auto"), details=True, annotate_crop=True)
            if focal_px:
                r["focal_src"] = "EXIF"
        _results.append((name, r, None))
        out = dict(name=name, ok=True, result=_clean(r), overlay_path=ann)
    except Exception as e:  # report the failure for this image and carry on
        msg = str(e) or e.__class__.__name__
        _results.append((name, None, msg))
        out = dict(name=name, ok=False, error=msg)
    return json.dumps(out)


def combine_all(mode, opts_json="{}"):
    """opts: plane ("xy", or "xz" / "yz" for cube walls)."""
    import skewcal_cli
    plane = json.loads(opts_json or "{}").get("plane") or "xy"
    ok = [(n, r) for n, r, e in _results if r is not None]
    images = []
    for n, r, e in _results:
        d = dict(name=n, ok=r is not None)
        if r is None:
            d["error"] = e
        else:
            d["result"] = _clean(r)
            d["overlay_path"] = "/tmp/out/" + os.path.splitext(os.path.basename(n))[0] + "_skewcal.jpg"
        images.append(d)
    if not ok:
        return json.dumps(dict(ok=False, images=images, mode=mode, plane=plane))
    names, results = [n for n, _ in ok], [r for _, r in ok]
    combined = an.combine(results)
    report = skewcal_cli.format_report(results, names, combined, SPEC, mode,
                                       script_path="/path/to/skew_gcode.py", plane=plane)
    full = dict(plane=plane, combined=combined, images=dict(zip(names, results)), plate=SPEC.__dict__)
    return json.dumps(_clean(dict(ok=True, mode=mode, plane=plane, combined=combined, images=images,
                                  report=report, report_json=json.dumps(_clean(full), indent=2),
                                  plate=SPEC.__dict__)))


def write_gray_png(path, raw_path, width, height):
    """Save 8-bit grey pixels decoded by the browser (e.g. from HEIC) as PNG."""
    import cv2
    g = np.fromfile(raw_path, np.uint8).reshape(int(height), int(width))
    cv2.imwrite(path, g, [cv2.IMWRITE_PNG_COMPRESSION, 1])


def correct_gcode(src, dst, angle, scale_x=1.0, scale_y=1.0, cx=90.0, cy=90.0, xz=90.0, yz=90.0):
    """Run skew_gcode on a file in the virtual filesystem. Returns JSON stats or error."""
    import io
    import contextlib
    import skew_gcode
    err = io.StringIO()
    argv = [src, "--xy-angle", str(angle), "--scale-x", str(scale_x), "--scale-y", str(scale_y),
            "--xz-angle", str(xz), "--yz-angle", str(yz), "--center", f"{cx},{cy}", "-o", dst]
    try:
        with contextlib.redirect_stderr(err):
            skew_gcode.main(argv)
    except SystemExit as e:
        return json.dumps(dict(ok=False, error=str(e.code) if e.code not in (None, 0) else err.getvalue()))
    except Exception as e:
        return json.dumps(dict(ok=False, error=str(e)))
    return json.dumps(dict(ok=True, message=err.getvalue().strip(), size=os.path.getsize(dst)))


def make_zip(zip_path, members_json):
    """members: [[path_in_fs, name_in_zip], ...]"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in json.loads(members_json):
            z.write(src, arc)
    return os.path.getsize(zip_path)
