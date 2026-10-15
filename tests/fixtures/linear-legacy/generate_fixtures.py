"""Freeze the legacy four-DOF linear route (ADR-015 section 1) - run ONCE from main before L6-1 changes.

    PYTHONPATH=src python tests/fixtures/linear-legacy/generate_fixtures.py

Captured from ANYmarine main 85b22fb (2026-10-15): the analytic demo inputs, their input hash and a demo run
result; the regular-wave assessment rehearsal of tests/test_linear_assessment.py (project saved on disk with
inputs and result, case, report); with the argument `hydro` (run in a main worktree, after review round 1) the
raw and derived hydro-imported projects of tests/test_hydro_bridge.py / tests/test_derived_import.py. tests/test_linear_legacy_fixtures.py proves that later code reproduces them.
Never regenerate on a branch that changes the linear route.
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'tests'))

from anymarine.domain import default_two_body_project               # noqa: E402
from anymarine.linear import analytic_demo_inputs, linear_input_hash, run_linear   # noqa: E402
from anymarine.services import ApplicationService                   # noqa: E402
from test_linear_assessment import MODE_REGULAR_WAVE, WINDOWS, _regular_wave_inputs, _spec   # noqa: E402


def dump(name, data):
    (HERE / name).write_bytes((json.dumps(data, indent=1, sort_keys=True) + '\n').encode('utf-8'))


def main():
    project = default_two_body_project()
    inputs = analytic_demo_inputs(project)
    dump('demo_inputs.json', dict(inputs=inputs.to_dict(), input_hash=linear_input_hash(inputs)))
    result = run_linear(inputs, result_id='legacy-demo', created_utc='2026-10-15T00:00:00Z')
    dump('demo_result.json', result.to_dict())

    work = HERE / '_work'
    shutil.rmtree(work, ignore_errors=True)
    service = ApplicationService(work)
    project = service.new_project('Regular wave rehearsal')
    rw_inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, rw_inputs)
    service.save(project, root=work)
    case = service.prepare_linear_assessment(project, _spec('rw-pass', MODE_REGULAR_WAVE, rw_inputs, windows=WINDOWS))
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == 'pass', report.failed
    project = service.add_result(project, result)
    service.save(project, root=work)
    dump('rw_assessment.json', dict(input_hash=linear_input_hash(rw_inputs), case=case.to_dict(),
                                     case_hash=case.case_hash, report=report.to_dict(), result=result.to_dict()))
    target = HERE / 'rw_project'
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(work, target)
    shutil.rmtree(work)
    print('frozen', sorted(p.name for p in HERE.iterdir()))


def hydro(tmp):
    """Raw and derived hydro-imported projects (test_hydro_bridge / test_derived_import fixtures)."""
    import anyhydro
    import test_derived_import as td
    import test_hydro_bridge as tb

    out = {}
    for label, module in (('raw', tb), ('derived', td)):
        project = module._fixture_project()
        art = tmp / label
        art.mkdir(parents=True)
        if label == 'raw':
            dataset = module._fixture_dataset(project)
            kernel = module._fixture_kernel(dataset)
            anyhydro.save_hydro_dataset(dataset, art / 'dataset.nc')
            anyhydro.save_radiation_kernel(kernel, art / 'kernel.nc')
        else:
            dataset = module._fixture_dataset(project)
            artifact = anyhydro.derived_operators_from_dataset(dataset, module._derived_settings())
            kernel = module._derived_kernel(artifact, dataset)
            anyhydro.save_hydro_dataset(dataset, art / 'dataset.nc')
            anyhydro.save_derived_operators(artifact, art / 'operators.npz')
            anyhydro.save_radiation_kernel(kernel, art / 'kernel.nc')
        root = tmp / f'{label}_project'
        service = ApplicationService(root)
        service.save(project, root=root)
        options = module._base_options()
        if label == 'raw':
            new = service.import_hydro_inputs(project, art / 'dataset.nc', art / 'kernel.nc', options)
        else:
            new = service.import_derived_hydro_inputs(project, art / 'dataset.nc', art / 'operators.npz',
                                                      art / 'kernel.nc', options)
        service.save(new, root=root)
        target = HERE / f'{label}_hydro_project'
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(root, target)
        out[label] = dict(input_hash=linear_input_hash(new.linear_inputs),
                          provenance=new.hydro_provenance.to_dict())
    dump('hydro_projects.json', out)


if __name__ == '__main__':
    if sys.argv[1:] == ['hydro']:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            hydro(Path(tmp))
    else:
        main()
