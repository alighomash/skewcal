# skewcal — measure printer skew with a scanner or phone camera

Print a flat plate with a 9×9 grid of holes. Scan or photograph it, and skewcal finds every hole centre to a few micrometres. It then fits the grid to get:

- the **XY angle**: the true angle between your X and Y axes, where 90° is square
- the **X / Y scale** (how far apart the hole centres really are compared with how far apart they should be)
- a ready-to-paste **PrusaSlicer post-processing line** that corrects the skew

Hole *centres* stay put when holes print too small or too large. That makes the result insensitive to flow, elephant's foot and over-extrusion, which is the weak point of caliper-based tests.

## Use it in your browser

**https://alighomash.github.io/skewcal/** runs the same analysis with nothing to install. It runs on a phone too. Images are processed on your device and never uploaded. The page walks through printing the plate, scanning or photographing it, reading the results and correcting your G-code. On an iPad it also shows the tablet reference (section 3b). It opens on an example result from a Prusa MINI+.

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

## 3. Choose how to capture the plate

| Method | Angle | Scale | Needs |
|---|---|---|---|
| **3a. Flatbed scanner** | ±0.006° | exact, no calibration | a scanner |
| **3b. Phone + tablet** | ±0.003° | exact after a one-time 100 mm bar check | an 11-inch+ iPad, a phone with a 2× lens |
| 3c. Phone + paper sheet | ±0.01° | needs measured sheet spans | a printer for the sheet |

The angle column is the combined result from two images with the plate turned 90°, measured on a Prusa MINI+. Take more images if you can: four are better than two.

## 3a. Measure with a flatbed scanner

1. Lay the plate **face-down** (first layer on the glass), roughly straight. Use dark filament with the lid closed, or light filament with black paper on top of it.
2. Scan at **600 dpi**, grayscale, as PNG or TIFF. Turn off sharpening and descreening.
3. **Turn the plate 90°** and scan again. Doing it twice cancels the scanner's own squareness error (see "Why two images"). Optionally scan at 180° and 270° too; four scans average out more noise.
4. Run:

```bash
.venv/bin/python skewcal.py scan scan1.png scan2.png            # add --dpi 600 if the file lacks DPI info
```

## 3b. Measure with a phone and a tablet (recommended phone setup)

An iPad shows the reference markers instead of a printed sheet. A screen is perfectly flat and its pixels are evenly spaced. You don't need to print a sheet, and absolute scale needs at most a one-time calipers check of a bar shown on the screen. On a test plate it matched a flatbed scan of the same plate to 0.0015° in angle and 0.01% in scale (see [Accuracy](#accuracy)).

You need:
- the thin-rim plate (the default `skewcal_plate.stl`)
- an iPad with a screen at least 15 cm across the short side: 11-inch or larger (an iPad mini is too small)
- a phone with a 2× or 3× lens

