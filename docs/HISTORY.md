# Project history and decisions

This file records why Skewcal is built the way it is and what it has been measured against. The code and README say how it works; this file says how it got there. Dates are 2026.

## Validation on the reference printer (Prusa MINI+)

All angles are the X–Y angle; 90° is square. Raw images and reports are kept locally in `runs/<date>/` (not in the repo, as they are personal photos).

| Date | Method | Result | Notes |
|---|---|---|---|
| 10-13 | Calipers (CaliStar) | 89.769° | After fixing one typo in a reading. Scale X ≈ 99.8–99.96 %, Y ≈ 99.7–99.9 %. |
| 10-13 | Flatbed scan pair, 600 dpi, plate turned 90° | **89.7671° ± 0.006** | Best reference. Scanner's own bias −0.019°, removed by the reversal. Scale X 99.92 %, Y 99.79 %. |
| 10-13 | Phone, 1× lens at 22 cm, paper sheet | 89.768° | Agrees on angle, but scale read 1.6 % low and the plate fitted as 3 mm "cupped". |
| 10-13 | Phone, 2× lens at 45 cm, paper sheet | 89.788 ± 0.008 | Plate fits flat, scale matches the scans. The "cup" at 1× was lens distortion, not a real bow. |
| 10-13 | **Corrected reprint**, scan pair | **90.0002° ± 0.006** | Printed with `skew_gcode.py --xy-angle 89.7671`. Closed loop verified. |
| 10-13 | Thin-rim plate, scan pair | 89.7571 ± 0.006 | Same printer, new plate design. |
| 10-13 | Thin-rim plate, phone on iPad Pro 11" | 89.7556 ± 0.003 | Agrees with the scan to 0.0015°. Scale was +0.30 % until the screen's true pixel density (264.8 ppi, not the published 264) was used. |
| 10-14 | Scan pair after a printer modification | **89.7220 ± 0.0056** | Skew got ~0.04° worse. Current correction: `--xy-angle 89.7220`. |

Breakaway cube (X–Z / Y–Z): tested on synthetic images only. A real print and measurement is still to do.

## Design decisions

- **Hole centres, not edges.** Centres don't move with flow, elephant's foot or hole compensation, so no caliper-style "inner minus outer" tricks are needed.
- **90° reversal is required.** No scanner glass or printed sheet is exactly square; the scanner used here was off by about 0.02°. Turning the plate 90° flips the sign of that error, so it can be solved for. Turning the phone instead of the plate, or flipping *and* turning, does not count as a reversal.
- **Glare-robust detector (10-13).** Real phone photos had lamp glare bands that lost 15–25 holes. Detection now uses local contrast, fills missing holes from the lattice and fits each hole edge with per-ray thresholds. Result: 81/81 holes on the same photos.
- **Bow model is automatic.** Kept only when the corner bow is > 0.5 mm and > 3σ, because a fitted bow mostly shifts scale and the 1× "cup" turned out to be lens distortion.
- **Tablet screen reference.** A phone photo on a tablet gave the lowest uncertainty (±0.003°): the screen is flat, sharp and its pixel grid is exact. Scale needs the true ppi, which is why the app asks for the on-screen 100 mm bar to be measured.
- **Thin-rim plate is the default (10-13).** Holes are 4 mm for the first 0.6 mm and widen above, so the visible edge is the first-layer rim and parallax from the plate thickness is small. It did not reduce scan noise (the scanner dominates there), but helps photos.
- **Marker holes 2.0 → 2.4 mm (10-14).** After the printer modification the holes printed smaller and the 2.0 mm markers nearly closed. Added a contrast-based fallback that identifies the markers only when the choice is clear (ratio ≥ 1.5 and ≥ 1.5× the runner-up), otherwise it stops rather than guessing.
- **Correction methods.** `skew_gcode.py` rewrites G-code for firmware without skew compensation (Prusa). For Klipper, Marlin and RepRapFirmware the tool prints firmware settings instead (10-26). These were checked against each firmware's own transform, taken from its source (`tests/test_firmware.py`).
- **`skew_gcode.py` is standard-library only**, because PrusaSlicer runs it with the system Python.

## Hosting and publishing

- **Web app on GitHub Pages** (https://alighomash.github.io/skewcal/), redeployed by Actions on every push to `main`. It runs the unchanged Python package in Pyodide. claude.ai Artifacts were tried first but refuse the `.whl`/`.zip` files Pyodide needs.
- **Licences:** MIT for the code, CC BY 4.0 for the models (`LICENSE`, `LICENSE-models.md`).
- **Printables:** https://www.printables.com/model/1891628, published 10-26 as beta, with an AI-assistance note. The listing text is maintained by hand on Printables; when editing it, start from the current version, as it may have been changed there.
- **Privacy:** commits use the GitHub noreply address. Photos are published only with EXIF/GPS stripped.

## Open items

- Print the breakaway cube and measure X–Z / Y–Z on real images.
- Results from other printers, scanners and phones (the tool is validated on one MINI+ so far).
- Possibly a standalone Windows build of `skew_gcode.py`.
