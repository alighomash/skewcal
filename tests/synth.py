"""Synthetic scans / phone photos of a skewed test plate, for verifying skewcal."""
import math
import os
import sys

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from skewcal.plate import PlateSpec  # noqa: E402
from skewcal import sheet  # noqa: E402

SHIFT = 6
SC = 1 << SHIFT


def printer_matrix(theta_deg, sx, sy):
    t = math.radians(theta_deg)
    return np.array([[sx, sy * math.cos(t)], [0, sy * math.sin(t)]])


def plate_polys(spec, M, segs=96, marker_scale=1.0, droop=0.0):
    """Physical plate outline and hole polygons (mm), plate centred at origin.
    marker_scale < 1 simulates marker holes that printed partly closed.
    droop (mm) sags the top of every hole, as on a wall printed upright, where
    the holes run sideways and their tops are bridged."""
    centre = np.array([spec.span / 2, spec.span / 2])
    outline = (spec.outline() - centre) @ M.T
    holes = []
    for cx, cy, d in spec.holes():
        if d < spec.hole_d:
            d *= marker_scale
        a = np.arange(segs) * 2 * math.pi / segs
        c = np.c_[cx + d / 2 * np.cos(a), cy + d / 2 * np.sin(a) - droop * np.clip(np.sin(a), 0, None) ** 2] - centre
        holes.append(c @ M.T)
    return outline, holes


