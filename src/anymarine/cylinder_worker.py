"""Isolated cylinder coefficient job. No Qt and no numerical import on base import."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time


def run(input_path, output_path):
    from .domain import ProjectModel, Body, EquilibriumMetadata, AnalysisSettings
    from .hashing import input_hash
    from .cylinder_heave_plate import solve_project_coefficients
    from anyhydro import save_hydro_dataset, load_hydro_dataset, dataset_content_hash
    path = Path(input_path)
    if path.stat().st_size > 32*1024*1024:
        raise ValueError('Cylinder input snapshot exceeds 32 MiB')
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    if set(snapshot) != {'project', 'input_hash', 'periods_s'}:
        raise ValueError('Invalid cylinder job snapshot')
    data = snapshot['project']
    required = {'name', 'bodies', 'equilibrium', 'settings', 'results', 'linear_inputs', 'hydro_provenance'}
    if (not isinstance(data, dict) or set(data) != required
            or data['linear_inputs'] is not None or data['hydro_provenance'] is not None):
        raise ValueError('Cylinder coefficient snapshot must contain only its producing project inputs')
    project = ProjectModel(data['name'], tuple(Body.from_dict(b) for b in data['bodies']),
        equilibrium=EquilibriumMetadata.from_dict(data['equilibrium']),
        settings=AnalysisSettings.from_dict(data['settings']))
    if input_hash(project) != snapshot['input_hash']:
        raise ValueError('Cylinder job snapshot input hash mismatch')
    started = time.perf_counter()
    dataset = solve_project_coefficients(project, periods_s=snapshot['periods_s'])
    dataset.attrs['anymarine_input_hash'] = snapshot['input_hash']
    content_hash = dataset_content_hash(dataset)
    target = Path(output_path)
    save_hydro_dataset(dataset, target)
    retained = load_hydro_dataset(target)
    if retained.attrs.get('anymarine_input_hash') != snapshot['input_hash']:
        raise ValueError('Saved coefficient artifact lost selected-input identity')
    if dataset_content_hash(retained) != content_hash:
        raise ValueError('Saved coefficient artifact changed during persistence')
    rows = []
    for i, omega in enumerate(retained.omega.values):
        force = complex(retained.excitation_force.values[i, 0, 2])
        rows.append(dict(period_s=2*math.pi/float(omega),
            added_mass_heave_kg=float(retained.added_mass.values[i, 2, 2]),
            damping_heave_kg_s=float(retained.radiation_damping.values[i, 2, 2]),
            excitation_heave_N_m=[force.real, force.imag]))
    receipt = dict(status='completed', input_hash=snapshot['input_hash'],
        dataset_hash=content_hash,
        process_solve_and_save_seconds=time.perf_counter()-started, artifact=str(target),
        qualification='development_unqualified', rows=rows)
    target.with_suffix('.receipt.json').write_text(json.dumps(receipt, indent=2, allow_nan=False), encoding='utf-8')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.input, args.output), allow_nan=False), flush=True)
    except Exception as error:
        print(json.dumps(dict(status='failed', error_type=type(error).__name__, error=str(error))), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
