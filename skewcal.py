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

from skewcal.plate import PlateSpec, build_cube, build_mesh, check_watertight, write_stl
from skewcal import sheet as sheet_mod


def cmd_plate(a):
    if a.standard:
        rim = top_d = 0.0
    else:
        rim = a.rim or 0.6
        top_d = a.top_d or a.hole_d + 1.6
    spec = PlateSpec(n=a.n, pitch=a.pitch, hole_d=a.hole_d, marker_d=a.marker_d,
                     thickness=a.thickness, rim=rim, top_d=top_d)
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
    if spec.thin_rim:
        print(f"Thin-rim holes: {spec.hole_d} mm for the bottom {spec.rim} mm, widening to {spec.top_d} mm "
              "at the top. Scan or photograph it first-layer side down.")
    print("Print it flat, exactly as oriented in the file (do not rotate it in the slicer).")


def cmd_sheet(a):
    out = a.output or f"skewcal_reference_{a.page}{'_dark' if a.dark else ''}.pdf"
    sheet_mod.save_pdf(out, a.page, a.dark)
    print(f"Wrote {out}. Print at 100% / Actual size (no 'fit to page').")
    print(f"Then measure the two spans marked with arrows on the sheet: SPAN X, the top-row outer edges "
          f"(nominal {sheet_mod.SPAN_X_NOMINAL:.0f} mm), and SPAN Y, the left-column outer edges "
          f"(nominal {sheet_mod.SPAN_Y_NOMINAL:.0f} mm). Pass them as --span-x / --span-y.")


def cmd_cube(a):
    V, F, L = build_cube()
    if not check_watertight(F):
        sys.exit("internal error: cube mesh not watertight")
    write_stl(a.output, V, F, name="skewcal_cube")
    js = os.path.splitext(a.output)[0] + "_wall.json"
    PlateSpec.cube_wall().to_json(js)
    print(f"Cube {L['S']:.1f} x {L['S']:.1f} x {L['W']:.1f} mm: four {L['W']:.0f} mm walls and the "
          f"{L['floor']:.0f} mm floor plate ({L['floor_clearance']:.1f} mm clearance). Wrote {a.output} and {js}")
    print("Print it as exported (front wall at the front, do not rotate), with the skew correction off.")
    print("Snap the walls apart at the corner tabs. Each piece is labelled on its outer face (FRONT XZ,")
    print("BACK XZ, LEFT YZ, RIGHT YZ, FLOOR XY, arrow = up as printed); lay every piece letters-up.")
    print("Front/back walls measure X-Z, left/right walls Y-Z, the floor plate X-Y:")
    print(f"  skewcal.py scan F1.png F2.png B1.png B2.png --plate {js} --plane xz")


def _load_spec(a):
    return PlateSpec.from_json(a.plate) if a.plate else PlateSpec()


PLANES = {"xy": ("X", "Y"), "xz": ("X", "Z"), "yz": ("Y", "Z")}


def format_report(results, names, combined, spec, mode, script_path=None, plane="xy"):
    """The human-readable report (also used by the web app). plane: which printer
    axes the plate's x/y grid directions are (xy for the flat plate, xz / yz for
    the cube's walls)."""
    A, B = PLANES[plane]
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
                          + " ".join(f"{z:+.1f}" for z in r["warp_corners_mm"]) + " mm"
                          + ("" if r.get("warp_used", True) else ", not significant: bow model off") + "]")
        P(f"  {n}: {A}{B} angle {r['theta']:.4f} +/- {r['theta_std']:.4f} deg | scale {A} {100*r['scale_x']:.3f}%"
          f" {B} {100*r['scale_y']:.3f}% | {r['n_holes']}/{r['n_expected']} holes, fit rms {r['rms_um']:.1f} um"
          f" | {orient}{', mirrored' if r['mirrored'] else ''}{extra}")
    th, sd = combined["theta"], combined["theta_std"]
    skew = th - 90.0
    per100 = 100 * math.tan(math.radians(skew))
    P("")
    P("=" * 72)
    P(f"{A}{B} angle:  {th:.4f} deg  (+/- {sd:.4f})")
    P(f"Skew:      {skew:+.4f} deg  ->  {A} drifts {abs(per100):.3f} mm per 100 mm of {B} travel")
    side = "-" if skew > 0 else "+"
    if skew and B == "Z":
        P(f"           (the Z axis leans toward {side}{A}: tall parts lean toward {side}{A} as they rise)")
    elif skew:
        P(f"           (the {B} axis leans toward {side}{A}: corners at +{A}+{B} / -{A}-{B} are "
          f"{'obtuse' if skew > 0 else 'acute'})")
    na = sum(1 for r in results if r["frame_sign"] > 0)
    nb = len(results) - na
    P(f"Positions: {na} with the plate at 0/180 deg, {nb} at 90/270 deg (relative to each other)")
    if combined["reversal"] and len(results) < 4:
        P("           Tip: two more images, with the plate turned another 90 deg each time, average out")
        P("           image-to-image noise and give an honest uncertainty from the scatter.")
    if combined["reversal"]:
        P(f"Reference bias removed by 90-deg reversal: {combined['frame_bias_deg']:+.4f} deg")
    else:
        P("WARNING: all images had the plate in the same orientation, so the scanner/sheet's own")
        P("         squareness error is NOT cancelled. Add an image with the plate turned 90 deg.")
    sx, sy = combined["scale_x"], combined["scale_y"]
    P(f"Scale:     {A} {100*sx:.3f}%   {B} {100*sy:.3f}%   (hole-centre spacing, measured/nominal)")
    if mode == "photo" and results[0].get("reference") == "screen" and results[0].get("sheet_scale", (1, 1))[0] == 1:
        P("           (tablet reference: scale assumes the screen's published ppi is exact, often only")
        P("            to ~0.3%; measure its 100 mm bar with calipers and pass --screen-bar)")
    elif mode == "photo" and tuple(results[0].get("sheet_scale", (1.0, 1.0))) == (1.0, 1.0):
        P("           (absolute scale assumes the sheet printed at exactly 100%; pass --span-x/--span-y")
        P("            with your ruler/caliper measurements to make it exact)")
    P(f"           -> slicer object scale {A} {100/sx:.2f}%  {B} {100/sy:.2f}% (only if you want to compensate)")
    hd = [r["hole_d_mean"] for r in results if r.get("hole_d_mean")]
    if hd and mode == "scan":
        P(f"Holes:     mean diameter {sum(hd)/len(hd):.3f} mm vs nominal {spec.hole_d} mm (first-layer side)")
    P("")
    if abs(skew) < 0.02:
        P("Skew is negligible (< 0.02 deg); no correction needed.")
    else:
        P("To correct, add this PrusaSlicer post-processing script (Print Settings > Output options):")
        P(f'  /usr/bin/python3 "{script_path}" --{plane}-angle {th:.4f};')
        if plane != "xy":
            P("  (combine with your --xy-angle and the other wall's angle in one line)")
    P("=" * 72)
    return "\n".join(lines)


