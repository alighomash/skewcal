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
    # Thin-rim holes (the default): the grid holes are hole_d only for the bottom
    # `rim` mm, then widen as a cone to top_d at the top surface. The visible hole
    # edge is then a thin first-layer rim, so lighting angle and parallax barely
    # move it. rim = 0 gives the standard plate with straight holes.
    rim: float = 0.6
    top_d: float = 5.6
    # Cube walls (printed upright, measured for X-Z / Y-Z): the plate's y axis is Z.
    # id_holes small holes in the top border tell the wall pairs apart (mesh only).
    wall: bool = False
    id_holes: int = 0

    @classmethod
    def standard(cls, **kw) -> "PlateSpec":
        """The original plate with straight holes."""
        return cls(rim=0.0, top_d=0.0, **kw)

    @classmethod
    def cube_wall(cls, id_holes=0) -> "PlateSpec":
        """A breakaway-cube wall: straight holes (they run sideways when printed
        upright), slightly larger markers because sideways holes print a little
        closed, and a 118 mm square so the floor plate fits inside the cube."""
        return cls(rim=0.0, top_d=0.0, margin=7.0, marker_d=2.4, wall=True, id_holes=id_holes)

    def id_points(self) -> np.ndarray:
        """Centres of the ID holes in the top border (nominal coordinates)."""
        if not self.id_holes:
            return np.empty((0, 2))
        y = self.span + (self.edge + self.hole_d / 2) / 2
        xs = [self.span / 2] if self.id_holes == 1 else \
            list(self.span / 2 + 8.0 * (np.arange(self.id_holes) - (self.id_holes - 1) / 2))
        return np.array([[x, y] for x in xs])

    @property
    def thin_rim(self) -> bool:
        return self.rim > 0 and self.top_d > self.hole_d

    def feature_height(self, upside_down=False) -> float:
        """Effective height of the hole edges above the surface the plate lies on.
        A thin-rim plate lying first-layer side up has its rims at the top."""
        if not self.thin_rim:
            return self.thickness / 2
        return self.thickness - self.rim / 2 if upside_down else self.rim / 2

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
        hs += [(x, y, self.marker_d) for x, y in self.id_points()]
        return hs

    def validate(self):
        r, rm = self.hole_d / 2, self.marker_d / 2
        walls = {
            "X marker to grid hole": self.pitch / 2 - rm - r,
            "Y marker to grid hole": self.pitch / 2 - self.y_pair_sep / 2 - rm - r,
            "between Y markers": self.y_pair_sep - 2 * rm,
            "between grid holes": self.pitch - 2 * r,
        }
        if self.thin_rim:
            if not (0 < self.rim < self.thickness):
                raise ValueError("rim must be between 0 and the plate thickness")
            rt = self.top_d / 2
            # the cone walls taper, so only their top edge may be thinner
            walls["X marker to cone top"] = self.pitch / 2 - rm - rt
            walls["Y marker to cone top"] = self.pitch / 2 - self.y_pair_sep / 2 - rm - rt
            walls["between cone tops"] = self.pitch - 2 * rt
        bad = {k: v for k, v in walls.items() if v < (0.5 if "cone top" in k else 0.9)}
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
        d.pop("cube", None)
        # files written before thin-rim plates existed describe straight holes
        d.setdefault("rim", 0.0)
        d.setdefault("top_d", 0.0)
        return cls(**d)


# ---------------------------------------------------------------------------
# STL generation (no CAD dependency: Delaunay triangulation of the top face)
# ---------------------------------------------------------------------------

def _circle(cx, cy, d, segs):
    a = np.arange(segs) * 2 * math.pi / segs
    return np.c_[cx + d / 2 * np.cos(a), cy + d / 2 * np.sin(a)]


