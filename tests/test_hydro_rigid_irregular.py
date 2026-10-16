"""Irregular-sea forcing of the six-DOF rigid import (slice N3): first-order excitation series + Newman slow drift.

Uses the analytic box fixture of test_hydro_rigid_import.py, extended by a far-field mean-drift block (surge
positive, sway negative, yaw changing sign inside the admitted window). Oracles are written out here: the
first-order force is the plain sum over components with linearly interpolated complex coefficients, the slow drift
the O(N^2) double sum with the declared Newman rule.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from conftest import requires_solver

anyhydro = pytest.importorskip("anyhydro")
pytest.importorskip("anyhydro.hydrostatics_exact")
pytest.importorskip("anyhydro.slow_drift")
anyloads = pytest.importorskip("anyloads")

from anyloads import AiryComponent, JonswapSpectrum, WaveRealization  # noqa: E402

from anymarine.domain import _jsonable_value  # noqa: E402
from anymarine.hydro import HydroAdmissionError, HydroOptionsError  # noqa: E402
from anymarine.hydro_rigid import import_rigid_hydro_inputs, rigid_frequency_references  # noqa: E402
from anymarine.linear import linear_input_hash  # noqa: E402
from anymarine.services import ApplicationService  # noqa: E402

from test_hydro_rigid_import import DT, G, OMEGAS, RHO, _body, _options, _project, _source  # noqa: E402

pytestmark = requires_solver

TIMES = DT * np.arange(801)


def _drift_values():
    w = OMEGAS[:, None]
    return np.stack([1e4 * w ** 2, -3e3 * w, 2e4 * (w - 0.9)], axis=-1)[:, :, :]       # (omega, 1 heading, 3)


def _with_drift(body):
    """Schema 1.3.0 Kochin export (as the Capytaine backend writes it; values irrelevant here: zero) plus the
    far-field mean-drift block of one body (JSON attributes as the producer writes them, JSON text), added before
    the hydrostatic contract and the kernel bind the dataset; the import reads the stored (decoded) form."""
    from anyhydro.mean_drift import MEAN_DRIFT_FORMAT
    from anyhydro.schema import KOCHIN_SCHEMA_VERSION, MEAN_DRIFT_UNITS

    def extend(ds):
        n_theta = 720
        ds = ds.assign_coords(theta=2.0 * np.pi * np.arange(n_theta) / n_theta,
                              drift_dof=[f"{body.body_id}::{d}" for d in ("surge", "sway", "yaw")])
        ds["kochin_radiation"] = (("omega", "radiating_dof", "theta"),
                                  np.zeros((len(OMEGAS), ds.sizes["radiating_dof"], n_theta), complex))
        ds["kochin_diffraction"] = (("omega", "wave_direction", "theta"),
                                    np.zeros((len(OMEGAS), 1, n_theta), complex))
        ds["wavenumber"] = (("omega",), OMEGAS ** 2 / G)
        for name, value in (("water_depth", np.inf), ("rho", RHO), ("g", G)):
            ds[name] = ((), np.float64(value))
        ds.attrs["wave_environment"] = json.dumps(dict(water_depth="inf", rho=RHO, g=G))
        ds.attrs["kochin_export"] = json.dumps(dict(route="zero test fixture", reference_point=[0.0, 0.0],
                                                    theta_count=n_theta))
        ds.attrs["schema_version"] = KOCHIN_SCHEMA_VERSION
        ds["mean_drift_far_field"] = (("omega", "wave_direction", "drift_dof"), _drift_values())
        ds.attrs["mean_drift_far_field"] = json.dumps(dict(
            format=MEAN_DRIFT_FORMAT, method="far_field_momentum_conservation", units=MEAN_DRIFT_UNITS,
            yaw_reference=[float(v) for v in body.reference_position], motions=dict(source="fixed", motion_hash="x")))
        return ds
    return extend


def _drift_source(tmp_path, project, tag="d"):
    dataset_path, kernel_path, ds, _ = _source(tmp_path, project, tag=tag, extend=_with_drift(project.bodies[0]))
    return dataset_path, kernel_path, ds


def _realization(n=9, seed=4):
    rng = np.random.default_rng(seed)
    return WaveRealization(tuple(AiryComponent(float(rng.uniform(0.2, 1.0)), float(w), 0.0,
                                               float(rng.uniform(-math.pi, math.pi)))
                                 for w in np.linspace(0.45, 1.5, n)))                 # off-grid frequencies


def _wave(realization, slow_drift=None):
    return dict(kind="irregular", realization=json.loads(json.dumps(realization.to_dict())), slow_drift=slow_drift)


def _first_order(ds, realization, keys):
    """Plain sum over components; complex coefficients interpolated linearly per DOF (real and imaginary parts)."""
    X = ds["excitation_force"].transpose("omega", "wave_direction", "influenced_dof").values[:, 0, :]
    stored = [str(k) for k in ds["influenced_dof"].values]
    out = np.zeros((len(TIMES), len(keys)))
    for c in realization.components:
        for j, key in enumerate(keys):
            col = X[:, stored.index(key)]
            x = np.interp(c.omega, OMEGAS, col.real) + 1j * np.interp(c.omega, OMEGAS, col.imag)
            out[:, j] += (c.amplitude * np.exp(1j * c.phase) * x * np.exp(-1j * c.omega * TIMES)).real
    return out


def _newman_direct(realization, method):
    """O(N^2) double sum, D interpolated linearly, geometric (sign-preserving, opposite signs 0) or arithmetic."""
    D = np.stack([np.interp([c.omega for c in realization.components], OMEGAS, _drift_values()[:, 0, k])
                  for k in range(3)], axis=1)
    out = np.zeros((len(TIMES), 3))
    for k in range(3):
        for ci, Di in zip(realization.components, D[:, k]):
            for cj, Dj in zip(realization.components, D[:, k]):
                if method == "arithmetic":
                    T = 0.5 * (Di + Dj)
                else:
                    T = 0.0 if Di * Dj <= 0 else math.copysign(math.sqrt(Di * Dj), Di)
                    if ci is cj:
                        T = Di
                out[:, k] += ci.amplitude * cj.amplitude * T * np.cos(
                    (ci.omega - cj.omega) * TIMES - (ci.phase - cj.phase))
    return out


DOFS6 = ("surge", "sway", "heave", "roll", "pitch", "yaw")


def test_first_order_irregular_forcing_equals_the_component_sum(tmp_path):
    project = _project()
    ds_path, k_path, ds, _ = _source(tmp_path, project)
    waves = _realization()
    inputs, provenance = import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, wave=_wave(waves)))
    keys = [f"vessel::{d}" for d in DOFS6]
    expected = _first_order(ds, waves, keys)
    assert np.allclose(np.asarray(inputs.forces), expected, rtol=1e-10, atol=1e-9 * np.max(np.abs(expected)))
    details = _jsonable_value(inputs.provenance.details)
    assert details["wave"] is None and provenance["wave"] is None
    selection = details["irregular_wave"]
    assert selection["realization_hash"] == waves.content_hash and selection["slow_drift"] is None
    assert selection["n_components"] == 9


@pytest.mark.parametrize("method", ["geometric", "arithmetic"])
def test_newman_slow_drift_adds_the_double_sum_on_surge_sway_yaw(tmp_path, method):
    project = _project()
    ds_path, k_path, ds = _drift_source(tmp_path, project)
    waves = _realization()
    first, _ = import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, wave=_wave(waves)))
    both, _ = import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, wave=_wave(waves, method)))
    difference = np.asarray(both.forces) - np.asarray(first.forces)
    expected = _newman_direct(waves, method)
    for k, column in enumerate((0, 1, 5)):
        assert np.allclose(difference[:, column], expected[:, k], rtol=1e-8, atol=1e-8 * np.max(np.abs(expected)))
    assert np.array_equal(difference[:, [2, 3, 4]], np.zeros((len(TIMES), 3)))
    record = _jsonable_value(both.provenance.details)["irregular_wave"]["slow_drift"]
    assert record["method"] == method and record["applied_dofs"] == ["vessel::surge", "vessel::sway", "vessel::yaw"]
    assert record["dropped_dofs"] == [] and record["source_method"] == "far_field_momentum_conservation"
    D = np.stack([np.interp([c.omega for c in waves.components], OMEGAS, _drift_values()[:, 0, k]) for k in range(3)],
                 axis=1)
    assert np.allclose(record["mean"], (np.array([c.amplitude for c in waves.components])[:, None] ** 2 * D).sum(0))


def test_slow_drift_on_a_retained_subset_records_the_dropped_dofs(tmp_path):
    project = _project()
    ds_path, k_path, _ = _drift_source(tmp_path, project)
    waves = _realization()
    options = _options(project, dofs={"vessel": ["surge", "heave", "pitch"]}, external_matrices=[dict(
        kind="stiffness", dofs=["vessel:surge"], matrix=[[1e4]], source="declared weak surge spring")],
        wave=_wave(waves, "geometric"))
    inputs, _ = import_rigid_hydro_inputs(project, ds_path, k_path, options)
    record = _jsonable_value(inputs.provenance.details)["irregular_wave"]["slow_drift"]
    assert record["applied_dofs"] == ["vessel::surge"] and record["dropped_dofs"] == ["vessel::sway", "vessel::yaw"]
    with pytest.raises(HydroOptionsError, match="none of them is a retained DOF"):
        import_rigid_hydro_inputs(project, ds_path, k_path, dict(options, dofs={"vessel": ["heave", "pitch"]},
                                                                 external_matrices=[]))


def test_a_seeded_jonswap_realization_is_admitted(tmp_path):
    project = _project()
    ds_path, k_path, _ = _drift_source(tmp_path, project)
    omegas = np.linspace(0.45, 1.5, 22)
    waves = WaveRealization.from_jonswap(JonswapSpectrum(2.0, 8.0), omegas, np.full(22, 0.99 * (omegas[1] - omegas[0])),
                                         seed=11)
    inputs, provenance = import_rigid_hydro_inputs(project, ds_path, k_path,
                                                   _options(project, wave=_wave(waves, "geometric")))
    assert np.all(np.isfinite(np.asarray(inputs.forces)))
    assert provenance["rigid_bundle"]["options"]["wave"]["realization"]["seed"] == 11


def test_refusals(tmp_path):
    project = _project()
    ds_path, k_path, _ = _drift_source(tmp_path, project)
    plain_path, plain_kernel, _, _ = _source(tmp_path, project, tag="plain")
    waves = _realization()

    def refused(error, match, wave, paths=(ds_path, k_path), proj=project):
        with pytest.raises(error, match=match):
            import_rigid_hydro_inputs(proj, *paths, _options(proj, wave=wave))

    outside = WaveRealization((AiryComponent(1.0, 0.45, 0.0, 0.0), AiryComponent(1.0, 1.7, 0.0, 0.0)))
    refused(HydroOptionsError, "window", _wave(outside))
    refused(HydroOptionsError, "exactly", dict(_wave(waves), extra=1))
    refused(HydroOptionsError, "exactly", dict(_wave(waves), kind="regular"))
    refused(HydroOptionsError, "slow_drift must be", _wave(waves, "harmonic"))
    tampered = _wave(waves)
    tampered["realization"]["components"][0]["amplitude"] = "1"
    refused(HydroOptionsError, "not admissible|canonical", tampered)
    refused(HydroOptionsError, "mean drift", _wave(waves, "geometric"), paths=(plain_path, plain_kernel))
    seeded = WaveRealization.from_jonswap(JonswapSpectrum(2.0, 8.0), np.linspace(0.5, 1.3, 5), np.full(5, 0.19), seed=1)
    bad = _wave(seeded)
    bad["realization"]["components"][0]["amplitude"] *= 1.5          # disagrees with its spectral bins
    refused(HydroOptionsError, "not admissible", bad)
    two = _project((_body("a"), _body("b", x0=30.0)))
    two_paths = _source(tmp_path, two, tag="two")[:2]
    refused(HydroOptionsError, "single-body", _wave(waves, "geometric"), paths=two_paths, proj=two)
    off = WaveRealization((AiryComponent(1.0, 0.6, 45.0, 0.0),))
    with pytest.raises(HydroAdmissionError, match="heading"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, wave=_wave(off)))


def test_regular_option_and_its_record_are_unchanged(tmp_path):
    """The regular wave keeps its exact option shape and record (no irregular key)."""
    project = _project()
    ds_path, k_path, ds, _ = _source(tmp_path, project)
    inputs, provenance = import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))
    details = _jsonable_value(inputs.provenance.details)
    assert "irregular_wave" not in details and set(provenance["wave"]) == {"omega", "heading", "amplitude", "phase"}
    o = 40
    F = ds["excitation_force"].transpose("omega", "wave_direction", "influenced_dof").values[o, 0, :]
    expected = (F[None, :] * np.exp(-1j * OMEGAS[o] * TIMES)[:, None]).real
    assert np.array_equal(np.asarray(inputs.forces), expected)


def test_save_reopen_verify_run_and_reference_refusal(tmp_path):
    source_dir, root = tmp_path / "source", tmp_path / "project"
    source_dir.mkdir()
    project = _project()
    ds_path, k_path, _ = _drift_source(source_dir, project)
    service = ApplicationService(root)
    service.save(project, root=root)
    imported = service.import_rigid_hydro_inputs(project, ds_path, k_path,
                                                 _options(project, n_steps=401, wave=_wave(_realization(), "geometric")))
    service.save(imported)
    reopened = ApplicationService(root).load(root)
    assert linear_input_hash(reopened.linear_inputs) == imported.hydro_provenance.linear_input_hash
    record = ApplicationService(root).run_linear_local(reopened)
    assert record.input_hash is not None
    with pytest.raises(HydroOptionsError, match="irregular sea"):
        rigid_frequency_references(reopened.linear_inputs, reopened.hydro_provenance,
                                   root / reopened.hydro_provenance.dataset_sidecar)


@pytest.mark.parametrize("installed", ["0.2.1", "0.2.1+local", "0.2.2.dev0", "0.2.2rc1"])
def test_slow_drift_needs_anyhydro_0_2_2(tmp_path, monkeypatch, installed):
    from anymarine.hydro import HydroUnavailableError
    project = _project()
    ds_path, k_path, _ = _drift_source(tmp_path, project)
    monkeypatch.setattr(anyhydro, "__version__", installed)
    if installed.startswith("0.2.1"):                                  # first order alone still runs on 0.2.1
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, wave=_wave(_realization())))
    with pytest.raises(HydroUnavailableError, match="0.2.2"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, wave=_wave(_realization(), "geometric")))
