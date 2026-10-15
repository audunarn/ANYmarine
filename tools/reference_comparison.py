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
    if isx:
        panels += [p * np.array([-1.0, 1.0, 1.0]) for p in list(panels)]
    if isy:
        panels += [p * np.array([1.0, -1.0, 1.0]) for p in list(panels)]
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


def orcawave_parts(directory: Path, body_name: str | None = None) -> tuple[dict, dict, list, list]:
    ref = json.loads((directory / 'orcawave_reference.json').read_text(encoding='utf-8'))
    positions, triangles = read_gdf(directory / ref['body']['mesh_exported'])
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
    return body, source, ref['periods_s'], ref['headings_deg']


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
    body, reference, periods, headings = orcawave_parts(args.orcawave, args.body_name)
    sources = [reference] + [npz_source(path, periods, headings) for path in args.source]
    data = build(title=args.title, notes=args.note, body=body, periods_s=periods, headings_deg=headings,
                 sources=sources)
    write(data, args.out)
    print(f'wrote {args.out}: {len(sources)} sources, {len(periods)} periods, {len(headings)} headings, '
          f'hash {data["artifact_hash"][:12]}')


if __name__ == '__main__':
    main()
