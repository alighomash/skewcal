#!/usr/bin/env python3
"""skewcal - measure 3D-printer XY skew and scale with a scanner or phone camera.

  python3 skewcal.py plate  [-o skewcal_plate.stl]        # test plate STL (+ .json)
  python3 skewcal.py sheet  [--page letter|a4] [--dark]   # phone reference sheet PDF
  python3 skewcal.py scan   SCAN1.png SCAN2.png [--dpi 600]
  python3 skewcal.py photo  IMG_1.jpg IMG_2.jpg [--span-x 174.0 --span-y 234.0]

Take at least two images with the plate turned 90 degrees between them; the
tool then cancels the scanner's / reference sheet's own squareness error.
"""
import argparse
import json
import math
import os
import sys

from skewcal.plate import PlateSpec, build_mesh, check_watertight, write_stl
from skewcal import sheet as sheet_mod


def cmd_plate(a):
    spec = PlateSpec(n=a.n, pitch=a.pitch, hole_d=a.hole_d, marker_d=a.marker_d,
                     thickness=a.thickness)
    walls = spec.validate()
    V, F = build_mesh(spec)
    if not check_watertight(F):
        sys.exit("internal error: mesh not watertight")
    write_stl(a.output, V, F)
    js = os.path.splitext(a.output)[0] + ".json"
    spec.to_json(js)
    print(f"Plate {spec.size:.1f} x {spec.size:.1f} x {spec.thickness} mm, "
          f"{spec.n}x{spec.n} holes @ {spec.pitch} mm (span {spec.span:.1f} mm)")
    print(f"Thinnest wall {min(walls.values()):.2f} mm. Wrote {a.output} and {js}")
    print("Print it flat, exactly as oriented in the file (do not rotate it in the slicer).")


def cmd_sheet(a):
    out = a.output or f"skewcal_reference_{a.page}{'_dark' if a.dark else ''}.pdf"
    sheet_mod.save_pdf(out, a.page, a.dark)
    print(f"Wrote {out}. Print at 100% / Actual size (no 'fit to page').")
    print(f"Then measure the two spans marked with arrows on the sheet: SPAN X, the top-row outer edges "
          f"(nominal {sheet_mod.SPAN_X_NOMINAL:.0f} mm), and SPAN Y, the left-column outer edges "
          f"(nominal {sheet_mod.SPAN_Y_NOMINAL:.0f} mm). Pass them as --span-x / --span-y.")


def _load_spec(a):
    return PlateSpec.from_json(a.plate) if a.plate else PlateSpec()


def format_report(results, names, combined, spec, mode, script_path=None):
    """The human-readable report (also used by the web app)."""
    if script_path is None:
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skew_gcode.py")
    lines = []
    P = lines.append
    P("")
    P("Per image:")
    for n, r in zip(names, results):
        orient = "X along frame x" if r["plate_x_along_frame_x"] else "X along frame y"
        extra = ""
        if mode == "photo":
            extra = (f"  [{r['markers']} markers, ref fit {r['ref_rms_um']:.0f} um, tilt {r['tilt_deg']:.0f} deg, "
                     f"distance {r['camera_distance_mm']:.0f} mm ({r['focal_src']} focal)]")
            if "warp_corners_mm" in r:
                extra += (" [plate corners vs centre: "
                          + " ".join(f"{z:+.1f}" for z in r["warp_corners_mm"]) + " mm]")
        P(f"  {n}: XY angle {r['theta']:.4f} +/- {r['theta_std']:.4f} deg | scale X {100*r['scale_x']:.3f}%"
          f" Y {100*r['scale_y']:.3f}% | {r['n_holes']}/{r['n_expected']} holes, fit rms {r['rms_um']:.1f} um"
          f" | {orient}{', mirrored' if r['mirrored'] else ''}{extra}")
    th, sd = combined["theta"], combined["theta_std"]
    skew = th - 90.0
    per100 = 100 * math.tan(math.radians(skew))
    P("")
    P("=" * 72)
    P(f"XY angle:  {th:.4f} deg  (+/- {sd:.4f})")
    P(f"Skew:      {skew:+.4f} deg  ->  X drifts {abs(per100):.3f} mm per 100 mm of Y travel")
    if skew > 0:
        P("           (the Y axis leans toward -X: corners at +X+Y / -X-Y are obtuse)")
    elif skew < 0:
        P("           (the Y axis leans toward +X: corners at +X+Y / -X-Y are acute)")
    if combined["reversal"]:
        P(f"Reference bias removed by 90-deg reversal: {combined['frame_bias_deg']:+.4f} deg")
    else:
        P("WARNING: all images had the plate in the same orientation, so the scanner/sheet's own")
        P("         squareness error is NOT cancelled. Add an image with the plate turned 90 deg.")
    sx, sy = combined["scale_x"], combined["scale_y"]
    P(f"Scale:     X {100*sx:.3f}%   Y {100*sy:.3f}%   (hole-centre spacing, measured/nominal)")
    if mode == "photo" and tuple(results[0].get("sheet_scale", (1.0, 1.0))) == (1.0, 1.0):
        P("           (absolute scale assumes the sheet printed at exactly 100%; pass --span-x/--span-y")
        P("            with your ruler/caliper measurements to make it exact)")
    P(f"           -> slicer object scale X {100/sx:.2f}%  Y {100/sy:.2f}% (only if you want to compensate)")
    hd = [r["hole_d_mean"] for r in results if r.get("hole_d_mean")]
    if hd and mode == "scan":
        P(f"Holes:     mean diameter {sum(hd)/len(hd):.3f} mm vs nominal {spec.hole_d} mm (first-layer side)")
    P("")
    if abs(skew) < 0.02:
        P("Skew is negligible (< 0.02 deg); no correction needed.")
    else:
        P("To correct, add this PrusaSlicer post-processing script (Print Settings > Output options):")
        P(f'  /usr/bin/python3 "{script_path}" --xy-angle {th:.4f};')
    P("=" * 72)
    return "\n".join(lines)


