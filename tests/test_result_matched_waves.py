"""Matched incident waves for hydro linear results: the recorded forcing wave.

The displayed elevation must be the wave that forced the result: recorded
amplitude/omega/heading/phase, the producing dataset's own depth and
gravity, the forcing's exp(-i omega t + i phase) convention and the
dataset's phase origin. Development tests; analytic fixtures only.
"""
import dataclasses
import math

import numpy as np
import pytest

import anyhydro
from anyhydro.excitation_source import attach_excitation_source

from conftest import requires_qt, requires_solver
from test_derived_import import (
    DIRECTIONS, GRAVITY, WATER_DEPTH, _base_options, _derived_kernel, _derived_settings,
    _fixture_bodies, _fixture_dataset, _fixture_project, _import,
    derived_artifacts,  # noqa: F401  (module fixture: dataset without a source request)
)
from anymarine.services import ServiceError
from anymarine.wave_match import RegularIncidentWave, dispersion_wavenumber

WAVE = {"omega": 1.3, "heading": float(DIRECTIONS[1]), "amplitude": 0.8, "phase": 0.25}


@pytest.mark.parametrize("depth", [2.0, 20.0, 150.0])
def test_dispersion_and_elevation_match_the_mechanical_environment(depth):
    from anyloads.marine import AiryComponent, AiryEnvironment
    wave = RegularIncidentWave(amplitude=.7, omega=1.1, heading=math.radians(30.), phase=.4,
                               water_depth=depth, gravity=9.81)
    env = AiryEnvironment(depth, (AiryComponent(.7, 1.1, 30., .4),), gravity=9.81)
    assert wave.wavenumber == pytest.approx(env.wave_numbers[0], rel=1e-13)
    points = np.random.default_rng(3).uniform(-20., 20., (50, 2))
    for t in (0., .37, 5.):
        np.testing.assert_allclose(wave.surface_elevation(points, t), env.surface_elevation(points, t),
                                   atol=1e-12, rtol=0.)


def test_deep_water_and_refusals():
    assert dispersion_wavenumber(2., math.inf, 9.81) == 4. / 9.81
    assert dispersion_wavenumber(2., 1e4, 9.81) == pytest.approx(4. / 9.81, rel=1e-15)
    for bad in (dict(omega=0.), dict(gravity=-1.), dict(water_depth=0.), dict(amplitude=-.1),
                dict(phase=float('nan'))):
        with pytest.raises(ValueError):
            RegularIncidentWave(**{**dict(amplitude=1., omega=1., heading=0., phase=0.,
                                          water_depth=10., gravity=9.81), **bad})


@pytest.fixture(scope="module")
def contract_artifacts(tmp_path_factory):
    """Derived triple from a dataset carrying its excitation-source request."""
    root = tmp_path_factory.mktemp("contract-artifacts")
    project = _fixture_project()
    raw = _fixture_dataset(project)
    request = anyhydro.HydroRequest(
        omegas=raw.omega.values.copy(), wave_directions=DIRECTIONS.copy(), water_depth=WATER_DEPTH,
        rho=1025.0, gravity=GRAVITY, include_zero_frequency=False, include_infinite_frequency=False,
        backend_settings={"fixture": "derived-import-analytic"})
    dataset = attach_excitation_source(raw, request, _fixture_bodies(project),
                                       convention_evidence="analytic test fixture declaration")
    artifact = anyhydro.derived_operators_from_dataset(dataset, _derived_settings())
    kernel = _derived_kernel(artifact, dataset)
    paths = {name: root / name for name in ("dataset.nc", "operators.npz", "kernel.nc")}
    anyhydro.save_hydro_dataset(dataset, paths["dataset.nc"])
    anyhydro.save_derived_operators(artifact, paths["operators.npz"])
    anyhydro.save_radiation_kernel(kernel, paths["kernel.nc"])
    return {"project": project, "dataset_path": paths["dataset.nc"],
            "operators_path": paths["operators.npz"], "kernel_path": paths["kernel.nc"]}


@requires_solver
def test_result_wave_is_the_recorded_forcing_wave(tmp_path, contract_artifacts):
    service, project = _import(tmp_path, contract_artifacts, _base_options(wave=WAVE))
    result = service.run_linear_local(project)
    wave = service.result_incident_wave(project, result)
    assert (wave.amplitude, wave.omega, wave.heading, wave.phase) == (
        WAVE["amplitude"], WAVE["omega"], WAVE["heading"], WAVE["phase"])
    assert (wave.water_depth, wave.gravity) == (WATER_DEPTH, GRAVITY)
    # Same convention as the forcing: eta(origin, t) = A Re{exp(-i w t + i phase)}.
    times = np.asarray(result.times)
    origin = np.zeros((1, 2))
    eta = np.array([wave.surface_elevation(origin, t)[0] for t in times])
    expected = WAVE["amplitude"] * np.exp(-1j * WAVE["omega"] * times + 1j * WAVE["phase"]).real
    np.testing.assert_allclose(eta, expected, atol=1e-12, rtol=0.)
    # A result whose producing inputs are no longer the project's is refused.
    stale = dataclasses.replace(result, input_hash="0" * 64)
    with pytest.raises(ServiceError, match="producing"):
        service.result_incident_wave(project, stale)


@requires_solver
def test_refusals_without_wave_or_declared_depth(tmp_path, contract_artifacts):
    service, project = _import(tmp_path / "nowave", contract_artifacts, _base_options())
    result = service.run_linear_local(project)
    with pytest.raises(ServiceError, match="not forced"):
        service.result_incident_wave(project, result)
    with pytest.raises(ServiceError, match="only linear"):
        service.result_incident_wave(project, dataclasses.replace(result, kind="synthetic_unqualified",
                                                                   surface_snapshot=None))


