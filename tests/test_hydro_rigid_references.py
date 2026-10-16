"""Frequency-domain references of a rigid hydro import, bound to its provenance (ADR-015 L6-3 M-d).

Analytic box fixture of test_hydro_rigid_import.py: A(w) = A_inf - C / (lam^2 + w^2), B(w) = C lam / (lam^2 + w^2)
and the closed-form excitation, so the continuous reference has a closed form. The kernel carries the source's
explicit B(0) = C / lam. Measured discrete-vs-continuous gap (normalised, q_hat = S^-1 q), H = 60 s, dt = 0.05 s:
0.74 / 0.34 / 0.45 % at w 0.55 / 1.04 / 1.54 with the explicit B(0); 2.9 / 1.4 / 0.8 % with a linear-to-zero tail.
"""
from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest

from conftest import requires_solver

pytest.importorskip("anyhydro")
pytest.importorskip("anyhydro.hydrostatics_exact")

from anymarine import linear_fd as fd  # noqa: E402
from anymarine.domain import HydroBridgeProvenance  # noqa: E402
from anymarine.linear import linear_input_hash  # noqa: E402
from anymarine.services import ApplicationService, ServiceError  # noqa: E402

from test_hydro_rigid_import import LAM, OMEGAS, _blocks, _body, _options, _project, _source  # noqa: E402

pytestmark = requires_solver

WAVE_INDEX = 40
L = 12.0


def _imported(tmp_path, body_id="vessel", **option_overrides):
    source_dir, root = tmp_path / "source", tmp_path / "project"
    source_dir.mkdir(parents=True)
    project = _project((_body(body_id, cb_offset=(1.0, -0.5)),))
    # the analytic source has B(0) = C / lam != 0: the kernel gets that explicit zero-frequency sample (a
    # linear-to-zero low tail would contradict this source and alone costs ~2 % at w = 0.55, review round 1)
    _, C, _, _, _ = _blocks(project.bodies[0])
    ds_path, k_path, _, _ = _source(source_dir, project, explicit_b0=C / LAM)
    service = ApplicationService(root)
    service.save(project, root=root)
    options = _options(project, **option_overrides)
    return service, service.import_rigid_hydro_inputs(project, ds_path, k_path, options)


def _closed_form(project, omega, amplitude, phase, wave_index=WAVE_INDEX):
    inputs = project.linear_inputs
    a_inf, C, _, _, _ = _blocks(project.bodies[0])
    A = a_inf - C / (LAM ** 2 + omega ** 2)
    B = C * LAM / (LAM ** 2 + omega ** 2)
    F = amplitude * np.exp(1j * phase) * np.array(
        [2e5 * (1 + 0.1 * i) * np.exp(1j * (0.2 * i + 0.05 * wave_index)) for i in range(6)])
    Z = (np.asarray(inputs.stiffness) - omega ** 2 * (np.asarray(inputs.mass_dry) + A)
         - 1j * omega * (B + np.asarray(inputs.damping)))
    return np.linalg.solve(Z, F), F


def _complex(pairs):
    return np.array([complex(re, im) for re, im in pairs])


def _normalised_difference(X, X_d):
    """Independent oracle of the declared metric: motions in q_hat = S^-1 q (rotations times L)."""
    s_inv = np.array([1.0, 1.0, 1.0, L, L, L])
    return float(np.max(np.abs(s_inv * (X - X_d))) / np.max(np.abs(s_inv * X)))


@pytest.mark.parametrize("wave_index, body_id", [(20, "vessel"), (40, "fleet:1"), (60, "vessel")])
def test_references_match_the_closed_form_and_the_stored_inputs(tmp_path, wave_index, body_id):
    omega = float(OMEGAS[wave_index])
    service, project = _imported(tmp_path, body_id=body_id,
                                 wave=dict(omega=omega, heading=0.0, amplitude=1.5, phase=0.3))
    refs = service.rigid_frequency_references(project)
    assert json.loads(json.dumps(refs)) == refs
    assert refs["linear_input_hash"] == project.hydro_provenance.linear_input_hash
    assert refs["dataset_raw_sha256"] == project.hydro_provenance.dataset_raw_sha256
    assert refs["labels"][0] == f"{body_id}:surge"
    X_expected, F_expected = _closed_form(project, omega, 1.5, 0.3, wave_index)
    X = _complex(refs["continuous"])
    X_d = _complex(refs["discrete"])
    assert np.allclose(_complex(refs["force"]), F_expected, rtol=1e-12, atol=0)
    assert np.max(np.abs(X - X_expected)) <= 1e-10 * np.max(np.abs(X_expected))
    inputs = project.linear_inputs
    X_d_direct = fd.discrete_reference(inputs.mass_dry, inputs.added_mass_infinite, inputs.damping,
                                       inputs.stiffness, inputs.radiation_kernel, inputs.dt, omega, F_expected)
    assert np.max(np.abs(X_d - X_d_direct)) <= 1e-12 * np.max(np.abs(X_d_direct))
    metric = _normalised_difference(X, X_d)
    assert refs["normalised_max_difference"] == pytest.approx(metric, rel=1e-12)
    # declared acceptance (M-d plan): discrete and continuous agree within 2 % of the normalised peak
    assert metric <= 0.02, metric


def test_metric_does_not_hide_rotation_errors():
    """Review round 1: a 0.01 rad roll difference on a 0.1 rad roll is 10 %, not suppressed by the scaling."""
    X = np.array([1.0, 0, 0, 0.1, 0, 0], complex)
    X_d = X + np.array([0, 0, 0, 0.01, 0, 0])
    assert _normalised_difference(X, X_d) == pytest.approx(0.01 * L / (0.1 * L))


def test_singular_impedance_is_a_typed_refusal(tmp_path, monkeypatch):
    from anymarine import hydro_rigid
    service, project = _imported(tmp_path, wave=dict(omega=float(OMEGAS[WAVE_INDEX]), heading=0.0, amplitude=1.0))

    def singular(*args, **kwargs):
        raise np.linalg.LinAlgError("Singular matrix")

    monkeypatch.setattr(hydro_rigid.fd, "continuous_reference", singular)
    with pytest.raises(ServiceError, match="singular"):
        service.rigid_frequency_references(project)


def test_free_decay_legacy_and_tampered_projects_are_refused(tmp_path):
    service, project = _imported(tmp_path, wave=None)
    with pytest.raises(ServiceError, match="free decay"):
        service.rigid_frequency_references(project)
    _, forced = _imported(tmp_path / "forced")
    data = forced.hydro_provenance.to_dict()
    inputs = forced.linear_inputs
    K = np.array(inputs.stiffness)
    K[3, 3] *= 1.05
    tampered = dataclasses.replace(inputs, stiffness=tuple(tuple(row) for row in K))
    forged = dataclasses.replace(forced, linear_inputs=tampered, hydro_provenance=HydroBridgeProvenance.from_dict(
        dict(data, linear_input_hash=linear_input_hash(tampered))))
    with pytest.raises(ServiceError, match="re-imported"):
        ApplicationService(tmp_path / "forced" / "project").rigid_frequency_references(forged)
    with pytest.raises(ServiceError, match="rigid_subset_v1 hydro import"):
        service.rigid_frequency_references(dataclasses.replace(project, hydro_provenance=None, linear_inputs=None))
