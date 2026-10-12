"""Regenerate the bundled regular-wave replay example (development, unqualified).

Solves ``native_examples.regular_wave_mooring_project`` through the same
snapshot, compiler, solver and evidence-to-record path as the native worker
and stores project + result with ProjectStore. Refuses an existing target.

    python tools/build_regular_wave_example.py [target-directory]
"""
from datetime import datetime, timezone
from pathlib import Path
import sys


def build(target):
    from anymarine.mechanics import prepare_native_nodes, prepare_native_run
    from anymarine.native_examples import regular_wave_mooring_project
    from anymarine.native_jobs import project_from_snapshot, record_from_evidence
    from anymarine.persistence import ProjectStore
    from anymarine.services import ApplicationService
    import anysolver.marine_mechanical as solver

    target = Path(target)
    if target.exists():
        raise SystemExit(f'{target} exists; evidence is never overwritten')
    project = regular_wave_mooring_project()
    steps = project.native_inputs.run_spec['steps']
    snapshot = ApplicationService().native_input_snapshot(project, max_frames=steps + 1, wall_seconds=600.)
    produced = project_from_snapshot(snapshot)
    model, states, spec = prepare_native_run(produced)
    result = solver.solve_marine_mechanical(model, states, spec, initial_nodes=prepare_native_nodes(produced))
    if result.status != 'completed':
        raise SystemExit(f'native solve did not complete: {result.status}')
    record = record_from_evidence(solver.result_to_dict(result), snapshot,
        result_id='regular-wave-moored-cubes',
        note='Native wave member/line hydrodynamics in one regular Airy wave, development, unqualified; '
             'contact/equilibrium/W6 incomplete.',
        created_utc=datetime.now(timezone.utc).isoformat())
    ProjectStore(target).save(project.with_edits(results=(record,)))
    return target


if __name__ == '__main__':
    default = Path(__file__).resolve().parents[1] / 'src/anymarine/examples/regular-wave-replay'
    print(build(sys.argv[1] if len(sys.argv) > 1 else default))