def _report(results, names, combined, spec, outdir, mode, plane="xy"):
    text = format_report(results, names, combined, spec, mode, plane=plane)
    print(text)
    with open(os.path.join(outdir, "skewcal_report.txt"), "w") as f:
        f.write(text + "\n")
    with open(os.path.join(outdir, "skewcal_report.json"), "w") as f:
        json.dump({"plane": plane, "combined": combined, "images": dict(zip(names, results)),
                   "plate": spec.__dict__}, f, indent=2, default=float)


def _run(a, mode):
    from skewcal import analyze as an
    spec = _load_spec(a)
    if spec.wall and a.plane == "xy":
        sys.exit("These are cube walls: pass --plane xz for the front/back walls (one small hole in the "
                 "top border) or --plane yz for the left/right walls (two holes).")
    if a.plane != "xy" and not spec.wall:
        sys.exit(f"--plane {a.plane} is for the cube walls; pass --plate skewcal_cube_wall.json.")
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
                                     correct_warp=False if a.no_warp else True if a.warp else "auto",
                                     screen_bar=a.screen_bar)
        except RuntimeError as e:
            print(f"  {os.path.basename(p)}: FAILED - {e}", file=sys.stderr)
            continue
        results.append(r); names.append(os.path.basename(p))
    if not results:
        sys.exit("No image could be analysed.")
    combined = an.combine(results)
    _report(results, names, combined, spec, outdir, mode, a.plane)
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
    p.add_argument("--standard", action="store_true",
                   help="straight holes (the original plate) instead of thin-rim holes")
    p.add_argument("--thin-rim", action="store_true", help=argparse.SUPPRESS)   # now the default
    p.add_argument("--rim", type=float, default=0.0, help="thin-rim height in mm (default 0.6)")
    p.add_argument("--top-d", type=float, default=0.0, help="cone diameter at the top surface (default hole_d + 1.6)")

    p = sub.add_parser("cube", help="generate the breakaway cube (X-Z, Y-Z walls + XY floor plate)")
    p.add_argument("-o", "--output", default="skewcal_cube.stl")

    p = sub.add_parser("sheet", help="generate the phone reference sheet (PDF)")
    p.add_argument("--page", choices=["letter", "a4"], default="letter")
    p.add_argument("--dark", action="store_true", help="black window, for light-coloured plates")
    p.add_argument("-o", "--output")

    for name in ("scan", "photo"):
        p = sub.add_parser(name, help=f"analyse {name}s")
        p.add_argument("images", nargs="+")
        p.add_argument("--plate", help="plate .json written by 'plate' (default: thin-rim plate; "
                                       "skewcal_plate_standard.json for the straight-hole plate)")
        p.add_argument("--out", help="output folder (default: next to the first image)")
        p.add_argument("--no-annotate", action="store_true")
        p.add_argument("--plane", choices=sorted(PLANES), default="xy",
                       help="xy for the flat plate / cube floor, xz for the cube's front/back walls, "
                            "yz for its left/right walls")
        if name == "scan":
            p.add_argument("--dpi", type=float, help="scan resolution if not stored in the file")
        else:
            p.add_argument("--span-x", type=float, help=f"measured top-row span (nominal {sheet_mod.SPAN_X_NOMINAL:.0f} mm)")
            p.add_argument("--span-y", type=float, help=f"measured left-column span (nominal {sheet_mod.SPAN_Y_NOMINAL:.0f} mm)")
            p.add_argument("--screen-bar", type=float,
                           help="tablet reference: measured length of its on-screen 100 mm bar (mm)")
            p.add_argument("--focal-px", type=float, help="focal length in pixels (default: from EXIF)")
            p.add_argument("--hole-height", type=float, help="effective feature height above sheet (default thickness/2)")
            p.add_argument("--no-distortion", action="store_true", help="skip lens-distortion fit")
            p.add_argument("--no-warp", action="store_true", help="assume the plate is perfectly flat")
            p.add_argument("--warp", action="store_true",
                           help="always fit the bowed-plate model (default: only when the bow is significant)")

    a = ap.parse_args(argv)
    if a.cmd == "plate":
        cmd_plate(a)
    elif a.cmd == "cube":
        cmd_cube(a)
    elif a.cmd == "sheet":
        cmd_sheet(a)
    else:
        return _run(a, a.cmd)


if __name__ == "__main__":
    main()
