# skewcal — measure printer skew with a scanner or phone camera

Print a flat plate with a 9×9 grid of holes. Scan or photograph it, and skewcal finds every hole centre to a few micrometres. It then fits the grid to get:

- the **XY angle**: the true angle between your X and Y axes, where 90° is square
- the **X / Y scale** (how far apart the hole centres really are compared with how far apart they should be)
- a ready-to-paste **PrusaSlicer post-processing line** that corrects the skew

Hole *centres* stay put when holes print too small or too large. That makes the result insensitive to flow, elephant's foot and over-extrusion, which is the weak point of caliper-based tests.

## Use it in your browser

**https://alighomash.github.io/skewcal/** runs the same analysis with nothing to install. It runs on a phone too. Images are processed on your device and never uploaded. The page walks through printing the plate, scanning it, reading the results and correcting your G-code. It opens on an example result from a Prusa MINI+.

The rest of this README covers the command-line version.

## 1. Install (once)

```bash
cd "Skew Correction"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# optional, lets it read iPhone .HEIC photos directly:
.venv/bin/pip install pillow-heif
```

The G-code post-processor (`skew_gcode.py`) uses only the Python standard library and needs none of this.

## 2. Print the test plate

`skewcal_plate.stl` is included (114 × 114 × 1.6 mm, 4 mm holes on a 12.5 mm grid). Its holes are **thin-rim** holes: 4 mm only for the bottom 0.6 mm, then widening as a cone to 5.6 mm at the top. The visible hole edge is a thin first-layer rim, so lighting and viewing angle barely move it. In tests this halved the per-hole error in phone photos, and scans were no worse. To regenerate it or change its size:

```bash
.venv/bin/python skewcal.py plate -o skewcal_plate.stl
```

`skewcal_plate_standard.stl` (`plate --standard`) is the original plate with straight holes. To analyse it, pass `--plate skewcal_plate_standard.json`; otherwise the thin-rim plate is assumed. Plate `.json` files saved before thin-rim plates existed are still read as standard plates.

- **Do not rotate or mirror it in the slicer.** The small marker holes tell the software which way is X and which is Y on the plate. That only matches the printer's X and Y if the plate is printed exactly as exported. The origin hole is at the front left.
- Use your normal settings. Leave out a brim: it would make the edges hard to separate from the background.
- Dark filament is easiest because the holes show up bright. Light filament also works if there's something dark behind the holes.
- Let it cool fully on the sheet before you remove it. A warped plate gives poor results.

## 3a. Measure with a flatbed scanner (most accurate)

1. Lay the plate **face-down** (first layer on the glass), roughly straight. Use dark filament with the lid closed, or light filament with black paper on top of it.
2. Scan at **600 dpi**, grayscale, as PNG or TIFF. Turn off sharpening and descreening.
3. **Turn the plate 90°** and scan again. Doing it twice cancels the scanner's own squareness error (see "Why two images").
4. Run:

```bash
.venv/bin/python skewcal.py scan scan1.png scan2.png            # add --dpi 600 if the file lacks DPI info
```

## 3b. Measure with a phone

**Best setup:** the thin-rim plate, a tablet as the reference, the 2× lens from 40–50 cm, and four photos with the plate turned 90° each time.

- **Thin-rim plate** (the default `skewcal_plate.stl`). Lay it first-layer side down. If it lies upside down, the analysis notices (the hole pattern appears mirrored) and corrects for the rims being near the top.
- **Tablet reference.** In the web app, open *Scan or photograph → Tablet as the reference* on an iPad (11-inch or larger). It draws a compact marker ring full screen at exact size. Pixel pitch is exact, so no ruler check is needed, and glass is flatter than paper. The analysis recognises the screen markers automatically and allows for the cover glass the plate lies on. Apple's published ppi is rounded, so absolute scale can be off by up to about 0.3%. Measure the on-screen 100 mm bar with calipers and pass it as `--screen-bar` (or enter it in the app). The iPad Pro 11-inch (2018–2022) is already calibrated in the app at 264.8 ppi.
- **Four photos** (plate at 0°, 90°, 180°, 270°). The report counts positions, and with more than two images the uncertainty comes from the real scatter.
- **Bowed-plate correction** is automatic. It is used only when the photos show a significant bow (more than 0.5 mm, and more than 3× its uncertainty), because on a flat plate it only adds noise. Use `--warp` or `--no-warp` to force it.

With the paper sheet instead:

1. Print the reference sheet `skewcal_reference_letter.pdf` (or `_a4`) at **100% / Actual size**. For a light-coloured plate, use the `_dark` version.
2. Measure the two spans marked with arrows on the printed sheet, using calipers or a steel rule (not the printed 100 mm ruler, which comes from the same printer):
   - **SPAN X:** outer edge to outer edge of the **top row** of black squares (nominal 174 mm).
   - **SPAN Y:** outer edge to outer edge of the **left column** (nominal 234 mm).

   For a ruler, stand it on edge and start at the 10 mm mark. If your calipers are too short, or you want to cancel toner spread, measure like edges instead (left edge to left edge, nominal 160 mm; top edge to top edge, nominal 220 mm) and scale: `span_x = L × 174/160`, `span_y = L × 234/220`.
3. Tape the sheet to a flat, hard table, not a book or a mat. Lay the plate in the window.
4. Photograph from about 40–50 cm, using the **2× or 3× lens** if you have one. Avoid the 1× lens up close: its leftover lens distortion makes a flat plate look bowled by several millimetres, which throws off the scale. Keep lamps out of the plate's reflection; bright glare bands on the plate are handled, but they add noise. The whole sheet should be in frame, with soft even light, no flash, and no shadow of the phone. A slight tilt is fine. Close up with the 1× lens, every bit of plate or paper height shows up as a position error.
5. **Turn the plate 90° on the sheet** and take another photo. Turning the phone, or the sheet and plate together, does not count: the plate has to change orientation *relative to the sheet's markers*. Two to four photos are best.
6. Use the **original files**. AirDrop them or copy them from Photos into the folder. Images pasted into a chat are shrunk to about 2000 px and lose the EXIF focal length, which costs accuracy.
7. Run:

