"""Measure XY skew and scale from scans or phone photos of the test plate."""
from __future__ import annotations

import math
import os

import cv2
import numpy as np

from . import sheet
from .plate import PlateSpec


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def load_gray(path):
    """Return (gray float32 in 0..1, PIL info dict, exif dict)."""
    info, exif = {}, {}
    try:
        from PIL import Image, ImageOps
        try:
            import pillow_heif  # optional, for iPhone .HEIC files
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        im = Image.open(path)
        info = dict(im.info)
        try:
            ex = im.getexif()
            exif = dict(ex)
            exif.update(ex.get_ifd(0x8769))
        except Exception:
            pass
        im = ImageOps.exif_transpose(im)
        if im.mode in ("I;16", "I;16B", "I;16L", "I"):
            a = np.asarray(im, dtype=np.float32)
            a /= max(1.0, float(a.max()))
        else:
            a = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
        return a, info, exif
    except Exception as e:  # fall back to OpenCV
        g = cv2.imread(path, cv2.IMREAD_GRAYSCALE | cv2.IMREAD_ANYDEPTH)
        if g is None:
            raise RuntimeError(f"Cannot read image {path}: {e}")
        g = g.astype(np.float32)
        g /= 65535.0 if g.max() > 255 else 255.0
        return g, info, exif


# ---------------------------------------------------------------------------
# Hole detection in a metric (fronto-parallel, known px/mm) image
# ---------------------------------------------------------------------------

def _component_holes(comp):
    """Given a binary component mask (uint8 0/1), return label image of the
    enclosed background regions (holes) and their stats."""
    inv = (comp == 0).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(inv, connectivity=4)
    H, W = comp.shape
    holes = []
    for k in range(1, n):
        x, y, w, h, a = stats[k]
        if x == 0 or y == 0 or x + w >= W or y + h >= H:
            continue                          # touches border -> outside region
        holes.append((k, x, y, w, h, a, cent[k]))
    return lab, holes


