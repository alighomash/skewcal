"""Paper reference sheet for the phone-camera workflow.

A ring of ArUco markers surrounds an empty window where the test plate is laid.
Sheet coordinates are millimetres, origin at the page centre, +x right, +y up.
The layout fits on both US Letter and A4.
"""
from __future__ import annotations

import cv2
import numpy as np

ARUCO_DICT = cv2.aruco.DICT_4X4_100
MARKER_SIZE = 14.0       # mm
MARKER_PITCH = 20.0      # mm
WINDOW_HALF = 70.0       # plate window is 140 x 140 mm
COLS = [-80 + 20 * k for k in range(9)]            # -80 .. 80
ROWS = [120, 100, 80, 60, 40, 20, 0, -20, -40, -60, -80, -100]

PAGES = {"letter": (215.9, 279.4), "a4": (210.0, 297.0)}

# Distances the user can check with calipers / a ruler after printing
SPAN_X_NOMINAL = (COLS[-1] - COLS[0]) + MARKER_SIZE   # outer edges, top row: 174 mm
SPAN_Y_NOMINAL = (ROWS[0] - ROWS[-1]) + MARKER_SIZE   # outer edges, left column: 234 mm


def marker_layout():
    """List of (id, centre_x, centre_y) for all markers outside the window."""
    out = []
    mid = 0
    lim = WINDOW_HALF + MARKER_SIZE / 2
    for y in ROWS:
        for x in COLS:
            if abs(x) < lim and abs(y) < lim:
                continue
            out.append((mid, float(x), float(y)))
            mid += 1
    return out


def marker_corners(scale_x=1.0, scale_y=1.0):
    """dict id -> (4,2) corners in sheet mm, in OpenCV order (TL, TR, BR, BL as seen
    on the page). Optional scale factors correct for a printer that did not print
    at exactly 100%."""
    h = MARKER_SIZE / 2
    d = {}
    for mid, x, y in marker_layout():
        c = np.array([[x - h, y + h], [x + h, y + h], [x + h, y - h], [x - h, y - h]])
        c[:, 0] *= scale_x
        c[:, 1] *= scale_y
        d[mid] = c
    return d