def _triangulate(P, holes):
    """Delaunay of outline + hole loops, minus triangles inside holes, oriented CCW (+Z)."""
    from scipy.spatial import Delaunay
    tri = Delaunay(P).simplices
    cen = P[tri].mean(axis=1)
    keep = np.ones(len(tri), bool)
    for cx, cy, ap in holes:
        keep &= np.hypot(cen[:, 0] - cx, cen[:, 1] - cy) > ap
    tri = tri[keep]
    a, b, c = P[tri[:, 0]], P[tri[:, 1]], P[tri[:, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    flip = cross < 0
    tri[flip] = tri[flip][:, [0, 2, 1]]
    return tri


def build_mesh(spec: PlateSpec, segs_big=72, segs_small=40):
    """Return (vertices (V,3), triangles (T,3)) for a watertight plate.

    Every loop (outline, each hole) has the same vertex count at each height,
    so the walls between heights are simple quad strips. Straight holes use
    heights [0, t]; thin-rim holes use [0, rim, t] with the grid holes widening
    to top_d between rim and t."""
    spec.validate()
    off = spec.edge          # front-left plate corner at (0, 0)
    t = spec.thickness
    zs = [0.0, spec.rim, t] if spec.thin_rim else [0.0, t]

    holes = spec.holes()
    segs = [segs_big if d >= spec.hole_d else segs_small for _, _, d in holes]

    def loops_at(z):
        out = [spec.outline() + off]
        for (cx, cy, d), n in zip(holes, segs):
            if spec.thin_rim and d >= spec.hole_d and z >= t:
                d = spec.top_d
            out.append(_circle(cx + off, cy + off, d, n))
        return out

    levels = [loops_at(z) for z in zs]
    sizes = [len(l) for l in levels[0]]
    per_level = sum(sizes)
    V = np.vstack([np.c_[np.vstack(L), np.full(per_level, z)] for L, z in zip(levels, zs)])
    starts = np.cumsum([0] + sizes[:-1])

    def apothems(L):
        return [(cx + off, cy + off, d_r)
                for (cx, cy, _), loop, n in zip(holes, L[1:], segs)
                for d_r in [np.hypot(*(loop[0] - [cx + off, cy + off])) * math.cos(math.pi / n)]]

    faces = []
    bottom = _triangulate(np.vstack(levels[0]), apothems(levels[0]))
    faces.append(bottom[:, [0, 2, 1]])                                   # -Z
    top = _triangulate(np.vstack(levels[-1]), apothems(levels[-1]))
    faces.append(top + per_level * (len(zs) - 1))                        # +Z

    def wall(lo, hi, idx, outward_ccw):
        k = len(idx)
        f = []
        for m in range(k):
            i, j = idx[m] + lo, idx[(m + 1) % k] + lo
            I, J = idx[m] + hi, idx[(m + 1) % k] + hi
            f += [(i, j, J), (i, J, I)] if outward_ccw else [(j, i, I), (j, I, J)]
        return np.array(f)

    for lv in range(len(zs) - 1):
        lo, hi = lv * per_level, (lv + 1) * per_level
        for li, (st, n) in enumerate(zip(starts, sizes)):
            faces.append(wall(lo, hi, np.arange(st, st + n), li == 0))   # outline faces out, holes in
    return V, np.vstack(faces)


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


# ---------------------------------------------------------------------------
# Breakaway cube: four upright walls (X-Z and Y-Z) and the XY plate as its floor
# ---------------------------------------------------------------------------

CUBE_GAP = 0.8                       # between walls at the corners (wider than an extrusion line)
CUBE_TAB_Z = (0.0, 28.0, 56.0, 84.0, 112.0)
CUBE_TAB_H = 1.0


def _place(V, F, R, t):
    """Rigidly place a mesh: rows of R give world x/y/z from local x/y/z."""
    R = np.asarray(R, float)
    V2 = V @ R.T + np.asarray(t, float)
    return V2, (F[:, [0, 2, 1]] if np.linalg.det(R) < 0 else F)


def _box(x0, x1, y0, y1, z0, z1):
    V = np.array([[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)], float)
    F = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6],      # bottom (-Z), top (+Z)
                  [0, 1, 4], [1, 5, 4], [2, 6, 3], [3, 6, 7],      # -Y, +Y
                  [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]])     # -X, +X
    return V, F


def cube_layout(wall: PlateSpec = None, floor: PlateSpec = None):
    """Footprint of the breakaway cube. Walls form a pinwheel: each is a full
    square plate, and each wall's end faces the next wall across CUBE_GAP."""
    wall = wall or PlateSpec.cube_wall()
    floor = floor or PlateSpec()
    W, t = wall.size, wall.thickness
    S = W + t + CUBE_GAP
    return dict(W=W, t=t, S=S, inner=S - 2 * t, floor=floor.size,
                floor_clearance=(S - 2 * t - floor.size) / 2)


def build_cube(floor: PlateSpec = None):
    """Return (V, F, info) for the cube: front/back walls (X-Z, one ID hole),
    left/right walls (Y-Z, two ID holes), snap-off corner tabs and the floor plate.
    The parts are separate closed shells; the tabs overlap the walls, which the
    slicer merges."""
    floor = floor or PlateSpec()
    xz, yz = PlateSpec.cube_wall(1), PlateSpec.cube_wall(2)
    L = cube_layout(xz, floor)
    W, t, S, g = L["W"], L["t"], L["S"], CUBE_GAP
    if L["floor_clearance"] < 1.0:
        raise ValueError("floor plate does not fit inside the cube")
    along_x = [[1, 0, 0], [0, 0, 1], [0, 1, 0]]     # local x -> +X, local y -> +Z, thickness -> +Y
    along_y = [[0, 0, 1], [1, 0, 0], [0, 1, 0]]     # local x -> +Y, local y -> +Z, thickness -> +X
    parts = []
    Vx, Fx = build_mesh(xz)
    Vy, Fy = build_mesh(yz)
    parts.append(_place(Vx, Fx, along_x, (0, 0, 0)))                 # front
    parts.append(_place(Vy, Fy, along_y, (S - t, 0, 0)))             # right
    parts.append(_place(Vx, Fx, along_x, (S - W, S - t, 0)))         # back
    parts.append(_place(Vy, Fy, along_y, (0, S - W, 0)))             # left
    Vf, Ff = build_mesh(floor)
    o = (S - floor.size) / 2
    parts.append((Vf + [o, o, 0.0], Ff))
    e, c = 0.4, 0.3                                                  # tab overlap into walls, inset across thickness
    for z0 in CUBE_TAB_Z:
        z1 = z0 + CUBE_TAB_H
        parts.append(_box(W - e, S - t + e, c, t - c, z0, z1))          # front-right
        parts.append(_box(S - t + c, S - c, W - e, S - t + e, z0, z1))  # right-back
        parts.append(_box(t - e, S - W + e, S - t + c, S - c, z0, z1))  # back-left
        parts.append(_box(c, t - c, t - e, S - W + e, z0, z1))          # left-front
    V, F, off = [], [], 0
    for Vp, Fp in parts:
        V.append(Vp); F.append(Fp + off); off += len(Vp)
    return np.vstack(V), np.vstack(F), L