def _report(results, names, combined, spec, outdir, mode):
    text = format_report(results, names, combined, spec, mode)
    print(text)
    with open(os.path.join(outdir, "skewcal_report.txt"), "w") as f:
        f.write(text + "\n")
    with open(os.path.join(outdir, "skewcal_report.json"), "w") as f:
        json.dump({"combined": combined, "images": dict(zip(names, results)),
                   "plate": spec.__dict__}, f, indent=2, default=float)


def _run(a, mode):
    from skewcal import analyze as an
    spec = _load_spec(a)
    outdir = a.out or os.path.dirname(os.path.abspath(a.images[0]))
    os.makedirs(outdir, exist_ok=True)
    results, names = [], []
    for p in a.images:
        base = os.path.splitext(os.path.basename(p))[0]
        ann = None if a.no_annotate else os.path.join(outdir, base + "_skewcal.jpg")
        try:
            if mode == "scan":
                r = an.analyze_scan(p, spec, dpi=a.dpi, annotate_path=ann)
            else:
                r = an.analyze_photo(p, spec, focal_px=a.focal_px, span_x=a.span_x, span_y=a.span_y,
                                     hole_height=a.hole_height, annotate_path=ann,
                                     fit_distortion=not a.no_distortion,
                                     correct_warp=not a.no_warp)
        except RuntimeError as e:
            print(f"  {os.path.basename(p)}: FAILED - {e}", file=sys.stderr)
            continue
        results.append(r); names.append(os.path.basename(p))
    if not results:
        sys.exit("No image could be analysed.")
    combined = an.combine(results)
    _report(results, names, combined, spec, outdir, mode)
    return combined


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plate", help="generate the test-plate STL")
    p.add_argument("-o", "--output", default="skewcal_plate.stl")
    p.add_argument("--n", type=int, default=9)
    p.add_argument("--pitch", type=float, default=12.5)
    p.add_argument("--hole-d", type=float, default=4.0)
    p.add_argument("--marker-d", type=float, default=2.0)
    p.add_argument("--thickness", type=float, default=1.6)

    p = sub.add_parser("sheet", help="generate the phone reference sheet (PDF)")
    p.add_argument("--page", choices=["letter", "a4"], default="letter")
    p.add_argument("--dark", action="store_true", help="black window, for light-coloured plates")
    p.add_argument("-o", "--output")

    for name in ("scan", "photo"):
        p = sub.add_parser(name, help=f"analyse {name}s")
        p.add_argument("images", nargs="+")
        p.add_argument("--plate", help="plate .json written by 'plate' (if not default)")
        p.add_argument("--out", help="output folder (default: next to the first image)")
        p.add_argument("--no-annotate", action="store_true")
        if name == "scan":
            p.add_argument("--dpi", type=float, help="scan resolution if not stored in the file")
        else:
            p.add_argument("--span-x", type=float, help=f"measured top-row span (nominal {sheet_mod.SPAN_X_NOMINAL:.0f} mm)")
            p.add_argument("--span-y", type=float, help=f"measured left-column span (nominal {sheet_mod.SPAN_Y_NOMINAL:.0f} mm)")
            p.add_argument("--focal-px", type=float, help="focal length in pixels (default: from EXIF)")
            p.add_argument("--hole-height", type=float, help="effective feature height above sheet (default thickness/2)")
            p.add_argument("--no-distortion", action="store_true", help="skip lens-distortion fit")
            p.add_argument("--no-warp", action="store_true", help="assume the plate is perfectly flat")

    a = ap.parse_args(argv)
    if a.cmd == "plate":
        cmd_plate(a)
    elif a.cmd == "sheet":
        cmd_sheet(a)
    else:
        return _run(a, a.cmd)


if __name__ == "__main__":
    main()
