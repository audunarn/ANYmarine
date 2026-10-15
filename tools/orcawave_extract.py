"""Extract an OrcaWave results file (.owr) into neutral SI benchmark data.

Standalone tool for the OrcaWave benchmark (docs/IMPLEMENTATION_PLAN.md, "OrcaWave
benchmark B"). Run it with a Python that has Orcina's OrcFxAPI and an OrcaWave licence::

    python tools/orcawave_extract.py <results.owr> <output directory>

ANYmarine itself never imports OrcFxAPI; it reads the JSON written here. Values are
copied as OrcFxAPI reports them, converted only from the file's SI-with-tonnes units
(te, kN, m) to SI (kg, N, m). Complex RAOs keep OrcFxAPI's own complex convention; the
mapping to ANYmarine's ``x = Re{X exp(-i w t)}`` is defined and proved separately
(benchmark slice B2), never assumed here. Rotational displacement RAOs are in rad/m.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

SCHEMA = 'orcawave_reference_v1'
TONNE, KILONEWTON = 1000.0, 1000.0
INFINITE_DEPTH = 1e300
# OrcaWave BodyMeshSymmetry -> (OrcFxAPI PanelMeshSymmetry, exported mesh file name)
MESH_SYMMETRY = {
    'xz and yz planes': ('XZYZ', 'body_mesh_quarter_xz_yz.gdf'),
    'xz plane': ('XZ', 'body_mesh_half_xz.gdf'),
    'yz plane': ('YZ', 'body_mesh_half_yz.gdf'),
    'None': (None, 'body_mesh_full.gdf'),     # no OrcFxAPI export: written from the panel geometry
}


def _pairs(array):
    return [[float(z.real), float(z.imag)] for z in array]


def _matrix(m, scale=1.0):
    return [[float(v) * scale for v in row] for row in m]


def _mean_drift(d):
    """OrcaWave mean drift loads per unit wave amplitude squared, SI (kN -> N, kN.m -> N.m), or None.

    Methods are included only when OrcaWave calculated them (an empty array is omitted). Values stay complex as
    reported ([heading pair][period][dof] [re, im]); equal-heading pairs are real by construction.
    """
    import numpy as np
    pairs = np.asarray(d.meanDriftHeadingPairs, dtype=float)
    if pairs.ndim != 2 or pairs.size == 0:
        return None
    methods = {}
    for name, attribute in (('control_surface', 'meanDriftLoadControlSurface'),
                            ('momentum_conservation', 'meanDriftLoadMomentumConservation'),
                            ('pressure_integration', 'meanDriftLoadPressureIntegration')):
        values = getattr(d, attribute, None)
        array = None if values is None else np.asarray(values)
        if array is None or array.ndim != 3 or array.size == 0:
            continue
        si = array.astype(complex) * KILONEWTON
        methods[name] = [[_pairs(row) for row in per_pair] for per_pair in si]
    if not methods:
        return None
    return dict(heading_pairs_deg=[[float(a), float(b)] for a, b in pairs], methods=methods,
                units='N/m^2 (forces), N.m/m^2 (moments) per unit wave amplitude squared',
                layout='[heading pair][period][dof] complex [re, im] as reported by OrcFxAPI; periods as periods_s')


def _optional_text(value):
    return None if value is None else str(value)


def _external(d, name):
    """A single body's 6x6 external matrix in SI (file force units kN -> N; rows by GetData index)."""
    columns = ('x', 'y', 'z', 'Rx', 'Ry', 'Rz')
    return [[float(d.GetData(name + c, row)) * KILONEWTON for c in columns] for row in range(6)]


