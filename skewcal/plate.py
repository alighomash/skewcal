"""Test-plate definition and STL generation.

The plate is a flat square with an n x n grid of round through-holes.
Three small "marker" holes identify the origin hole and the X / Y directions,
so the analyzer can index every hole no matter how the plate is rotated or
whether it is scanned face-down (mirrored):

  * X marker: one small hole half-way between hole (0,0) and hole (1,0)
  * Y marker: two small holes straddling the point half-way between
              hole (0,0) and hole (0,1)

Nominal coordinates are in millimetres relative to the origin hole (0,0).
In the STL the origin hole sits at the front-left of the plate, so the plate
must be printed exactly as exported (do not rotate it in the slicer).
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class PlateSpec:
    n: int = 9                 # holes per side
    pitch: float = 12.5        # mm between hole centres
    hole_d: float = 4.0        # grid-hole diameter (mm)
    marker_d: float = 2.0      # marker-hole diameter (mm)
    y_pair_sep: float = 3.5    # centre-to-centre spacing of the Y-marker pair (mm)
    margin: float = 5.0        # solid border outside the outermost holes (mm)
    thickness: float = 1.6     # plate thickness (mm)

    # ---- derived geometry -------------------------------------------------
    @property
    def span(self) -> float:
        return (self.n - 1) * self.pitch

    @property
    def edge(self) -> float:
        """Distance from an outer hole centre to the plate edge."""
        return self.margin + self.hole_d / 2

    @property
    def size(self) -> float:
        return self.span + 2 * self.edge

    def grid_points(self) -> np.ndarray:
        """(n*n, 4) array of [i, j, x_mm, y_mm] for every grid hole."""
        out = []
        for j in range(self.n):
            for i in range(self.n):
                out.append((i, j, i * self.pitch, j * self.pitch))
        return np.array(out, dtype=float)

    def x_marker(self) -> np.ndarray:
        return np.array([[self.pitch / 2, 0.0]])

    def y_markers(self) -> np.ndarray:
        m = self.pitch / 2
        h = self.y_pair_sep / 2
        return np.array([[0.0, m - h], [0.0, m + h]])

    def outline(self) -> np.ndarray:
        """Plate corners (CCW) in nominal coordinates."""
        lo, hi = -self.edge, self.span + self.edge
        return np.array([[lo, lo], [hi, lo], [hi, hi], [lo, hi]])

    def holes(self):
        """List of (cx, cy, diameter) for every hole, nominal coordinates."""
        hs = [(x, y, self.hole_d) for _, _, x, y in self.grid_points()]
        hs += [(x, y, self.marker_d) for x, y in self.x_marker()]
        hs += [(x, y, self.marker_d) for x, y in self.y_markers()]
        return hs

    def validate(self):
        r, rm = self.hole_d / 2, self.marker_d / 2
        walls = {
            "X marker to grid hole": self.pitch / 2 - rm - r,
            "Y marker to grid hole": self.pitch / 2 - self.y_pair_sep / 2 - rm - r,
            "between Y markers": self.y_pair_sep - 2 * rm,
            "between grid holes": self.pitch - 2 * r,
        }
        bad = {k: v for k, v in walls.items() if v < 0.9}
        if bad:
            raise ValueError("Walls too thin to print reliably (<0.9 mm): " +
                             ", ".join(f"{k} = {v:.2f} mm" for k, v in bad.items()))
        return walls

    # ---- json -------------------------------------------------------------
    def to_json(self, path):
        with open(path, "w") as f:
            json.dump({"skewcal_plate": 1, **asdict(self)}, f, indent=2)

    @classmethod
    def from_json(cls, path) -> "PlateSpec":
        with open(path) as f:
            d = json.load(f)
        d.pop("skewcal_plate", None)
        return cls(**d)


# ---------------------------------------------------------------------------
# STL generation (no CAD dependency: Delaunay triangulation of the top face)
# ---------------------------------------------------------------------------

def _circle(cx, cy, d, segs):
    a = np.arange(segs) * 2 * math.pi / segs
    return np.c_[cx + d / 2 * np.cos(a), cy + d / 2 * np.sin(a)]


def build_mesh(spec: PlateSpec, segs_big=72, segs_small=40):
    """Return (vertices (V,3), triangles (T,3)) for a watertight plate."""
    from scipy.spatial import Delaunay

    spec.validate()
    # Shift so the plate's front-left corner is at (0,0)
    off = spec.edge

    outline = spec.outline() + off
    loops = []          # (points, is_hole)
    hole_info = []      # (cx, cy, r_apothem)
    pts = [outline]
    for (cx, cy, d) in spec.holes():
        segs = segs_big if d >= spec.hole_d else segs_small
        c = _circle(cx + off, cy + off, d, segs)
        loops.append(c)
        hole_info.append((cx + off, cy + off, d / 2 * math.cos(math.pi / segs)))
        pts.append(c)

    P = np.vstack(pts)
    tri = Delaunay(P).simplices

    # Drop triangles inside holes
    cen = P[tri].mean(axis=1)
    keep = np.ones(len(tri), bool)
    for cx, cy, ap in hole_info:
        keep &= np.hypot(cen[:, 0] - cx, cen[:, 1] - cy) > ap
    tri = tri[keep]

    # Orient CCW (normal +Z)
    a, b, c = P[tri[:, 0]], P[tri[:, 1]], P[tri[:, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    flip = cross < 0
    tri[flip] = tri[flip][:, [0, 2, 1]]

    nv = len(P)
    t = spec.thickness
    V = np.vstack([np.c_[P, np.zeros(nv)], np.c_[P, np.full(nv, t)]])
    faces = [tri + nv, tri[:, [0, 2, 1]]]          # top (+Z), bottom (-Z)

    # Side walls. Outer loop is CCW -> outward normals with (i, j, j+nv).
    def wall(idx, outward_ccw):
        k = len(idx)
        f = []
        for m in range(k):
            i, j = idx[m], idx[(m + 1) % k]
            if outward_ccw:
                f += [(i, j, j + nv), (i, j + nv, i + nv)]
            else:
                f += [(j, i, i + nv), (j, i + nv, j + nv)]
        return np.array(f)

    start = 0
    faces.append(wall(np.arange(start, start + 4), True))
    start += 4
    for loop in loops:
        k = len(loop)
        faces.append(wall(np.arange(start, start + k), False))   # hole walls face inward
        start += k

    F = np.vstack(faces)
    return V, F


def check_watertight(F) -> bool:
    from collections import Counter
    e = Counter()
    for a, b, c in F:
        for u, v in ((a, b), (b, c), (c, a)):
            e[(u, v)] += 1
    # every directed edge must appear exactly once, and its reverse exactly once
    return all(n == 1 and e.get((v, u), 0) == 1 for (u, v), n in e.items())


def write_stl(path, V, F, name="skewcal_plate"):
    tris = V[F]
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-30
    with open(path, "wb") as f:
        f.write(name.encode()[:80].ljust(80, b" "))
        f.write(struct.pack("<I", len(F)))
        rec = np.zeros(len(F), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
        rec["n"] = n
        rec["v"] = tris
        f.write(rec.tobytes())