def detect_holes(g, s, spec: PlateSpec, debug=None):
    """Find grid holes and marker holes.

    g: float32 image (0..1) where 1 px = 1/s mm, fronto-parallel.
    Returns dict with 'big' (N,2) and 'small' (M,2) sub-pixel centres in pixel
    coordinates (col,row), hole diameters (mm) and image statistics.
    """
    sig = max(0.6, 0.06 * s)
    gb = cv2.GaussianBlur(g, (0, 0), sig)
    g8 = np.clip(gb * 255, 0, 255).astype(np.uint8)
    _, bright = cv2.threshold(g8, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    plate_area = (spec.size * s) ** 2
    A_big = math.pi * (spec.hole_d / 2 * s) ** 2
    best = None
    for dark_plate in (True, False):
        mask = (bright == 0) if dark_plate else (bright == 1)
        mask = mask.astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        order = np.argsort(-stats[1:, cv2.CC_STAT_AREA]) + 1
        for k in order[:4]:
            x, y, w, h, a = stats[k]
            if not (0.35 * plate_area < a < 1.6 * plate_area):
                continue
            pad = 3
            comp = np.zeros((h + 2 * pad, w + 2 * pad), np.uint8)
            comp[pad:pad + h, pad:pad + w] = (lab[y:y + h, x:x + w] == k)
            hlab, holes = _component_holes(comp)
            big, small = [], []
            for (hk, hx, hy, hw, hh, ha, hc) in holes:
                if min(hw, hh) < 3:
                    continue
                aspect = max(hw, hh) / min(hw, hh)
                fill = ha / float(hw * hh)
                if aspect > 1.5 or not (0.55 < fill < 0.97):
                    continue
                q = ha / A_big
                entry = (hc[0] + x - pad, hc[1] + y - pad, ha)
                if 0.45 < q < 1.8:
                    big.append(entry)
                elif 0.05 < q <= 0.45:
                    small.append(entry)
            score = len(big)
            if best is None or score > best["score"]:
                plate_px = comp[pad:pad + h, pad:pad + w].astype(bool)
                best = dict(score=score, big=big, small=small, dark_plate=dark_plate,
                            bbox=(x, y, w, h), plate_px=plate_px)
    if best is None or best["score"] < 6:
        raise RuntimeError("Could not find the test plate in the image (need clear contrast "
                           "between the plate and what is behind its holes).")

    x, y, w, h = best["bbox"]
    sub = gb[y:y + h, x:x + w]
    lvl_plate = float(np.median(sub[best["plate_px"]]))

    # Hole (background) level: median inside the big holes
    vals = []
    for cx, cy, _ in best["big"]:
        r = 0.3 * spec.hole_d / 2 * s
        x0, x1 = int(cx - r), int(cx + r) + 1
        y0, y1 = int(cy - r), int(cy + r) + 1
        vals.append(np.median(gb[y0:y1, x0:x1]))
    lvl_hole = float(np.median(vals))

    def refine(cx, cy, rad_mm):
        r = rad_mm * s
        R = 1.35 * r + 2
        for _ in range(4):
            x0, x1 = int(math.floor(cx - R)), int(math.ceil(cx + R)) + 1
            y0, y1 = int(math.floor(cy - R)), int(math.ceil(cy + R)) + 1
            if x0 < 0 or y0 < 0 or x1 > g.shape[1] or y1 > g.shape[0]:
                return None
            patch = gb[y0:y1, x0:x1]
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            w = np.clip((patch - lvl_plate) / (lvl_hole - lvl_plate), 0, 1)
            w *= ((xx - cx) ** 2 + (yy - cy) ** 2) <= R * R
            sw = w.sum()
            if sw <= 0:
                return None
            cx, cy = float((w * xx).sum() / sw), float((w * yy).sum() / sw)
        d_mm = 2 * math.sqrt(sw / math.pi) / s
        return cx, cy, d_mm

    big, dbig = [], []
    for cx, cy, _ in best["big"]:
        r = refine(cx, cy, spec.hole_d / 2)
        if r:
            big.append(r[:2]); dbig.append(r[2])
    small = []
    for cx, cy, _ in best["small"]:
        r = refine(cx, cy, spec.marker_d / 2)
        if r:
            small.append(r[:2])
    return dict(big=np.array(big), small=np.array(small), hole_d=np.array(dbig),
                dark_plate=best["dark_plate"], contrast=abs(lvl_hole - lvl_plate),
                gb=gb, lvl_plate=lvl_plate, lvl_hole=lvl_hole, s=s)


# ---------------------------------------------------------------------------
# Grid indexing and affine fit (all in millimetres, y up)
# ---------------------------------------------------------------------------

def index_grid(big, small, spec: PlateSpec):
    p = spec.pitch
    if len(small) < 3:
        raise RuntimeError(f"Found only {len(small)} of 3 marker holes; cannot tell X from Y. "
                           "Check that the small holes printed open and are visible.")
    # Y marker = the pair of small holes closest to y_pair_sep apart
    best = None
    for a in range(len(small)):
        for b in range(a + 1, len(small)):
            d = np.linalg.norm(small[a] - small[b])
            err = abs(d - spec.y_pair_sep) / spec.y_pair_sep
            if err < 0.35 and (best is None or err < best[0]):
                best = (err, a, b)
    if best is None:
        raise RuntimeError("Could not find the Y-marker pair of small holes.")
    _, a, b = best
    ymid = (small[a] + small[b]) / 2
    others = [small[k] for k in range(len(small)) if k not in (a, b)]

    cand = None
    for xm in others:
        for k, o in enumerate(big):
            e1 = abs(np.linalg.norm(xm - o) - p / 2)
            e2 = abs(np.linalg.norm(ymid - o) - p / 2)
            e = e1 + e2
            if e1 < 0.25 * p and e2 < 0.25 * p and (cand is None or e < cand[0]):
                cand = (e, k, xm)
    if cand is None:
        raise RuntimeError("Could not locate the origin hole next to the X / Y markers.")
    _, ko, xm = cand
    O = big[ko]
    ex, ey = 2 * (xm - O), 2 * (ymid - O)
    ang = math.degrees(math.acos(np.dot(ex, ey) / (np.linalg.norm(ex) * np.linalg.norm(ey))))
    if abs(ang - 90) > 15:
        raise RuntimeError(f"Marker geometry inconsistent (axis angle {ang:.1f} deg).")

    B = np.c_[ex, ey] / p           # nominal mm -> measured mm (approx.)
    Binv = np.linalg.inv(B)
    nom, meas = [], []
    seen = set()
    for q in big:
        ij = Binv @ (q - O) / p
        r = np.round(ij)
        if np.max(np.abs(ij - r)) > 0.3:
            continue
        i, j = int(r[0]), int(r[1])
        if not (0 <= i < spec.n and 0 <= j < spec.n) or (i, j) in seen:
            continue
        seen.add((i, j))
        nom.append((i * p, j * p))
        meas.append(q)
    return np.array(nom), np.array(meas)


def _lattice(big, spec: PlateSpec):
    """Assign integer lattice coordinates to grid holes without using markers.
    Returns (ij (N,2) int, keep mask, affine L mapping [i, j, 1] -> mm)."""
    p = spec.pitch
    d = big[None, :, :] - big[:, None, :]
    r = np.linalg.norm(d, axis=2)
    vec = d[(r > 0.7 * p) & (r < 1.3 * p)]
    if len(vec) < 8:
        raise RuntimeError("Too few neighbouring grid holes to find the lattice.")
    a = np.arctan2(vec[:, 1], vec[:, 0])
    phi = math.atan2(np.sin(4 * a).mean(), np.cos(4 * a).mean()) / 4
    basis = []
    for ang in (phi, phi + math.pi / 2):
        u = np.array([math.cos(ang), math.sin(ang)])
        v = vec[vec @ u > 0.9 * np.linalg.norm(vec, axis=1)]
        basis.append(np.median(v, axis=0))
    Binv = np.linalg.inv(np.c_[basis[0], basis[1]])
    q0 = big[np.argmin(np.linalg.norm(big - np.median(big, axis=0), axis=1))]
    ij = (Binv @ (big - q0).T).T
    r_ = np.round(ij)
    keep = np.max(np.abs(ij - r_), axis=1) < 0.3
    ij = r_.astype(int)
    for k in range(2):                      # trim to an n-wide band
        vals, cnt = np.unique(ij[keep, k], return_counts=True)
        best = max(range(len(vals)), key=lambda s0: cnt[(vals >= vals[s0]) & (vals < vals[s0] + spec.n)].sum())
        keep &= (ij[:, k] >= vals[best]) & (ij[:, k] < vals[best] + spec.n)
        ij[:, k] -= vals[best]
    if keep.sum() < 12:
        raise RuntimeError(f"Only {keep.sum()} grid holes fit a regular lattice.")
    L, *_ = np.linalg.lstsq(np.c_[ij[keep], np.ones(keep.sum())], big[keep], rcond=None)
    return ij, keep, L


def index_grid_by_lattice(det, big, spec: PlateSpec, to_mm):
    """Fallback when the small marker holes are too closed-up to segment: index
    the grid from the big holes alone, then pick the corner / axis assignment
    whose predicted marker positions are brightest in the image."""
    p, n = spec.pitch, spec.n
    ij, keep, L = _lattice(big, spec)
    px = det["big"]
    M, *_ = np.linalg.lstsq(np.c_[to_mm(px), np.ones(len(px))], px, rcond=None)
    s = det["s"]
    g = cv2.GaussianBlur(det["gb"], (0, 0), max(0.6, 0.15 * s))
    lp, lh = det["lvl_plate"], det["lvl_hole"]
    R = max(1, int(round(0.5 * s)))
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
    disk = xx ** 2 + yy ** 2 <= R * R

    def bright(lat):
        q = np.r_[np.r_[lat, 1.0] @ L, 1.0] @ M
        cx, cy = int(round(q[0])), int(round(q[1]))
        if cx - R < 0 or cy - R < 0 or cx + R + 1 > g.shape[1] or cy + R + 1 > g.shape[0]:
            return 0.0
        v = g[cy - R:cy + R + 1, cx - R:cx + R + 1][disk].max()
        return float(np.clip((v - lp) / (lh - lp), 0, 1))

    m, h = 0.5, spec.y_pair_sep / 2 / p
    cands = []
    for ci in (0, n - 1):
        for cj in (0, n - 1):
            o = np.array([ci, cj], float)
            a = np.array([1 if ci == 0 else -1, 0], float)
            b = np.array([0, 1 if cj == 0 else -1], float)
            for dx, dy in ((a, b), (b, a)):
                on = [o + m * dx, o + (m - h) * dy, o + (m + h) * dy]
                off = [o + m * dy, o + (m - h) * dx, o + (m + h) * dx]
                score = min(bright(z) for z in on) - max(bright(z) for z in off)
                cands.append((score, o, dx, dy))
    cands.sort(key=lambda c: -c[0])
    if cands[0][0] < 0.15 or cands[0][0] - cands[1][0] < 0.1:
        raise RuntimeError("Could not identify the marker holes (they may be blocked); "
                           "cannot tell X from Y.")
    _, o, dx, dy = cands[0]
    rel = ij[keep] - o
    nom = np.c_[rel @ dx, rel @ dy] * p
    return nom, big[keep]


def _design(nom, meas, warp):
    """Least-squares system for meas = affine(nom) [+ height-warp parallax].

    With warp=True (photo mode), a plate that is not perfectly flat shifts each
    hole by z/D * (p - nadir).  z is modelled as a quadratic bowl/saddle about
    the plate centre (q = x^2, xy, y^2).  The camera nadir is not known well
    enough (it depends on the focal length), so z*nadir is left free as an
    independent quadratic field per axis; on a symmetric grid those terms are
    orthogonal to the affine part.  Unknowns:
    [P[:,0] (3), P[:,1] (3), and if warp: z/D (3), quad field x (3), y (3)]."""
    N = len(nom)
    X = np.c_[nom, np.ones(N)]
    Z = np.zeros((N, 3))
    Ax, Ay = np.c_[X, Z], np.c_[Z, X]
    if warp:
        c = nom - nom.mean(0)
        q = np.c_[c[:, 0] ** 2, c[:, 0] * c[:, 1], c[:, 1] ** 2] / 1e4
        d = meas - meas.mean(0)
        Ax = np.c_[Ax, q * d[:, :1], q, Z]
        Ay = np.c_[Ay, q * d[:, 1:], Z, q]
    return np.r_[Ax, Ay], np.r_[meas[:, 0], meas[:, 1]]


def fit_affine(nom, meas, warp=False, n_iter=3):
    """Robust fit.  Returns P (3x2 affine: [x, y, 1] -> mm), keep mask,
    model prediction for every hole, kept residuals, sigma, solution vector
    and its covariance."""
    N = len(nom)
    Af, _ = _design(nom, meas, warp)

    def solve(keep):
        A, b = _design(nom[keep], meas[keep], warp)
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        return sol, (Af @ sol).reshape(2, N).T

    keep = np.ones(N, bool)
    for _ in range(n_iter):
        sol, pred = solve(keep)
        rn = np.linalg.norm(meas - pred, axis=1)
        new = rn < max(4 * 1.4826 * np.median(rn[keep]), 0.03)
        if new.sum() < 12 or np.array_equal(new, keep):
            break
        keep = new
    sol, pred = solve(keep)
    A, b = _design(nom[keep], meas[keep], warp)
    res = (b - A @ sol).reshape(2, -1).T
    dof = max(1, A.shape[0] - A.shape[1])
    sigma = math.sqrt((res ** 2).sum() / dof)
    cov = sigma ** 2 * np.linalg.inv(A.T @ A)
    P = np.c_[sol[0:3], sol[3:6]]
    return P, keep, pred, res, sigma, sol, cov


def geometry_from_affine(P):
    A = P[:2].T                       # columns: image of nominal unit X / Y
    a1, a2 = A[:, 0], A[:, 1]
    sx, sy = np.linalg.norm(a1), np.linalg.norm(a2)
    theta = math.degrees(math.acos(np.clip(np.dot(a1, a2) / (sx * sy), -1, 1)))
    return sx, sy, theta, a1, a2


def affine_uncertainty(sol, cov, n=3000, seed=0):
    rng = np.random.default_rng(seed)
    S = rng.multivariate_normal(sol, cov, size=n, method="cholesky")
    th, sx, sy = [], [], []
    for z in S:
        a, b, t, *_ = geometry_from_affine(np.c_[z[0:3], z[3:6]])
        sx.append(a); sy.append(b); th.append(t)
    return float(np.std(th)), float(np.std(sx)), float(np.std(sy))


def analyze_metric(g, s, to_mm, spec: PlateSpec, scale_corr=1.0, annotate_path=None, warp=False,
                   details=False, annotate_crop=False):
    """Full measurement on a metric image. to_mm maps (N,2) pixel coords -> mm (y up).
    details=True adds per-hole data (nominal position, residual in plate axes) for plotting."""
    det = detect_holes(g, s, spec)
    big_mm, small_mm = to_mm(det["big"]), to_mm(det["small"])
    try:
        nom, meas = index_grid(big_mm, small_mm, spec)
    except RuntimeError:
        nom, meas = index_grid_by_lattice(det, big_mm, spec, to_mm)
    if len(nom) < 12:
        raise RuntimeError(f"Only {len(nom)} grid holes could be indexed.")
    P, keep, pred, res, sigma, sol, cov = fit_affine(nom, meas, warp)
    sx, sy, theta, a1, a2 = geometry_from_affine(P)
    dth, dsx, dsy = affine_uncertainty(sol, cov)
    # orientation of plate axes relative to the image/reference frame
    along_x = abs(a1[0]) >= abs(a1[1])
    sign = np.sign(a1[0] * a2[1]) if along_x else np.sign(a1[1] * a2[0])
    mirrored = (a1[0] * a2[1] - a1[1] * a2[0]) < 0
    out = dict(
        theta=theta, theta_std=dth,
        scale_x=sx * scale_corr, scale_y=sy * scale_corr,
        scale_x_std=dsx, scale_y_std=dsy,
        n_holes=int(keep.sum()), n_expected=spec.n ** 2,
        rms_um=1000 * float(np.sqrt((res ** 2).sum(1).mean())),
        frame_sign=int(sign), plate_x_along_frame_x=bool(along_x), mirrored=bool(mirrored),
        rotation_deg=math.degrees(math.atan2(a1[1], a1[0])),
        hole_d_mean=float(np.mean(det["hole_d"])) if len(det["hole_d"]) else None,
        dark_plate=det["dark_plate"],
    )
    if warp:
        # warp coefficients are z/D per (100 mm)^2: z/D at the four outer holes
        # relative to the plate centre (the caller multiplies by D)
        h = (spec.span / 2) ** 2 / 1e4
        out["warp_zD_corners"] = [float(h * (sol[6] + sx_ * sy_ * sol[7] + sol[8]))
                                  for sx_ in (-1, 1) for sy_ in (-1, 1)]
    if details:
        # residuals expressed along the plate's own axes, so plots read the same
        # however the plate lay on the scanner/sheet
        r_plate = (meas - pred) @ np.linalg.inv(P[:2])
        out["holes"] = dict(nom=nom.tolist(), res=r_plate.tolist(), keep=keep.tolist())
    if annotate_path:
        _annotate(g, det, to_mm, nom, meas, P, pred, keep, annotate_path, s, crop=annotate_crop)
    return out


def _annotate(g, det, to_mm, nom, meas, P, pred, keep, path, s, gain=100.0, crop=False):
    """Write the overlay JPEG. crop=True trims it to the plate and caps it at 2000 px."""
    img = cv2.cvtColor(np.clip(g * 255, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    # map mm back to px: build inverse from the two point sets
    px = det["big"]
    mm = to_mm(px)
    M, *_ = np.linalg.lstsq(np.c_[mm, np.ones(len(mm))], px, rcond=None)
    to_px = lambda q: np.c_[q, np.ones(len(q))] @ M
    mp, pp = to_px(meas), to_px(pred)
    for k in range(len(nom)):
        c = (0, 200, 0) if keep[k] else (0, 0, 255)
        a = tuple(int(v) for v in mp[k])
        cv2.circle(img, a, int(2.6 * s), c, max(1, int(s / 12)), cv2.LINE_AA)
        end = pp[k] + (mp[k] - pp[k]) * gain
        cv2.arrowedLine(img, tuple(int(v) for v in pp[k]), tuple(int(v) for v in end),
                        (255, 80, 0), max(1, int(s / 12)), cv2.LINE_AA, tipLength=0.25)
    for q in det["small"]:
        cv2.circle(img, tuple(int(v) for v in q), int(0.4 * s), (0, 160, 255), max(1, int(s / 12)))
    org = to_px(P[2:3])[0]
    xa = to_px(np.array([[P[0, 0] * 60 + P[2, 0], P[0, 1] * 60 + P[2, 1]]]))[0]
    ya = to_px(np.array([[P[1, 0] * 60 + P[2, 0], P[1, 1] * 60 + P[2, 1]]]))[0]
    for tip, lbl in ((xa, "X"), (ya, "Y")):
        cv2.arrowedLine(img, tuple(int(v) for v in org), tuple(int(v) for v in tip), (0, 0, 255),
                        max(2, int(s / 6)), cv2.LINE_AA, tipLength=0.08)
        cv2.putText(img, lbl, tuple(int(v) for v in tip), cv2.FONT_HERSHEY_SIMPLEX, s / 8,
                    (0, 0, 255), max(2, int(s / 6)), cv2.LINE_AA)
    if crop:
        pad = 12 * s
        x0, y0 = np.maximum(mp.min(0) - pad, 0).astype(int)
        x1, y1 = (mp.max(0) + pad).astype(int)
        img = img[y0:y1, x0:x1]
        f = 2000 / max(img.shape[:2])
        if f < 1:
            img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    else:
        cv2.putText(img, f"residuals x{gain:.0f}", (int(s), int(3 * s)), cv2.FONT_HERSHEY_SIMPLEX,
                    s / 12, (255, 80, 0), max(1, int(s / 8)), cv2.LINE_AA)
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 90])


# ---------------------------------------------------------------------------
# Scanner mode
# ---------------------------------------------------------------------------

def analyze_scan(path, spec, dpi=None, annotate_path=None, max_ppmm=24.0, **kw):
    g, info, _ = load_gray(path)
    if dpi is None:
        d = info.get("dpi")
        if d:
            dpi = float(d[0])
    if not dpi or dpi < 50:
        raise RuntimeError(f"{os.path.basename(path)}: scan resolution unknown; pass --dpi.")
    s0 = dpi / 25.4
    s = s0
    if s0 > max_ppmm * 1.2:
        h0, w0 = g.shape
        f = max_ppmm / s0
        nw, nh = int(round(w0 * f)), int(round(h0 * f))
        g = cv2.resize(g, (nw, nh), interpolation=cv2.INTER_AREA)
        fx, fy = nw / w0, nh / h0
        # resize maps pixel centres: x_new = (x_old + 0.5) * fx - 0.5
        to_mm = lambda q: (np.c_[((q[:, 0] + 0.5) / fx - 0.5) / s0,
                                  -((q[:, 1] + 0.5) / fy - 0.5) / s0] if len(q) else q)
        s = s0 * fx
    else:
        to_mm = lambda q: np.c_[q[:, 0] / s0, -q[:, 1] / s0] if len(q) else q
    r = analyze_metric(g, s, to_mm, spec, annotate_path=annotate_path, **kw)
    r.update(mode="scan", dpi=dpi)
    return r


# ---------------------------------------------------------------------------
# Phone mode
# ---------------------------------------------------------------------------

def _undistort_pts(p, k, c, R):
    d = p - c
    r2 = (d ** 2).sum(1, keepdims=True) / R ** 2
    return c + d * (1 + k * r2)


def _distort_pts(u, k, c, R, iters=8):
    d = u.copy()
    for _ in range(iters):
        r2 = ((d - c) ** 2).sum(-1, keepdims=True) / R ** 2
        d = c + (u - c) / (1 + k * r2)
    return d


def _focal_from_exif(exif, w, h):
    f35 = exif.get(0xA405)
    if f35:
        try:
            f35 = float(f35)
            if f35 > 0:
                return f35 * math.hypot(w, h) / 43.2666
        except (TypeError, ValueError):
            pass
    return None


def _focal_from_h(H, c):
    """Zhang single-view focal estimate (principal point c, square pixels).
    H maps sheet mm -> image px."""
    T = np.array([[1, 0, -c[0]], [0, 1, -c[1]], [0, 0, 1.0]])
    G = T @ H
    h1, h2 = G[:, 0], G[:, 1]
    eqs = []
    den1 = h1[2] * h2[2]
    num1 = -(h1[0] * h2[0] + h1[1] * h2[1])
    den2 = h1[2] ** 2 - h2[2] ** 2
    num2 = -(h1[0] ** 2 + h1[1] ** 2 - h2[0] ** 2 - h2[1] ** 2)
    # least squares on f^2 * den = num
    A = np.array([den1, den2]); b = np.array([num1, num2])
    if np.dot(A, A) < 1e-30:
        return None
    f2 = np.dot(A, b) / np.dot(A, A)
    return math.sqrt(f2) if f2 > 0 else None


def camera_distance(H_s2i, f, c):
    K = np.array([[f, 0, c[0]], [0, f, c[1]], [0, 0, 1.0]])
    B = np.linalg.inv(K) @ H_s2i
    lam = 2.0 / (np.linalg.norm(B[:, 0]) + np.linalg.norm(B[:, 1]))
    r1, r2, t = lam * B[:, 0], lam * B[:, 1], lam * B[:, 2]
    if t[2] < 0:
        r1, r2, t = -r1, -r2, -t
    R = np.c_[r1, r2, np.cross(r1, r2)]
    U, _, Vt = np.linalg.svd(R)
    R = U @ Vt
    C = -R.T @ t
    return abs(C[2]), math.degrees(math.acos(min(1.0, abs(R[2, 2]))))




def rectify_phone(g, exif, focal_px=None, span_x=None, span_y=None, ppmm=15.0,
                  fit_distortion=True):
    h_img, w_img = g.shape
    g8 = np.clip(g * 255, 0, 255).astype(np.uint8)
    dic = cv2.aruco.getPredefinedDictionary(sheet.ARUCO_DICT)
    prm = cv2.aruco.DetectorParameters()
    prm.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    prm.cornerRefinementWinSize = max(5, int(round(min(w_img, h_img) / 600)))
    det = cv2.aruco.ArucoDetector(dic, prm)
    corners, ids, _ = det.detectMarkers(g8)
    if ids is None or len(ids) < 6:
        raise RuntimeError(f"Found {0 if ids is None else len(ids)} reference markers; need at least 6. "
                           "Make sure the whole sheet is in frame, in focus and evenly lit.")
    kx = (span_x / sheet.SPAN_X_NOMINAL) if span_x else 1.0
    ky = (span_y / sheet.SPAN_Y_NOMINAL) if span_y else 1.0
    ref = sheet.marker_corners(kx, ky)
    ip, sp, sg = [], [], []
    for cset, mid in zip(corners, ids.ravel()):
        if int(mid) in ref:
            r_ = ref[int(mid)]
            ip.append(cset.reshape(4, 2)); sp.append(r_); sg.append(np.sign(r_ - r_.mean(0)))
    ip, sp, sg = np.vstack(ip).astype(np.float64), np.vstack(sp), np.vstack(sg)
    c = np.array([(w_img - 1) / 2, (h_img - 1) / 2])
    Rn = 0.5 * math.hypot(w_img, h_img)

    # Reject mis-detections
    _, inl = cv2.findHomography(ip, sp, cv2.RANSAC, 1.5)
    inl = inl.ravel().astype(bool)
    ip, sp, sg = ip[inl], sp[inl], sg[inl]

    # Fit radial lens distortion k1 and a marker "growth" term (ink spread /
    # exposure bloom moves every marker edge in or out by the same amount).
    def cost(x):
        k, grow = x
        u = _undistort_pts(ip, k, c, Rn)
        s2 = sp + grow * sg
        Hh, _ = cv2.findHomography(u, s2, 0)
        pr = cv2.perspectiveTransform(u.reshape(-1, 1, 2), Hh).reshape(-1, 2)
        return float(np.sqrt(((pr - s2) ** 2).sum(1).mean()))

    from scipy.optimize import minimize
    x0 = np.array([0.0, 0.0])
    if fit_distortion:
        x = minimize(cost, x0, method="Nelder-Mead",
                     options={"xatol": 1e-6, "fatol": 1e-7, "initial_simplex":
                              [[0, 0], [0.02, 0], [0, 0.05]]}).x
    else:
        x = minimize(lambda v: cost([0.0, v[0]]), [0.0], method="Nelder-Mead").x
        x = np.array([0.0, x[0]])
    k1, grow = float(x[0]), float(x[1])
    rms = cost([k1, grow])
    u = _undistort_pts(ip, k1, c, Rn)
    H_i2s, _ = cv2.findHomography(u, sp + grow * sg, 0)
    H_s2i = np.linalg.inv(H_i2s)

    # Focal length -> camera distance (for parallax-free scale)
    f_src = "given"
    f = focal_px
    if not f:
        f = _focal_from_exif(exif, w_img, h_img); f_src = "EXIF"
    if not f:
        f = _focal_from_h(H_s2i, c); f_src = "estimated"
        if not f or not (0.3 * 2 * Rn < f < 4 * 2 * Rn):
            f = 0.6 * 2 * Rn; f_src = "assumed"
    D, tilt = camera_distance(H_s2i, f, c)

    # Rectify the window region
    Wh = sheet.WINDOW_HALF
    n = int(round(2 * Wh * ppmm))
    cols, rows = np.meshgrid(np.arange(n, dtype=np.float64), np.arange(n, dtype=np.float64))
    xs, ys = cols / ppmm - Wh, Wh - rows / ppmm
    pts = np.stack([xs, ys], -1).reshape(-1, 1, 2)
    uu = cv2.perspectiveTransform(pts, H_s2i).reshape(n, n, 2)
    dd = _distort_pts(uu, k1, c, Rn) if k1 else uu
    rect = cv2.remap(g, dd[..., 0].astype(np.float32), dd[..., 1].astype(np.float32),
                     cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    px_per_mm_img = 1.0 / np.sqrt(abs(np.linalg.det(H_i2s[:2, :2])))  # rough, at origin
    meta = dict(markers=int(len(ids)), ref_rms_um=1000 * rms, k1=k1, marker_growth_um=1000 * grow, focal_px=f, focal_src=f_src,
                camera_distance_mm=float(D), tilt_deg=tilt, sheet_scale=(kx, ky))
    return rect, ppmm, meta


def analyze_photo(path, spec, focal_px=None, span_x=None, span_y=None, hole_height=None,
                  annotate_path=None, fit_distortion=True, correct_warp=True, **kw):
    g, _, exif = load_gray(path)
    rect, s, meta = rectify_phone(g, exif, focal_px, span_x, span_y, fit_distortion=fit_distortion)
    Wh = sheet.WINDOW_HALF
    to_mm = lambda q: np.c_[q[:, 0] / s - Wh, Wh - q[:, 1] / s] if len(q) else q
    h = spec.thickness / 2 if hole_height is None else hole_height
    D = meta["camera_distance_mm"]
    corr = (D - h) / D            # features at height h appear enlarged by D/(D-h)
    r = analyze_metric(rect, s, to_mm, spec, scale_corr=corr, annotate_path=annotate_path,
                       warp=correct_warp, **kw)
    if "warp_zD_corners" in r:
        r["warp_corners_mm"] = [z * D for z in r.pop("warp_zD_corners")]
    r.update(mode="photo", parallax_corr=corr, **meta)
    return r


# ---------------------------------------------------------------------------
# Combining several images (rotation reversal)
# ---------------------------------------------------------------------------

def combine(results):
    th = np.array([r["theta"] for r in results])
    w = 1.0 / np.maximum(np.array([r["theta_std"] for r in results]), 1e-4) ** 2
    sg = np.array([r["frame_sign"] for r in results], float)
    out = {}
    if (sg > 0).any() and (sg < 0).any():
        A = np.c_[np.ones_like(sg), sg]
        W = np.diag(w)
        cov = np.linalg.inv(A.T @ W @ A)
        x = cov @ A.T @ W @ th
        out.update(theta=float(x[0]), frame_bias_deg=float(x[1]), reversal=True)
        n = len(th)
        resid = th - A @ x
        ext = math.sqrt((resid ** 2).sum() / (n - 2)) if n > 2 else 0.0
        out["theta_std"] = float(max(math.sqrt(cov[0, 0]), ext / math.sqrt(n)))
    else:
        x = float((w * th).sum() / w.sum())
        out.update(theta=x, frame_bias_deg=None, reversal=False,
                   theta_std=float(max(1 / math.sqrt(w.sum()),
                                       np.std(th) / math.sqrt(len(th)) if len(th) > 1 else 0)))
    # scales: log s_meas = log s + u*d   (u = +1 if plate X lies along frame x)
    u = np.array([1.0 if r["plate_x_along_frame_x"] else -1.0 for r in results])
    lx = np.log([r["scale_x"] for r in results])
    ly = np.log([r["scale_y"] for r in results])
    if (u > 0).any() and (u < 0).any():
        A = np.vstack([np.c_[np.ones_like(u), np.zeros_like(u), u],
                       np.c_[np.zeros_like(u), np.ones_like(u), -u]])
        x, *_ = np.linalg.lstsq(A, np.r_[lx, ly], rcond=None)
        out.update(scale_x=float(np.exp(x[0])), scale_y=float(np.exp(x[1])),
                   frame_aniso=float(np.exp(2 * x[2])))
    else:
        out.update(scale_x=float(np.exp(lx.mean())), scale_y=float(np.exp(ly.mean())),
                   frame_aniso=None)
    return out
