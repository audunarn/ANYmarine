"""Build an ``anymarine_reference_comparison_v1`` artifact (see src/anymarine/reference_comparison.py).

    python tools/reference_comparison.py --orcawave <extract dir> [--source <rao.npz> ...]
        --title "..." [--note "..."] --out <reference_comparison.json>

``--orcawave`` is a directory written by tools/orcawave_extract.py. It supplies the body (name, mass, centre
of mass, display mesh from the exported GDF, mirrored by its ISX/ISY flags) and the OrcaWave displacement RAOs,
converted to ANYmarine's ``x = Re{X exp(-i w t)}`` convention by complex conjugation (OrcaWave benchmark slice
B2). Each ``--source`` is an .npz with ``periods_s`` (P), ``headings_deg`` (H), ``raos`` (H x P x 6 complex,
ANYmarine convention, rad/m for rotations) and ``meta`` (a JSON string with label, program, version, method and
an optional details object); periods and headings must equal the OrcaWave ones.

The output may contain licensed third-party results: write it outside any repository.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from anymarine.reference_comparison import build, write  # noqa: E402


def read_gdf(path: Path) -> tuple[list, list]:
    """WAMIT GDF panels mirrored by ISX/ISY -> (positions, triangles) for display.

    Vertex coordinates are dimensional; ULEN is WAMIT's normalisation length and is not applied.
    """
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    isx, isy = (int(v) for v in lines[2].split()[:2])
    npan = int(lines[3].split()[0])
    numbers = [float(v) for line in lines[4:] for v in line.split()]
    if len(numbers) < npan * 12:
        raise SystemExit(f'{path}: {npan} panels declared but only {len(numbers) // 12} present')
    corners = np.asarray(numbers[:npan * 12], dtype=float).reshape(npan, 4, 3)
    panels = [corners]
    # each reflection reverses the vertex order, so mirrored panels keep the orientation of their originals
    if isx:
        panels += [p[:, ::-1] * np.array([-1.0, 1.0, 1.0]) for p in list(panels)]
    if isy:
        panels += [p[:, ::-1] * np.array([1.0, -1.0, 1.0]) for p in list(panels)]
    positions, triangles, index = [], [], {}

    def vid(point):
        key = tuple(np.round(point, 9))
        if key not in index:
            index[key] = len(positions)
            positions.append([float(v) for v in point])
        return index[key]

    for block in panels:
        for quad in block:
            ids = [vid(p) for p in quad]
            for tri in ((ids[0], ids[1], ids[2]), (ids[0], ids[2], ids[3])):
                if len(set(tri)) == 3:
                    triangles.append(list(tri))
    return positions, triangles


def _ear_clip(loop: list, points: np.ndarray) -> list:
    """Triangles of a simple polygon (plan view, counter-clockwise loop of vertex ids)."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= -1e-12 and cross(b, c, p) >= -1e-12 and cross(c, a, p) >= -1e-12

    n = len(loop)

    def crosses(p1, p2, p3, p4):
        d1, d2, d3, d4 = cross(p3, p4, p1), cross(p3, p4, p2), cross(p1, p2, p3), cross(p1, p2, p4)
        return ((d1 > 1e-12 and d2 < -1e-12) or (d1 < -1e-12 and d2 > 1e-12)) and \
               ((d3 > 1e-12 and d4 < -1e-12) or (d3 < -1e-12 and d4 > 1e-12))

    for i in range(n):                                    # simple polygon: no two non-adjacent edges cross
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if crosses(points[loop[i]], points[loop[(i + 1) % n]], points[loop[j]], points[loop[(j + 1) % n]]):
                raise ValueError('deck outline is not a simple polygon')
    ring, out = list(loop), []
    while len(ring) > 3:
        for i in range(len(ring)):
            a, b, c = ring[i - 1], ring[i], ring[(i + 1) % len(ring)]
            pa, pb, pc = points[a], points[b], points[c]
            if cross(pa, pb, pc) <= 1e-12:
                continue
            if any(inside(points[q], pa, pb, pc) for q in ring if q not in (a, b, c)):
                continue
            out.append([a, b, c])
            ring.pop(i)
            break
        else:
            raise ValueError('deck outline is not a simple polygon')
    if cross(points[ring[0]], points[ring[1]], points[ring[2]]) <= 1e-12:
        raise ValueError('deck outline leaves a degenerate triangle')
    out.append(ring)
    return out