```bash
.venv/bin/python skewcal.py photo IMG_0001.jpg IMG_0002.jpg --span-x 173.9 --span-y 233.8
```

If you leave out `--span-x/--span-y`, the angle result is unaffected, but absolute scale assumes the sheet printed at exactly 100%. The tool corrects perspective, lens distortion, ink spread, and the small parallax from the plate's thickness, using the focal length the iPhone stores in the photo's EXIF data.

A plate that is not perfectly flat (corners curled up, or slightly cupped) makes the corner holes look magnified in a photo. Photo mode models this as a bowl or saddle and reports it per image, e.g. `[plate corners vs centre: +1.2 +1.0 +1.0 +1.2 mm]`. Warp mainly corrupts the **scale**; the angle is much less sensitive. If you see more than about 0.5 mm, check the plate on a flat surface. Reprint it, or lay a sheet of picture-frame glass on top of the plate and sheet to press them flat (the glass shifts the plate and the markers equally, so it doesn't bias the result). `--no-warp` turns the model off.

The three small marker holes only need to be partly open. If they're too closed-up to segment, skewcal indexes the grid from the large holes and picks the orientation by checking where the marker holes should be.

## 4. Read the result

```
XY angle:  90.2510 deg  (+/- 0.0010)
Skew:      +0.2510 deg  ->  X drifts 0.438 mm per 100 mm of Y travel
Reference bias removed by 90-deg reversal: -0.0461 deg
Scale:     X 99.651%   Y 100.250%
To correct, add this PrusaSlicer post-processing script ...
  /usr/bin/python3 ".../skew_gcode.py" --xy-angle 90.2510;
```

Each image also gets a `*_skewcal.jpg`. It shows the detected holes, the X/Y axes it identified, and the fit residuals magnified 100×. If one hole has a long arrow, that hole is defective; it is excluded from the fit automatically. A full `skewcal_report.json` is also written.

How to read it:

- **Skew** below about 0.05° is barely measurable in real parts.
- **Mechanical skew** on the MINI usually means the Y-axis rail or bed isn't square to the X gantry. Check that first, and use the software correction for what's left.
- **Scale** is the true spacing of features (steps/mm, belts, and the plate's thermal shrinkage). It is separate from the hole-size or XY compensation settings. You can compensate with the slicer's object scale, or with `--scale-x/--scale-y` in the post-processor.

## 5. Apply the correction in PrusaSlicer

In **Print Settings → Output options → Post-processing scripts**, add the line skewcal printed, e.g.:

```
/usr/bin/python3 "/Users/<you>/Workspaces/Source/3D Printer/Skew Correction/skew_gcode.py" --xy-angle 90.2510;
```

- The script pre-distorts every XY move so the printer's skew cancels out. The pivot is the bed centre (90, 90), so parts move by well under a millimetre.
- Arcs (G2/G3 from Arc fitting) are converted into short line segments, because a skewed arc is no longer a circle.
- If the slicer profile outputs **binary G-code**, the script stops with a message. Turn it off in Printer Settings → General ("Supports binary G-code").
- The slicer's preview still shows the uncorrected paths; only the exported file changes.
- Check the fix: print the plate again with the script enabled and measure it. The XY angle should now be within about ±0.02° of 90.

## Why two images (reversal)

Neither a scanner nor a sheet from a paper printer is perfectly square. Turning the plate 90° flips the sign of that reference error but not the plate's own skew. Combining the two shots separates them; the report prints the reference bias it removed. A single image works, but you get a warning that the result includes the reference error.

## Accuracy

Tests on synthetic images with known skew, rendered with noise, blur, JPEG compression, perspective tilt, lens distortion, a skewed paper sheet and a mirrored scan, recover the angle to within about 0.001–0.002° and the scale to within 0.005%. Real prints will do worse: rough edges and slight warp typically limit you to around **0.01° (scanner)** or **0.02–0.03° (phone)**. That is still far finer than caliper diagonals, where 0.05 mm of reading error on 100 mm is about 0.03°.

Run the checks yourself:

```bash
.venv/bin/python tests/run_tests.py     # end-to-end, synthetic scans and photos
python3 tests/test_gcode.py             # post-processor geometry check
```

## About the iPhone LiDAR

The LiDAR isn't useful here. Its depth map is about 256 × 192 points with millimetre-level noise. Skew calibration needs to resolve about 0.02 mm across 100 mm, which only the normal camera image can do. Camera tilt, the one thing depth could help with, is already recovered exactly from the reference markers.

## Files

| File | Purpose |
|---|---|
| `skewcal.py` | command-line tool: `plate`, `sheet`, `scan`, `photo` |
| `skewcal/plate.py` | plate geometry and STL writer |
| `skewcal/sheet.py` | ArUco reference-sheet layout and PDF |
| `skewcal/analyze.py` | hole detection, grid fit, rectification, reversal maths |
| `skew_gcode.py` | PrusaSlicer post-processor (standard library only) |
| `skewcal_plate.stl` / `.json` | default test plate and its parameters |
| `skewcal_reference_*.pdf` | phone reference sheets (Letter / A4, white or dark window) |
| `tests/` | synthetic-image and G-code tests |
| `web/`, `tools/build_web.py` | browser app (Python in Pyodide); `python3 tools/serve_web.py` runs it locally |