@requires_solver
def test_dataset_without_excitation_source_request_is_refused(tmp_path, derived_artifacts):
    service, project = _import(tmp_path, derived_artifacts, _base_options(wave=WAVE))
    result = service.run_linear_local(project)
    with pytest.raises(ServiceError, match="excitation-source request"):
        service.result_incident_wave(project, result)


@requires_qt
@requires_solver
def test_gui_shows_the_forcing_wave_with_the_replayed_bodies(tmp_path, contract_artifacts):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    service, project = _import(tmp_path, contract_artifacts, _base_options(wave=WAVE))
    result = service.run_linear_local(project)
    app = QApplication.instance() or QApplication([])
    w = MainWindow(viewer_backend='software')
    try:
        w._service = service
        w._set_project(project.with_edits(results=(result,)))
        w._set_result(result)
        waves = w._waves
        assert waves.mode.currentIndex() == 3, waves.status.text()
        assert waves.field['kind'] == 'linear_regular_incident' and waves.follows_replay
        assert waves.field['result_input_hash'] == result.input_hash
        for frame in (0, 7, 40):
            w._apply_frame(frame)
            assert waves.active and waves.time == result.times[frame]
            expected = service.result_incident_wave(project, result).surface_elevation(
                waves.field['vertices'][:, :2], result.times[frame])
            np.testing.assert_allclose(waves.handle.mesh.positions[:, 2], expected, atol=1e-12, rtol=0.)
            for body_id in result.body_ids:
                np.testing.assert_allclose(w._handles[body_id].transform[:3, 3],
                                           result.positions[body_id][frame], atol=1e-12, rtol=0.)
    finally:
        w._dirty = False
        w.close()
        app.processEvents()


@pytest.mark.parametrize("omega,depth", [(1e-80, 1.0), (1e-3, 1e-3), (0.3, 1e3), (40.0, 0.5), (1.0, 0.5)])
def test_dispersion_extremes_are_accurate(omega, depth):
    k = dispersion_wavenumber(omega, depth, 9.81)
    y = k * depth
    assert y * math.tanh(y) == pytest.approx(omega * omega * depth / 9.81, rel=1e-14, abs=0.0)


@pytest.mark.parametrize("omega,depth", [(1e200, 1.0), (1e-200, 1e-200), (1.0, float('nan')), (1.0, -math.inf)])
def test_dispersion_outside_range_is_refused(omega, depth):
    with pytest.raises(ValueError):
        dispersion_wavenumber(omega, depth, 9.81)


def test_refused_or_absent_declarations_are_not_used(contract_artifacts):
    import json
    from anymarine.services import _verified_declaration
    dataset = anyhydro.load_hydro_dataset(contract_artifacts["dataset_path"])
    contract, reason = _verified_declaration(anyhydro, dataset)
    assert reason == "" and contract["request"]["water_depth"] == WATER_DEPTH
    changed = dataset.copy(deep=True)
    stored = changed.attrs["excitation_source_contract"]
    declared = json.loads(stored) if isinstance(stored, str) else json.loads(json.dumps(stored))
    declared["conventions"]["phase_origin_xy_m"] = [5.0, 0.0]
    changed.attrs["excitation_source_contract"] = json.dumps(declared)
    contract, reason = _verified_declaration(anyhydro, changed)
    assert contract is None and "refused" in reason
    del changed.attrs["excitation_source_contract"]
    assert _verified_declaration(anyhydro, changed) == (None, "")


@requires_solver
def test_snapshot_route_uses_the_producing_inputs(tmp_path, contract_artifacts):
    # Hull-only fixtures carry no surface snapshot; a stub stands in for a
    # ResultRecord whose snapshot ResultRecord itself binds to input_hash.
    from types import SimpleNamespace
    service, project = _import(tmp_path, contract_artifacts, _base_options(wave=WAVE))
    result = service.run_linear_local(project)
    snapshot_result = SimpleNamespace(kind=result.kind, input_hash="2" * 64, surface_snapshot=dict(
        schema='anymarine-surface-replay-v1', linear_inputs=project.linear_inputs.to_dict()))
    # The project's current inputs do not reproduce the result hash; the snapshot is used.
    assert service.result_recorded_wave(project, snapshot_result)[1]["omega"] == WAVE["omega"]
    assert service.result_incident_wave(project, snapshot_result).amplitude == WAVE["amplitude"]
    # Without a snapshot, non-producing current inputs are refused.
    with pytest.raises(ServiceError, match="producing"):
        service.result_recorded_wave(project, dataclasses.replace(result, input_hash="1" * 64))


@requires_qt
@requires_solver
def test_matched_then_manual_modes_and_unrelated_results(tmp_path, contract_artifacts):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    from anymarine.services import ApplicationService
    app = QApplication.instance() or QApplication([])
    w = MainWindow(viewer_backend='software')
    try:
        w.load_regular_wave_example()
        assert w._waves.follows_replay
        w._waves.mode.setCurrentIndex(1)   # manual preview after matched waves
        assert w._waves.mode.currentIndex() == 1 and w._waves.active, w._waves.status.text()
        assert w._waves.field['kind'] != 'mechanical_incident'
        assert len(w._waves.handle.mesh.positions) == len(w._waves.field['vertices'])
        # A result not solved in waves never receives "matched" waves.
        synthetic = ApplicationService().run_synthetic_local(w._project)
        w._set_project(w._project.with_edits(results=(synthetic,)))
        w._set_result(synthetic)
        w._waves.mode.setCurrentIndex(3)
        assert w._waves.mode.currentIndex() == 0 and not w._waves.active
        assert 'not solved in saved incident waves' in w._waves.status.text()
    finally:
        w._dirty = False
        w.close()
        app.processEvents()