def display_mesh(path: Path) -> tuple[list, list]:
    """Closed, outward-oriented display geometry: the GDF's wet panels (z <= 0), vertical topsides from the
    waterline to the mesh's highest point and a flat deck. Display only - the hydrodynamics never use it.
    Falls back to the plain GDF surface when the wet part has no single closed waterline.
    """
    positions, triangles = read_gdf(path)
    v = np.asarray(positions)
    wet = [tri for tri in triangles if np.all(v[tri, 2] <= 1e-9)]
    edges = {}
    for a, b, c in wet:
        for e in ((a, b), (b, c), (c, a)):
            edges[e] = edges.get(e, 0) + 1
    boundary = [e for e in edges if (e[1], e[0]) not in edges]
    following = {a: b for a, b in boundary}
    if not boundary or len(following) != len(boundary) or any(abs(v[a, 2]) > 1e-9 for a, _ in boundary):
        return positions, triangles
    loop, start = [boundary[0][0]], boundary[0][0]
    while following[loop[-1]] != start:
        loop.append(following[loop[-1]])
        if len(loop) > len(boundary):
            return positions, triangles
    if len(loop) != len(boundary):
        return positions, triangles                       # several waterline loops (e.g. multi-hull)
    freeboard = float(max(v[:, 2].max(), 0.0)) or 1.0
    out_positions = [list(p) for p in v]
    top = {}
    for a in loop:
        top[a] = len(out_positions)
        out_positions.append([v[a, 0], v[a, 1], freeboard])
    out = [list(tri) for tri in wet]
    for a in loop:                                        # sides traverse each waterline edge opposite to the hull
        b = following[a]
        out += [[b, a, top[a]], [b, top[a], top[b]]]
    # the deck traverses its edges opposite to the sides (which run top[a] -> top[b]): ring = reversed loop
    plan = np.asarray(out_positions)[:, :2]
    ring = [top[a] for a in reversed(loop)]
    area = sum(plan[ring[i - 1], 0] * plan[ring[i], 1] - plan[ring[i], 0] * plan[ring[i - 1], 1]
               for i in range(len(ring)))
    try:
        if area > 0:
            out += _ear_clip(ring, plan)
        else:                                             # clip counter-clockwise, then restore the ring's winding
            out += [[a, c, b] for a, b, c in _ear_clip(ring[::-1], plan)]
    except ValueError:
        return positions, triangles                       # display-only fallback: the plain GDF surface
    pts = np.asarray(out_positions)
    volume = sum(np.dot(pts[a], np.cross(pts[b], pts[c])) for a, b, c in out) / 6.0
    if volume < 0:
        out = [[a, c, b] for a, b, c in out]
    return [[float(x) for x in p] for p in out_positions], [[int(i) for i in tri] for tri in out]


def orcawave_parts(directory: Path, body_name: str | None = None) -> tuple[dict, dict, list, list]:
    ref = json.loads((directory / 'orcawave_reference.json').read_text(encoding='utf-8'))
    positions, triangles = display_mesh(directory / ref['body']['mesh_exported'])
    body = dict(name=body_name or ref['body']['name'],
                mass_kg=ref['body']['mass_kg'], centre_of_mass_m=ref['body']['centre_of_mass_m'],
                display_mesh=dict(positions=positions, triangles=triangles))
    raos = np.asarray(ref['displacement_raos'], dtype=float)          # [H][P][6][re, im]
    conj = [[[[z[0], -z[1]] for z in row] for row in block] for block in raos.tolist()]
    # the preferred method when both were calculated, otherwise the single calculated method
    method = ref['settings'].get('preferred_load_rao_method') or ref['settings'].get('load_rao_method', 'unknown')
    source = dict(label='OrcaWave', program='OrcaWave', version=ref['source']['orcawave_dll'],
                  method=f'potential flow, displacement RAOs from {method} loads',
                  details=dict(file_sha256=ref['source']['sha256'][:16],
                               interior_surface_panels=ref['body']['interior_surface_panels'],
                               convention='conjugated to x = Re{X exp(-i w t)}'), raos=conj)
    return body, source, ref['periods_s'], ref['headings_deg'], ref.get('environment', {}).get('water_depth_m')


def npz_source(path: Path, periods: list, headings: list) -> dict:
    data = np.load(path, allow_pickle=False)
    meta = json.loads(str(data['meta']))
    for name, grid in (('periods_s', periods), ('headings_deg', headings)):
        values = np.asarray(data[name], dtype=float)
        if values.ndim != 1 or values.shape[0] != len(grid) or not np.array_equal(values, np.asarray(grid, float)):
            raise SystemExit(f'{path}: {name} differ from the OrcaWave reference (exact match required)')
    raos = np.asarray(data['raos'], dtype=complex)
    if raos.shape != (len(headings), len(periods), 6):
        raise SystemExit(f'{path}: raos must be {len(headings)} x {len(periods)} x 6, got {raos.shape}')
    pairs = [[[[float(z.real), float(z.imag)] for z in row] for row in block] for block in raos]
    return dict(label=meta['label'], program=meta['program'], version=meta['version'], method=meta['method'],
                details=meta.get('details', {}), raos=pairs)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--orcawave', required=True, type=Path)
    parser.add_argument('--source', action='append', default=[], type=Path)
    parser.add_argument('--title', required=True)
    parser.add_argument('--body-name', default=None, help="display name (default: OrcaWave's body name)")
    parser.add_argument('--note', action='append', default=[])
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    body, reference, periods, headings, depth = orcawave_parts(args.orcawave, args.body_name)
    sources = [reference] + [npz_source(path, periods, headings) for path in args.source]
    data = build(title=args.title, notes=args.note, body=body, periods_s=periods, headings_deg=headings,
                 sources=sources, environment=dict(water_depth_m=depth))
    write(data, args.out)
    print(f'wrote {args.out}: {len(sources)} sources, {len(periods)} periods, {len(headings)} headings, '
          f'hash {data["artifact_hash"][:12]}')


if __name__ == '__main__':
    main()
