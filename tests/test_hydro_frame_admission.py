"""Public marine admission regressions; synthetic inputs, no backend execution."""
import numpy as np
import pytest
h = pytest.importorskip("anyhydro")
from anyhydro.fixtures import make_two_body_fixture
from anymarine.domain import default_two_body_project
from anymarine.hashing import input_hash
from anymarine.hydro import (import_hydro_inputs, import_derived_hydro_inputs,
                             HydroGeometryError, HydroUnavailableError)

def source_files(tmp_path, rotated=True):
    ds = make_two_body_fixture()
    if rotated:
        E = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        ds = h.rotate_axes(ds, {str(b): E for b in ds.body.values})
    dataset = tmp_path/'dataset.nc'
    h.save_hydro_dataset(ds, dataset)
    kernel, operators = tmp_path/'kernel.nc', tmp_path/'operators.npz'
    # Guards must refuse before attempting to decode either unrelated sidecar.
    kernel.write_bytes(b'not decoded')
    operators.write_bytes(b'not decoded')
    return dataset, kernel, operators


OPTIONS = {'n_steps': 2, 'criteria': dict(omega_min=.5, omega_max=1.,
           added_mass_abs_tol=1., added_mass_rel_tol=.1,
           damping_abs_tol=1., damping_rel_tol=.1)}


def test_raw_public_loader_refuses_local_axes_before_kernel_decode(tmp_path):
    dataset, kernel, _ = source_files(tmp_path)
    project = default_two_body_project()
    original = input_hash(project)
    with pytest.raises(HydroGeometryError, match='GLOBAL coefficient axes'):
        import_hydro_inputs(project, dataset, kernel, OPTIONS)
    assert input_hash(project) == original


def test_derived_public_loader_refuses_local_axes_before_operator_decode(tmp_path):
    dataset, kernel, operators = source_files(tmp_path)
    project = default_two_body_project()
    original = input_hash(project)
    with pytest.raises(HydroGeometryError, match='GLOBAL coefficient axes'):
        import_derived_hydro_inputs(project, dataset, operators, kernel, OPTIONS)
    assert input_hash(project) == original


def test_missing_frame_api_is_actionable_public_refusal(tmp_path, monkeypatch):
    dataset, kernel, _ = source_files(tmp_path, rotated=False)
    monkeypatch.delattr(h, 'require_global_dof_axes')
    with pytest.raises(HydroUnavailableError, match='tested ANYhydro wheel'):
        import_hydro_inputs(default_two_body_project(), dataset, kernel, OPTIONS)