def render_sheet(dpmm=600 / 25.4, page="letter", dark_window=False, text=True):
    """Render the full page as a uint8 image (white=255)."""
    pw, ph = PAGES[page]
    W, H = int(round(pw * dpmm)), int(round(ph * dpmm))
    img = np.full((H, W), 255, np.uint8)

    def to_px(x, y):
        return (W / 2 + x * dpmm, H / 2 - y * dpmm)

    dic = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    h = MARKER_SIZE / 2
    for mid, x, y in marker_layout():
        bits = cv2.aruco.generateImageMarker(dic, mid, 6, borderBits=1)
        cell = MARKER_SIZE / 6
        for r in range(6):
            for c in range(6):
                if bits[r, c] == 0:
                    x0, y0 = to_px(x - h + c * cell, y + h - r * cell)
                    x1, y1 = to_px(x - h + (c + 1) * cell, y + h - (r + 1) * cell)
                    img[int(round(y0)):int(round(y1)), int(round(x0)):int(round(x1))] = 0

    if dark_window:
        x0, y0 = to_px(-WINDOW_HALF, WINDOW_HALF)
        x1, y1 = to_px(WINDOW_HALF, -WINDOW_HALF)
        img[int(round(y0)):int(round(y1)), int(round(x0)):int(round(x1))] = 0

    # Corner ticks marking the window (kept outside the plate footprint)
    col = 255 if dark_window else 0
    lw = max(1, int(round(0.3 * dpmm)))
    for sx in (-1, 1):
        for sy in (-1, 1):
            cx, cy = sx * (WINDOW_HALF - 1), sy * (WINDOW_HALF - 1)
            p0 = tuple(int(round(v)) for v in to_px(cx, cy))
            p1 = tuple(int(round(v)) for v in to_px(cx - sx * 6, cy))
            p2 = tuple(int(round(v)) for v in to_px(cx, cy - sy * 6))
            cv2.line(img, p0, p1, col, lw)
            cv2.line(img, p0, p2, col, lw)

    if text:
        font = cv2.FONT_HERSHEY_SIMPLEX
        th = max(1, int(round(0.25 * dpmm)))

        def P(x, y):
            return tuple(int(round(v)) for v in to_px(x, y))

        def label(s, x, y, height, max_w=174.0, vertical=False):
            """Draw s centred on sheet point (x, y); vertical text reads bottom-to-top.
            Returns its length along the reading direction in mm."""
            (tw, tht), _ = cv2.getTextSize(s, font, 1.0, th)
            fs = min(height * dpmm / tht, max_w * dpmm / tw)
            (tw, tht), _ = cv2.getTextSize(s, font, fs, th)
            pad = 2 * th
            tile = np.full((tht + 2 * pad, tw + 2 * pad), 255, np.uint8)
            cv2.putText(tile, s, (pad, tht + pad), font, fs, 0, th, cv2.LINE_AA)
            if vertical:
                tile = np.rot90(tile)
            cx, cy = to_px(x, y)
            r0, c0 = int(round(cy - tile.shape[0] / 2)), int(round(cx - tile.shape[1] / 2))
            region = img[r0:r0 + tile.shape[0], c0:c0 + tile.shape[1]]
            np.minimum(region, tile, out=region)
            return tw / dpmm

        def arrowhead(tip, d):
            """Filled arrowhead at sheet point tip, pointing along unit vector d."""
            n = (-d[1], d[0])
            back = (tip[0] - 2.2 * d[0], tip[1] - 2.2 * d[1])
            pts = [tip, (back[0] + 0.7 * n[0], back[1] + 0.7 * n[1]),
                   (back[0] - 0.7 * n[0], back[1] - 0.7 * n[1])]
            cv2.fillPoly(img, [np.array([P(*p) for p in pts], np.int32)], 0, cv2.LINE_AA)

        # Dimension lines showing exactly what --span-x / --span-y mean: outer edge
        # to outer edge of the marker squares. Extension lines stop 2 mm short of the
        # markers so they never touch a marker's white quiet zone edge.
        gap, ext = 2.0, 1.5
        x_lo, x_hi = COLS[0] - h, COLS[-1] + h            # -87 .. 87
        y_lo, y_hi = ROWS[-1] - h, ROWS[0] + h            # -107 .. 127

        yd = y_hi + gap + 2.5                             # SPAN X, above the top row
        for x in (x_lo, x_hi):
            cv2.line(img, P(x, y_hi + gap), P(x, yd + ext), 0, th)
        s = f"SPAN X = {SPAN_X_NOMINAL:.0f} mm nominal (outer edges, top row)"
        half = label(s, 0, yd, 2.2) / 2 + 1.5
        cv2.line(img, P(x_lo, yd), P(-half, yd), 0, th)
        cv2.line(img, P(half, yd), P(x_hi, yd), 0, th)
        arrowhead((x_lo, yd), (-1, 0))
        arrowhead((x_hi, yd), (1, 0))

        xd = x_lo - gap - 3.0                             # SPAN Y, left of the left column
        for y in (y_lo, y_hi):
            cv2.line(img, P(x_lo - gap, y), P(xd - ext, y), 0, th)
        s = f"SPAN Y = {SPAN_Y_NOMINAL:.0f} mm nominal (outer edges, left column)"
        ym = (y_lo + y_hi) / 2
        half = label(s, xd, ym, 2.2, vertical=True) / 2 + 1.5
        cv2.line(img, P(xd, y_lo), P(xd, ym - half), 0, th)
        cv2.line(img, P(xd, ym + half), P(xd, y_hi), 0, th)
        arrowhead((xd, y_lo), (0, -1))
        arrowhead((xd, y_hi), (0, 1))

        lines = [
            "SKEWCAL PHONE REFERENCE - PRINT AT 100% / ACTUAL SIZE",
            "Measure SPAN X and SPAN Y (arrows) with calipers or a steel rule:",
            "outer edge to outer edge of the black squares. Pass them as --span-x / --span-y.",
        ]
        y = -112.5
        for ln in lines:
            (tw, tht), _ = cv2.getTextSize(ln, font, 1.0, th)
            fs = min(2.6 * dpmm / tht, 174 * dpmm / tw)      # <= 2.6 mm tall, <= 174 mm wide
            cv2.putText(img, ln, P(-87, y), font, fs, 0, th, cv2.LINE_AA)
            y -= 5.5
        # 100 mm ruler with 10 mm ticks: only a quick check that the print is not
        # grossly scaled; it comes from the same printer, so don't measure with it.
        y0 = -130.0
        cv2.line(img, P(-50, y0), P(50, y0), 0, th)
        for k in range(11):
            cv2.line(img, P(-50 + 10 * k, y0), P(-50 + 10 * k, y0 + (3 if k % 5 == 0 else 1.5)), 0, th)
        label("100 mm (print-scale check only)", 73, y0 + 0.5, 1.8, max_w=40)
    return img


def save_pdf(path, page="letter", dark_window=False, dpi=600):
    from PIL import Image
    img = render_sheet(dpi / 25.4, page, dark_window)
    Image.fromarray(img).convert("1").save(path, resolution=dpi)
    return img