def rot2(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


SS = 4   # supersampling factor (no AA drawing: OpenCV's AA fill grows shapes ~0.5 px)


def _fill(img, poly_px, val):
    q = (poly_px + 0.5) * SS - 0.5          # pixel-centre convention at SS x resolution
    p = np.round(q * SC).astype(np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(img, [p], val, cv2.LINE_8, SHIFT)


def _down(img):
    h, w = img.shape
    return cv2.resize(img, (w // SS, h // SS), interpolation=cv2.INTER_AREA)


def render_scan(path, spec, M, dpi=600, rot=0.0, mirror=False, frame_e=0.0, kx=1.0, ky=1.0,
                dark_plate=True, noise=0.01, blur=0.8, seed=0, droop=0.0):
    s = dpi / 25.4
    size = 1.5 * spec.size
    W = H = int(size * s)
    bg, fg = (230, 35) if dark_plate else (25, 215)
    img = np.full((H * SS, W * SS), bg, np.uint8)
    outline, holes = plate_polys(spec, M, droop=droop)
    Rm = rot2(rot)
    Mi = np.diag([-1.0, 1.0]) if mirror else np.eye(2)
    F = np.array([[1, -math.sin(math.radians(frame_e))], [0, math.cos(math.radians(frame_e))]])
    Q = np.diag([1 / kx, 1 / ky]) @ np.linalg.inv(F) @ Mi @ Rm

    def to_px(p):
        q = p @ Q.T
        return np.c_[q[:, 0] * s + W / 2, -q[:, 1] * s + H / 2]
    _fill(img, to_px(outline), fg)
    for h in holes:
        _fill(img, to_px(h), bg)
    g = _down(img).astype(np.float32) / 255
    g = cv2.GaussianBlur(g, (0, 0), blur)
    rng = np.random.default_rng(seed)
    g += rng.normal(0, noise, g.shape).astype(np.float32)
    out = np.clip(g * 255, 0, 255).astype(np.uint8)
    Image.fromarray(out).save(path, dpi=(dpi, dpi))


def look_at(C, T):
    z = (T - C) / np.linalg.norm(T - C)
    x = np.cross(z, [0, 1.0, 0]); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.vstack([x, y, z])
    return R, -R @ C


def render_photo(path, spec, M, f=3000.0, size=(3000, 2250), cam=(15, -40, 330), target=(0, 0, 0),
                 plate_rot=0.0, plate_off=(0, 0), h=None, k1=0.0, paper=np.eye(2),
                 dark_plate=True, dark_window=False, noise=0.012, blur=1.0, seed=0, jpeg=92,
                 marker_scale=1.0, warp=0.0, glare=0.0, glare_period=220.0, glare_angle=40.0,
                 layout="paper", flip=False, droop=0.0):
    """warp: height (mm) of the plate corners above its centre (a cupped plate).
    glare: peak brightness added to the plate surface in diagonal bands, like a lamp
    reflecting off the shiny top-layer lines (holes and paper are not affected).
    h: height of the hole edges above the reference (default: the plate's feature height).
    flip: the plate lies first-layer side up (mirrored, as seen from above)."""
    if h is None:
        h = spec.feature_height(upside_down=flip)
    Wd, Hd = size
    c = np.array([(Wd - 1) / 2, (Hd - 1) / 2])
    K = np.array([[f, 0, c[0]], [0, f, c[1]], [0, 0, 1]])
    R, t = look_at(np.array(cam, float), np.array(target, float))

    def Hz(z):
        return K @ np.c_[R[:, 0], R[:, 1], z * R[:, 2] + t]

    def proj(pts, z=0.0):
        p = np.c_[pts, np.ones(len(pts))] @ Hz(z).T
        return p[:, :2] / p[:, 2:]

    def proj_plate(local):
        """Project plate-local points (centred, unrotated) lifted by the warp."""
        z = h + warp * (local ** 2).sum(1) / (2 * (spec.span / 2) ** 2)
        w = local @ Rm.T + off
        p = (np.c_[w, z] @ R.T + t) @ K.T
        return p[:, :2] / p[:, 2:]

    paper_v, ink_v = 225, 18
    U = np.full((Hd * SS, Wd * SS), paper_v, np.uint8)
    if dark_window:
        wh = sheet.window_half(layout)
        _fill(U, proj(np.array([[-wh, -wh], [wh, -wh], [wh, wh], [-wh, wh]]) @ paper.T), ink_v)
    dic = cv2.aruco.getPredefinedDictionary(sheet.ARUCO_DICT)
    hs = sheet.marker_size(layout) / 2
    cell = sheet.marker_size(layout) / 6
    for mid, x, y in sheet.marker_layout(layout):
        bits = cv2.aruco.generateImageMarker(dic, mid, 6, borderBits=1)
        for r in range(6):
            for cc in range(6):
                if bits[r, cc] == 0:
                    x0, y0 = x - hs + cc * cell, y + hs - r * cell
                    q = np.array([[x0, y0], [x0 + cell, y0], [x0 + cell, y0 - cell], [x0, y0 - cell]])
                    # slightly overlap cells to avoid AA seams
                    _fill(U, proj(q @ paper.T), ink_v)
    outline, holes = plate_polys(spec, M, marker_scale=marker_scale, droop=droop)
    if flip:
        F = np.diag([-1.0, 1.0])
        outline, holes = outline @ F, [hp @ F for hp in holes]
    Rm = rot2(plate_rot)
    off = np.array(plate_off, float)
    pv = 40 if dark_plate else 200
    behind = ink_v if dark_window else paper_v
    _fill(U, proj_plate(outline), pv)
    for hp in holes:
        _fill(U, proj_plate(hp), behind)
    g = _down(U).astype(np.float32) / 255
    yy, xx = np.mgrid[0:Hd, 0:Wd].astype(np.float32)
    if glare:
        P = np.zeros((Hd * SS, Wd * SS), np.uint8)
        _fill(P, proj_plate(outline), 255)
        for hp in holes:
            _fill(P, proj_plate(hp), 0)
        on_plate = _down(P).astype(np.float32) / 255
        a = math.radians(glare_angle)
        u = (xx * math.cos(a) + yy * math.sin(a)) / glare_period
        band = np.clip(np.cos(2 * math.pi * u), 0, 1) ** 3
        env = np.exp(-(((xx - c[0]) / (0.45 * Wd)) ** 2 + ((yy - c[1]) / (0.45 * Hd)) ** 2))
        g += glare * band * env * on_plate
    # vignetting
    rr = ((xx - c[0]) ** 2 + (yy - c[1]) ** 2) / (0.5 * math.hypot(Wd, Hd)) ** 2
    g *= (1 - 0.25 * rr)
    # lens distortion: distorted pixel p_d shows undistorted point p_u
    if k1:
        Rn = 0.5 * math.hypot(Wd, Hd)
        mx = c[0] + (xx - c[0]) * (1 + k1 * rr * (0.5 * math.hypot(Wd, Hd)) ** 2 / Rn ** 2)
        my = c[1] + (yy - c[1]) * (1 + k1 * rr * (0.5 * math.hypot(Wd, Hd)) ** 2 / Rn ** 2)
        g = cv2.remap(g, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    g = cv2.GaussianBlur(g, (0, 0), blur)
    rng = np.random.default_rng(seed)
    g += rng.normal(0, noise, g.shape).astype(np.float32)
    out = np.clip(g * 255, 0, 255).astype(np.uint8)
    cv2.imwrite(path, out, [cv2.IMWRITE_JPEG_QUALITY, jpeg])