1. **Print the plate.** To measure your printer, slice it *without* the skew correction. To check a correction, slice it *with* the correction.
2. **Open the reference on the iPad.** In Safari, go to the [web app](https://alighomash.github.io/skewcal/), open **Scan or photograph → Tablet as the reference**, and check the model it selected.
   - The iPad Pro 11-inch (2018–2022) is calibrated at 264.8 ppi.
   - Other models use Apple's published 264 ppi, which is rounded. Calibrate them once in step 4.
3. **Prepare the screen.** Turn brightness up and auto-lock off, wipe the screen, and don't zoom the page. Tap **Show reference on this screen**. The marker ring fills the screen, with a 100 mm bar beside it.
4. **Calibrate the scale (once per iPad model).** Measure the 100 mm bar with calipers.
   - **About 100.0 mm:** nothing to do.
   - **Anything else, e.g. 99.7 mm:** enter it as *Tablet's 100 mm bar* in the Measure tab, or pass `--screen-bar 99.7`. This only affects absolute scale; the skew angle doesn't need it.
5. **Lay the iPad flat** on a table and put the plate inside the window, between the corner ticks, **first-layer side down**. Upside down still works: the analysis detects it and corrects for it.
6. **Photograph straight down** with the 2× or 3× lens from 30–50 cm.
   - Keep the whole marker ring in frame.
   - Don't let lamps reflect in the screen.
   - Don't use the 1× lens up close. Its leftover distortion makes a flat plate look bowled and throws off the scale.
7. **Turn the plate 90° on the screen** and take another photo. Repeat until you have four: plate at 0°, 90°, 180° and 270°. Turning the phone or the iPad doesn't count; the plate has to turn *relative to the markers*.
8. **Measure.**
   - **In the web app:** Measure tab → **Phone** → plate **Thin-rim** → add the original photos.
   - **On the command line:**

     ```bash
     .venv/bin/python skewcal.py photo IMG_1.HEIC IMG_2.HEIC IMG_3.HEIC IMG_4.HEIC [--screen-bar 99.7]
     ```

The analysis recognises the screen markers by their ids, so there is no separate tablet mode. Each image line in the report shows `40 markers` and a screen fit of about 30 µm.

Use the original photo files. AirDrop them, or copy them from Photos. Images sent through a chat app are shrunk to about 2000 px and lose the focal length the analysis uses.

## 3c. Measure with a phone and the paper sheet

Use this if you don't have a suitable tablet.

1. Print the reference sheet `skewcal_reference_letter.pdf` (or `_a4`) at **100% / Actual size**. For a light-coloured plate, use the `_dark` version.
2. Measure the two spans marked with arrows on the printed sheet. Use calipers or a steel rule, not the printed 100 mm ruler, which comes from the same printer.
   - **SPAN X:** outer edge to outer edge of the **top row** of black squares (nominal 174 mm).
   - **SPAN Y:** outer edge to outer edge of the **left column** (nominal 234 mm).

   For a ruler, stand it on edge and start at the 10 mm mark. If your calipers are too short, or you want to cancel toner spread, measure like edges instead and scale:
   - left edge to left edge (nominal 160 mm): `span_x = L × 174/160`
   - top edge to top edge (nominal 220 mm): `span_y = L × 234/220`
3. Tape the sheet to a flat, hard table, not a book or a mat. Lay the plate in the window, first-layer side down.
4. Photograph from 40–50 cm with the **2× or 3× lens**.
   - Keep the whole sheet in frame.
   - Use soft, even light, with no flash and no shadow of the phone.
   - Keep lamps out of the plate's reflection. Bright glare bands on a shiny plate are handled, but they add noise.
   - A slight tilt is fine.
5. **Turn the plate 90° on the sheet** and take another photo. Four photos (0°, 90°, 180°, 270°) are best.
6. Run:

```bash
.venv/bin/python skewcal.py photo IMG_0001.jpg IMG_0002.jpg --span-x 173.9 --span-y 233.8
```

Without `--span-x/--span-y`, the angle is unaffected, but absolute scale assumes the sheet printed at exactly 100%.

## Notes for both phone setups

- **What's corrected automatically:** perspective, lens distortion, ink spread or marker bloom, and the parallax from the hole edges sitting above the reference. On a screen, the plate lies on the cover glass, about 0.7 mm above the pixels; that's included too. The focal length comes from the photo's EXIF data.
- **Bowed plates:** if a plate doesn't lie flat, the corner holes look magnified. The analysis fits a bowl or saddle shape and reports it per image, e.g. `[plate corners vs centre: +1.2 +1.0 +1.0 +1.2 mm]`.
  - The fit is kept only when the bow is significant: more than 0.5 mm, and more than 3× its uncertainty. On a flat plate it only adds noise. `--warp` and `--no-warp` force it on or off.
  - A bow mostly affects scale; the angle is much less sensitive.
  - If one corner lifts, lay a sheet of clear glass over the plate *and* the markers. The glass shifts both equally, so it doesn't bias the result.
- **Marker holes:** the three small holes are 2.4 mm (2.0 mm on plates made before 14 Oct 2026) and only need to be partly open. If they're too closed-up to find, skewcal indexes the grid from the large holes alone. It then compares the brightness at the marker positions of every corner with the plain positions, and accepts a corner only if it stands out clearly; otherwise it stops rather than guess. A wrong corner would flip the sign of the skew.

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

## 6. Measure X–Z and Y–Z skew (breakaway cube)

XY skew is how far X and Y are from square. A leaning Z axis gives **X–Z** and **Y–Z** skew instead: tall parts lean as they rise. On a MINI+ this is the single Z column not being exactly vertical.

The breakaway cube measures all three angles in one print. It is `skewcal_cube.stl`, or `skewcal.py cube`. It is 120 × 120 × 118 mm, about 125 g of PLA, and a few hours of printing.

- **Four upright walls** carry the same 9 × 9 hole grid. Front and back walls measure X–Z; left and right walls measure Y–Z. The walls are joined at the corners by small tabs, so they print without wobbling.
- **The floor** is the normal thin-rim plate, lying loose inside the walls. It measures X–Y as usual.
- **Raised labels** on each piece's outer face say what it is and which way is up: `FRONT XZ`, `BACK XZ`, `LEFT YZ`, `RIGHT YZ` and `FLOOR XY`. The arrow points to the top as printed; on the floor it points to the back (+Y). The walls also have one or two small holes in their top border as a second cue.

1. **Print it as exported** (don't rotate it), with the skew correction **off**.
2. **Snap the walls apart** at the corner tabs. The small nubs left on the edges don't matter.
3. **Scan or photograph each wall** lying flat, exactly like the plate: scanner, tablet or paper sheet. Take two images per wall, turned 90° between them.
   - **Lay every piece letters-up.** The raised letters must not touch the glass or screen. Letters-up also means you never flip a wall by mistake: flipping and turning together doesn't cancel the scanner's own error.
4. **Measure each pair of walls together**, with the wall plate file and the plane:

```bash
.venv/bin/python skewcal.py scan front1.png front2.png back1.png back2.png --plate skewcal_cube_wall.json --plane xz
.venv/bin/python skewcal.py scan left1.png left2.png right1.png right2.png --plate skewcal_cube_wall.json --plane yz
.venv/bin/python skewcal.py scan floor1.png floor2.png                       # the floor plate: X-Y
```

   In the web app, choose **Plate: Cube wall** and the walls (front/back or left/right).

The report then says "XZ angle" or "YZ angle". Its second scale is the **Z scale**, which reflects Z steps/mm and layer height accuracy. Because two walls measure each angle, the scatter between their images is an honest check: they should agree within about 0.01°.

5. **Correct all three in one post-processing line:**

```
/usr/bin/python3 "/path/to/skew_gcode.py" --xy-angle 89.7671 --xz-angle 89.8200 --yz-angle 90.1200;
```

The script shifts every X/Y coordinate by the measured lean times its height, so the first layer stays where it is. Moves that change only Z get the matching small X/Y shift. Z values themselves are not changed.

The holes in the walls are printed sideways, so their tops are bridged and sag slightly. Every hole sags the same way, which only shifts the grid, and the fit ignores a shift. The tests include a 0.25 mm sag.

## Why two images (reversal)

Neither a scanner nor a sheet from a paper printer is perfectly square. Turning the plate 90° flips the sign of that reference error but not the plate's own skew. Combining the two shots separates them; the report prints the reference bias it removed. A single image works, but you get a warning that the result includes the reference error.

## Accuracy

On synthetic images with known skew, the tests recover the angle to about 0.001–0.002° and the scale to about 0.005%. The images include noise, blur, JPEG compression, perspective tilt, lens distortion, a skewed paper sheet, glare, a bowed plate and a mirrored scan.

Real results from one Prusa MINI+. Each setup used two images with the plate turned 90°. Each row is compared with a flatbed scan of the same plate:

| Setup | XY angle vs scan | ± per image | Hole residual | Scale vs scan |
|---|---|---|---|---|
| Flatbed scan, 600 dpi | (reference) | 0.009° | 42–44 µm | (reference) |
| **iPad Pro 11 + thin-rim plate, 2× lens, 30 cm** | **−0.0015°** | **0.004°** | **22–24 µm** | **0.01%** (bar calibrated) |
| Paper sheet + standard plate, 2× lens, 45 cm | +0.016° | 0.011° | 47–64 µm | +0.1% |
| Paper sheet + standard plate, 1× lens, 22 cm | 0.000° | 0.011° | 33 µm | −1.7% (lens distortion) |

All of these are far finer than caliper diagonals, where 0.05 mm of reading error on 100 mm is about 0.03°. Calipers did agree with the scanner's scale within about 0.1%.

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
| `skewcal/sheet.py` | ArUco reference layouts: the paper sheet (PDF) and the tablet screen |
| `skewcal/analyze.py` | hole detection, grid fit, rectification, reversal maths |
| `skew_gcode.py` | PrusaSlicer post-processor for XY, XZ and YZ skew (standard library only) |
| `skewcal_plate.stl` / `.json` | default (thin-rim) test plate and its parameters |
| `skewcal_plate_standard.stl` / `.json` | original plate with straight holes (`--plate skewcal_plate_standard.json`) |
| `skewcal_cube.stl`, `skewcal_cube_wall.json` | breakaway cube for X–Z / Y–Z (+ XY floor) and its wall plate parameters |
| `skewcal_reference_*.pdf` | phone reference sheets (Letter / A4, white or dark window) |
| `tests/` | synthetic-image and G-code tests |
| `web/`, `tools/build_web.py` | browser app (Python in Pyodide); `python3 tools/serve_web.py` runs it locally |
