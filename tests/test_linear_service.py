"""Service-layer linear development runs, binding verification and staleness."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import requires_solver

from anymarine import RESULT_KIND_LINEAR
from anymarine.linear import (
    LinearCancelledError,
    linear_input_hash,
    run_linear,
    verify_body_binding,
)

pytestmark = requires_solver


def demo_inputs(service, project):
    return service.linear_demo_inputs(project)


def test_demo_inputs_bind_project_bodies(service, project):
    inputs = demo_inputs(service, project)
    verify_body_binding(project.bodies, inputs)
    assert [b.body_id for b in inputs.bindings] == [b.body_id for b in project.bodies]


def test_set_and_reload_explicit_inputs(service, project):
    inputs = demo_inputs(service, project)
    data = inputs.to_dict()
    explicit = service.load_explicit_linear_inputs(project, data)
    assert linear_input_hash(explicit) == linear_input_hash(inputs)
    stored = service.set_linear_inputs(project, explicit)
    assert stored.linear_inputs is not None
    assert linear_input_hash(stored.linear_inputs) == linear_input_hash(inputs)


def test_explicit_inputs_refuse_stale_binding(service, project):
    inputs = demo_inputs(service, project)
    edited_body = project.bodies[0].with_edits(mass=project.bodies[0].mass * 2)
    edited = project.with_edits(
        bodies=tuple(
            edited_body if b.body_id == edited_body.body_id else b for b in project.bodies
        )
    )
    with pytest.raises(Exception, match="stale"):
        service.load_explicit_linear_inputs(edited, inputs.to_dict())


def test_run_linear_local_produces_authoritative_channels(service, project):
    inputs = demo_inputs(service, project)
    result = service.run_linear_local(project, inputs)
    assert result.kind == RESULT_KIND_LINEAR
    assert result.input_hash == linear_input_hash(inputs)
    assert len(result.times) == inputs.n_steps
    labels = inputs.labels
    expected_prefixes = (
        "q:", "v:", "a:", "force_external:", "force_dry_inertia:",
        "force_added_inertia:", "force_memory:", "force_damping:",
        "force_stiffness:", "force_residual:",
    )
    for label in labels:
        for prefix in expected_prefixes:
            assert f"{prefix}{label}" in result.channels
    assert len(result.channels) == 4 * len(expected_prefixes)
    assert "anysolver.marine_linear" in result.note
    assert "not a qualified marine analysis" in result.note


def test_result_maps_surge_to_x_and_heave_to_z(service, project):
    inputs = demo_inputs(service, project)
    result = service.run_linear_local(project, inputs)
    for body_index, binding in enumerate(inputs.bindings):
        surge_label = f"{binding.body_id}:surge"
        heave_label = f"{binding.body_id}:heave"
        reference = binding.reference_position
        rows = result.positions[binding.body_id]
        assert len(rows) == inputs.n_steps
        for frame, row in enumerate(rows):
            assert row[0] == pytest.approx(
                reference[0] + result.channels[f"q:{surge_label}"][frame]
            )
            assert row[1] == pytest.approx(reference[1])
            assert row[2] == pytest.approx(
                reference[2] + result.channels[f"q:{heave_label}"][frame]
            )
        assert all(row == (0.0, 0.0, 0.0) for row in result.orientations[binding.body_id])


def test_channels_match_direct_public_solver_call(service, project):
    from anysolver import marine_linear

    inputs = demo_inputs(service, project)
    result = service.run_linear_local(project, inputs)
    spec = marine_linear.MarineLinearSpec(
        labels=inputs.labels,
        dt=inputs.dt,
        times=np.asarray(inputs.times),
        forces=np.asarray(inputs.forces),
        mass_dry=np.asarray(inputs.mass_dry),
        added_mass_infinite=np.asarray(inputs.added_mass_infinite),
        damping=np.asarray(inputs.damping),
        stiffness=np.asarray(inputs.stiffness),
        radiation_kernel=np.asarray(inputs.radiation_kernel),
        kernel_times=np.asarray(inputs.kernel_times),
        initial_displacement=np.asarray(inputs.q0),
        initial_velocity=np.asarray(inputs.v0),
    )
    solved = marine_linear.solve_marine_linear(spec)
    assert solved.complete and not solved.cancelled
    for dof, label in enumerate(inputs.labels):
        assert np.allclose(result.channels[f"q:{label}"], solved.displacements[:, dof])
        assert np.allclose(result.channels[f"v:{label}"], solved.velocities[:, dof])
        assert np.allclose(result.channels[f"a:{label}"], solved.accelerations[:, dof])
        assert np.allclose(
            result.channels[f"force_memory:{label}"], solved.memory_forces[:, dof]
        )
        assert np.allclose(
            result.channels[f"force_stiffness:{label}"], solved.stiffness_forces[:, dof]
        )


def test_cancellation_produces_no_result(service, project):
    inputs = demo_inputs(service, project)
    with pytest.raises(LinearCancelledError):
        run_linear(inputs, cancel=lambda: True)
    with pytest.raises(Exception, match="cancel"):
        service.run_linear_local(project, inputs, cancel=lambda: True)


def test_missing_solver_fails_closed(service, project, monkeypatch):
    from anymarine import linear as linear_module

    def unavailable():
        raise linear_module.LinearSolverUnavailableError("solver missing (test)")

    monkeypatch.setattr(linear_module, "_import_solver", unavailable)
    inputs = demo_inputs(service, project)
    with pytest.raises(Exception, match="solver missing"):
        service.run_linear_local(project, inputs)


def test_no_stored_inputs_is_actionable(service, project):
    with pytest.raises(Exception, match="no stored linear inputs"):
        service.run_linear_local(project)
    with pytest.raises(Exception, match="no stored linear inputs"):
        service.linear_job_snapshot(project)


def test_staleness_on_body_edits(service, project):
    inputs = demo_inputs(service, project)
    project = service.set_linear_inputs(project, inputs)
    result = service.run_linear_local(project, inputs)
    project = service.add_result(project, result)
    assert service.stale_result_ids(project) == ()

    def edit(**changes):
        body = project.bodies[0].with_edits(**changes)
        return project.with_edits(
            bodies=tuple(
                body if b.body_id == body.body_id else b for b in project.bodies
            )
        )

    assert service.result_stale(edit(reference_position=(0.0, 0.0, 1.0)), result)
    assert service.result_stale(edit(mass=project.bodies[0].mass * 1.5), result)
    assert service.result_stale(edit(name="Renamed"), result)
    reordered = project.with_edits(bodies=project.bodies[::-1])
    assert service.result_stale(reordered, result)
    # Removing the stored inputs also flags the result stale.
    cleared = project.with_edits(linear_inputs=None)
    assert service.result_stale(cleared, result)


def test_synthetic_results_unaffected_by_linear_inputs(service, project):
    synthetic = service.run_synthetic_local(project)
    baseline = service.input_hash(project)
    project = service.add_result(project, synthetic)
    inputs = demo_inputs(service, project)
    project = service.set_linear_inputs(project, inputs)
    assert service.input_hash(project) == baseline
    assert service.stale_result_ids(project) == ()