def write_gdf(path: Path, panel_geometry, g: float) -> None:
    """WAMIT GDF (ULEN 1, no symmetry) from OrcFxAPI panelGeometry; a triangle repeats its last vertex."""
    lines = ['OrcaWave panel geometry (wet panels, no symmetry)', f'1 {g!r} ULEN GRAV', '0 0 ISX ISY',
             f'{len(panel_geometry)} NPAN']
    for row in panel_geometry:
        corners = [[float(v) for v in vertex] for vertex in row['vertices'] if all(math.isfinite(float(v)) for v in vertex)]
        if len(corners) not in (3, 4):
            raise SystemExit(f'panel with {len(corners)} vertices cannot be written as GDF')
        while len(corners) < 4:
            corners.append(corners[-1])
        lines += [' '.join(repr(v) for v in vertex) for vertex in corners]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def extract(owr: Path, out: Path) -> dict:
    import numpy as np
    import OrcFxAPI as ofx

    raw = owr.read_bytes()
    d = ofx.Diffraction(str(owr))
    units = (d.UnitsSystem, d.LengthUnits, d.MassUnits, d.ForceUnits)
    if units != ('SI', 'm', 'te', 'kN'):
        raise SystemExit(f'unsupported OrcaWave units {units}; expected SI with m, te, kN')
    if d.NumberOfBodies != 1:
        raise SystemExit('benchmark B extracts single-body results only')
    periods = np.asarray(d.periods, dtype=float)
    headings = np.asarray(d.headings, dtype=float)
    depth = float(d.WaterDepth)
    out.mkdir(parents=True, exist_ok=True)
    # The mesh is saved with the body's own symmetry (WAMIT GDF): xz -> ISY = 1, yz -> ISX = 1.
    symmetry_name = str(d.GetData('BodyMeshSymmetry', 0))
    symmetry, mesh_name = MESH_SYMMETRY[symmetry_name]
    if symmetry is None:
        # SaveSymmetrisedBodyMesh admits XZ, YZ and XZYZ only; write OrcaWave's own (wet) panels instead.
        write_gdf(out / mesh_name, d.panelGeometry, float(d.g))
    else:
        d.SaveSymmetrisedBodyMesh(getattr(ofx.PanelMeshSymmetry, symmetry), 1e-6, str(out / mesh_name))
    h = d.hydrostaticResults[0]
    added = np.asarray(d.addedMass, dtype=float) * TONNE          # te, te.m, te.m^2 -> kg, kg.m, kg.m^2
    damping = np.asarray(d.damping, dtype=float) * KILONEWTON     # kN.s/m (and moments) -> N.s/m
    load_diffraction = np.asarray(d.loadRAOsDiffraction) * KILONEWTON
    load_haskind = np.asarray(d.loadRAOsHaskind) * KILONEWTON
    displacement = np.asarray(d.displacementRAOs)
    body = dict(
        name=str(d.GetData('BodyName', 0)),
        mesh_source=str(d.GetData('BodyMeshFileName', 0)),
        mesh_source_format=str(d.GetData('BodyMeshFormat', 0)),
        mesh_source_symmetry=str(d.GetData('BodyMeshSymmetry', 0)),
        mesh_exported=mesh_name,
        interior_surface_panels=str(d.GetData('BodyAddInteriorSurfacePanels', 0)),
        volume_m3=float(h['volume']),
        centre_of_buoyancy_m=[float(v) for v in h['centreOfBuoyancy']],
        mass_kg=float(h['mass']) * TONNE,
        centre_of_mass_m=[float(v) for v in h['centreOfMass']],
        restoring_matrix_si=_matrix(h['restoringMatrix'], KILONEWTON),
        inertia_matrix_si=_matrix(h['inertiaMatrix'], TONNE),
        waterplane_area_m2=float(h['Awp']),
        # Body inputs that enter OrcaWave's displacement RAOs besides A, B, loads, inertia and restoring.
        external_stiffness_si=_external(d, 'BodyExternalStiffnessMatrix'),
        external_stiffness_origin=str(d.GetData('BodyExternalStiffnessMatrixOriginType', 0)),
        external_damping_si=_external(d, 'BodyExternalDampingMatrix'),
        external_damping_origin=str(d.GetData('BodyExternalDampingMatrixOriginType', 0)),
        increase_roll_damping_to_target=str(d.GetData('BodyIncreaseRollDampingToTarget', 0)),
        # OrcaWave reports NaN when no body position applies; JSON gets null (strict parsers).
        position_m=None if not all(math.isfinite(float(v)) for v in h['position']) else [float(v) for v in h['position']],
    )
    data = dict(
        schema=SCHEMA,
        source=dict(path=str(owr), sha256=hashlib.sha256(raw).hexdigest(), orcawave_dll=str(ofx.DLLVersion()),
                    file_units=dict(system='SI', length='m', mass='te', force='kN')),
        environment=dict(water_depth_m=None if depth >= INFINITE_DEPTH else depth,
                         water_density_kg_m3=float(d.WaterDensity) * TONNE, g_m_s2=float(d.g)),
        settings=dict(solve_type=str(d.SolveType), load_rao_method=str(d.LoadRAOCalculationMethod),
                      # None (JSON null) when only one load method was calculated
                      preferred_load_rao_method=_optional_text(d.GetData('PreferredLoadRAOCalculationMethod', -1))),
        body=body,
        periods_s=[float(t) for t in periods],
        omegas_rad_s=[2.0 * math.pi / float(t) for t in periods],
        headings_deg=[float(b) for b in headings],
        added_mass_si=[_matrix(m) for m in added],
        damping_si=[_matrix(m) for m in damping],
        displacement_raos=[[_pairs(row) for row in per_heading] for per_heading in displacement],
        load_raos_diffraction_si=[[_pairs(row) for row in per_heading] for per_heading in load_diffraction],
        load_raos_haskind_si=[[_pairs(row) for row in per_heading] for per_heading in load_haskind],
        mean_drift=_mean_drift(d),
        layout=dict(raos='[heading][period][dof] complex [re, im] as reported by OrcFxAPI',
                    dofs=['surge', 'sway', 'heave', 'roll', 'pitch', 'yaw'],
                    matrices='[period][6][6]', rotation_units='rad (displacement RAOs rad/m)'),
        conventions=dict(complex='as reported by OrcFxAPI (OrcaWave convention); mapping to ANYmarine '
                                 'x = Re{X exp(-i w t)} is defined and proved in benchmark slice B2'),
    )
    # allow_nan=False: any other non-finite value is a refusal, never a nonstandard JSON token.
    (out / 'orcawave_reference.json').write_text(json.dumps(data, indent=1, allow_nan=False), encoding='utf-8')
    return data


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        raise SystemExit(__doc__)
    data = extract(Path(argv[0]), Path(argv[1]))
    print(f"wrote {argv[1]}: {len(data['periods_s'])} periods, {len(data['headings_deg'])} headings, "
          f"sha256 {data['source']['sha256'][:12]}")


if __name__ == '__main__':
    main()
