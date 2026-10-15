"""rigid_subset_v1 linear layout (ADR-015 slice L6-1): domain, dry mass, persistence, staleness and guards."""
from __future__ import annotations

import dataclasses
import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import requires_solver

from anymarine.domain import (
    LINEAR_LAYOUT_RIGID, ExternalMatrixRecord, LinearDevelopmentInputs, LinearExecutionLimits, LinearProvenance,
    default_two_body_project,
)
from anymarine.hashing import equilibrium_hash
from anymarine.linear import (
    LinearBindingError, LinearLayoutUnsupported, linear_input_hash, rigid_binding, rigid_dry_mass, run_linear,
    verify_linear_binding,
)
from anymarine.mass_properties import BodyMassProperties
from anymarine.persistence import ProjectStore

pytestmark = requires_solver          # the rigid layout's dry mass is solver-owned (anysolver.marine_rigid)

INERTIA = ((4.0e5, 1.0e3, -2.0e3), (1.0e3, 6.0e5, 5.0e2), (-2.0e3, 5.0e2, 7.0e5))


def _rotation(axis, angle):
    k = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    R = np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * K @ K
    return tuple(tuple(float(v) for v in row) for row in R)


def _project(orientation=None, cg=(0.4, -0.2, -0.8)):
    project = default_two_body_project()
    bodies = []
    for i, body in enumerate(project.bodies):
        changes = dict(mass_properties=BodyMassProperties(tuple(c * (i + 1) for c in cg), INERTIA))
        if orientation is not None:
            changes["reference_orientation"] = orientation
        bodies.append(dataclasses.replace(body, **changes))
    return dataclasses.replace(project, bodies=tuple(bodies))


def _inputs(project, dofs_per_body, *, n_steps=3, dt=0.1, externals=(), limits=None, mass_dry=None, layout=None):
    bindings = tuple(rigid_binding(body, dofs) for body, dofs in zip(project.bodies, dofs_per_body) if dofs)
    n = sum(len(d) for d in dofs_per_body if d)
    M = rigid_dry_mass(bindings) if mass_dry is None else mass_dry
    eye = np.eye(n)
    return LinearDevelopmentInputs(
        bindings=bindings, dt=dt, n_steps=n_steps, forces=np.zeros((n_steps, n)).tolist(), mass_dry=M.tolist(),
        added_mass_infinite=(0.1 * np.diag(np.diag(M))).tolist(), damping=(1e3 * eye).tolist(),
        stiffness=(1e5 * eye).tolist(), radiation_kernel=[(10.0 * eye).tolist()] * n_steps,
        kernel_times=[k * dt for k in range(n_steps)], equilibrium_hash=equilibrium_hash(project.equilibrium),
        provenance=LinearProvenance(source="explicit", description="rigid layout test"),
        external_matrices=externals, execution_limits=limits, layout=layout or LINEAR_LAYOUT_RIGID)


def _independent_dry_mass(m, cg_body, inertia_body, R):
    R = np.asarray(R)
    c = R @ np.asarray(cg_body)
    Ic = R @ np.asarray(inertia_body) @ R.T
    S = np.array([[0, -c[2], c[1]], [c[2], 0, -c[0]], [-c[1], c[0], 0]])
    Io = Ic + m * ((c @ c) * np.eye(3) - np.outer(c, c))
    return np.block([[m * np.eye(3), -m * S], [m * S, Io]])


# ------------------------------------------------------------------ dry mass (ADR-015 section 2)

@pytest.mark.parametrize("orientation", [None, _rotation((0.3, -0.5, 1.0), 0.4)])
def test_dry_mass_is_the_rigid_body_assembly_with_offset_cg_and_full_inertia(orientation):
    project = _project(orientation)
    body = project.bodies[0]
    M = rigid_dry_mass((rigid_binding(body, ("surge", "sway", "heave", "roll", "pitch", "yaw")),))
    expected = _independent_dry_mass(body.mass, body.mass_properties.cg_body, INERTIA, body.reference_orientation)
    assert np.allclose(M, expected, rtol=0, atol=1e-9 * np.abs(expected).max())
    assert np.abs(M[0:3, 3:6]).max() > 0                     # -m S(c) coupling present (offset CG)


def test_retained_dofs_are_a_row_column_selection_block_diagonal_over_bodies():
    project = _project()
    b0 = rigid_binding(project.bodies[0], ("surge", "heave", "pitch"))
    b1 = rigid_binding(project.bodies[1], ("sway", "roll"))
    M = rigid_dry_mass((b0, b1))
    full0 = rigid_dry_mass((rigid_binding(project.bodies[0], ("surge", "sway", "heave", "roll", "pitch", "yaw")),))
    full1 = rigid_dry_mass((rigid_binding(project.bodies[1], ("surge", "sway", "heave", "roll", "pitch", "yaw")),))
    assert np.array_equal(M[:3, :3], full0[np.ix_([0, 2, 4], [0, 2, 4])])
    assert np.array_equal(M[3:, 3:], full1[np.ix_([1, 3], [1, 3])])
    assert not M[:3, 3:].any() and not M[3:, :3].any()


# ------------------------------------------------------------------ layout, serialisation, hashes

def test_one_body_six_dof_round_trip_and_hash():
    project = _project(_rotation((0, 0, 1), 0.2))
    inputs = _inputs(project, [("surge", "sway", "heave", "roll", "pitch", "yaw"), ()])
    b = project.bodies[0].body_id
    assert inputs.ndof == 6 and inputs.labels == tuple(f"{b}:{d}" for d in
                                                       ("surge", "sway", "heave", "roll", "pitch", "yaw"))
    data = inputs.to_dict()
    assert data["layout"] == LINEAR_LAYOUT_RIGID and data["bindings"][0]["dofs"][0] == "surge"
    assert "reference_orientation" in data["bindings"][0] and "mass_properties" in data["bindings"][0]
    back = LinearDevelopmentInputs.from_dict(json.loads(json.dumps(data)))
    assert back == inputs and linear_input_hash(back) == linear_input_hash(inputs)


def test_two_body_mixed_subsets_and_external_records_round_trip():
    project = _project()
    b0, b1 = (body.body_id for body in project.bodies)
    record = ExternalMatrixRecord(kind="damping", dofs=(f"{b0}:pitch", f"{b1}:roll"),
                                  matrix=((2.0e4, 0.0), (0.0, 3.0e4)), source="test roll/pitch damping")
    inputs = _inputs(project, [("surge", "heave", "pitch"), ("sway", "roll")], externals=(record,),
                     limits=LinearExecutionLimits(max_frames=5000, max_radiation_samples=5000, max_wall_seconds=600))
    assert inputs.ndof == 5
    data = json.loads(json.dumps(inputs.to_dict()))
    assert data["external_matrices"][0]["semantics"] == "additive"
    assert data["execution_limits"] == {"max_frames": 5000, "max_radiation_samples": 5000, "max_wall_seconds": 600.0}
    back = LinearDevelopmentInputs.from_dict(data)
    assert back == inputs and linear_input_hash(back) == linear_input_hash(inputs)
    edited = json.loads(json.dumps(data))
    edited["external_matrices"][0]["source"] = "other"
    assert linear_input_hash(LinearDevelopmentInputs.from_dict(edited)) != linear_input_hash(inputs)


def test_legacy_limits_and_bindings_keep_their_bytes():
    assert LinearExecutionLimits(max_frames=2000).to_dict() == {"max_frames": 2000}
    with pytest.raises(ValueError, match="unknown control"):
        LinearExecutionLimits.from_dict({"max_frames": 2000, "max_seconds": 1})


def test_project_with_rigid_inputs_saves_and_loads(tmp_path):
    project = _project(_rotation((1, 0, 0), 0.1))
    inputs = _inputs(project, [("heave", "roll", "pitch"), ("surge",)])
    project = dataclasses.replace(project, linear_inputs=inputs)
    ProjectStore(tmp_path).save(project)
    loaded = ProjectStore(tmp_path).load()
    assert loaded.linear_inputs == inputs
    verify_linear_binding(loaded, loaded.linear_inputs)


# ------------------------------------------------------------------ refusals

def test_unknown_layout_and_too_many_dofs_are_refused():
    project = _project()
    with pytest.raises(ValueError, match="unknown linear layout"):
        _inputs(project, [("surge",), ()], layout="rigid_subset_v9")
    three = dataclasses.replace(project, bodies=project.bodies + (dataclasses.replace(
        project.bodies[0], body_id="third", name="Third"),))
    all6 = ("surge", "sway", "heave", "roll", "pitch", "yaw")
    with pytest.raises(ValueError, match="more than 12"):
        _inputs(three, [all6, all6, ("surge",)])


def test_wrong_dry_mass_is_refused():
    project = _project()
    good = rigid_dry_mass((rigid_binding(project.bodies[0], ("surge", "heave")),))
    bad = good.copy()
    bad[0, 0] *= 1 + 1e-9
    with pytest.raises(ValueError, match="RigidBody6D dry mass"):
        _inputs(project, [("surge", "heave"), ()], mass_dry=bad)


def test_binding_rules():
    project = _project()
    body = project.bodies[0]
    with pytest.raises(ValueError, match="canonical order"):
        rigid_binding(body, ("heave", "surge"))
    with pytest.raises(ValueError, match="subset"):
        rigid_binding(body, ("surge", "spin"))
    with pytest.raises(LinearBindingError, match="CG/inertia"):
        rigid_binding(dataclasses.replace(body, mass_properties=None), ("surge",))
    legacy_shape = rigid_binding(body, ("surge",))
    with pytest.raises(ValueError, match="only admitted with retained dofs"):
        dataclasses.replace(legacy_shape, dofs=None)


def test_legacy_layout_refuses_rigid_bindings_and_external_records():
    project = _project()
    rigid = _inputs(project, [("surge", "heave"), ("surge", "heave")])
    data = rigid.to_dict()
    del data["layout"]
    with pytest.raises(ValueError, match="legacy two-body"):
        LinearDevelopmentInputs.from_dict(data)


def test_external_record_dofs_must_be_retained():
    project = _project()
    b0 = project.bodies[0].body_id
    record = ExternalMatrixRecord(kind="stiffness", dofs=(f"{b0}:yaw",), matrix=((1.0,),), source="weak yaw")
    with pytest.raises(ValueError, match="not retained"):
        _inputs(project, [("surge",), ()], externals=(record,))
    with pytest.raises(ValueError, match="semantics"):
        ExternalMatrixRecord(kind="damping", dofs=(f"{b0}:yaw",), matrix=((1.0,),), source="s", semantics="total")


def test_radiation_sample_limit_is_enforced():
    project = _project()
    with pytest.raises(ValueError, match="max_radiation_samples"):
        _inputs(project, [("surge",), ()], n_steps=4,
                limits=LinearExecutionLimits(max_frames=10, max_radiation_samples=3))


# ------------------------------------------------------------------ staleness (ADR-015 section 2)

@pytest.mark.parametrize("edit", ["inertia", "orientation", "cg"])
def test_mass_record_or_pose_edits_make_rigid_inputs_stale(edit):
    project = _project()
    inputs = _inputs(project, [("surge", "pitch"), ("heave",)])     # every project body is bound, in order
    project = dataclasses.replace(project, linear_inputs=inputs)
    verify_linear_binding(project, inputs)
    body = project.bodies[0]
    if edit == "inertia":
        changed = dataclasses.replace(body, mass_properties=BodyMassProperties(
            body.mass_properties.cg_body, ((5.0e5, 0, 0), (0, 6.0e5, 0), (0, 0, 7.0e5))))
    elif edit == "cg":
        changed = dataclasses.replace(body, mass_properties=BodyMassProperties((0.0, 0.0, 0.0), INERTIA))
    else:
        changed = dataclasses.replace(body, reference_orientation=_rotation((0, 0, 1), 0.05))
    edited = dataclasses.replace(project, bodies=(changed,) + project.bodies[1:])
    with pytest.raises(LinearBindingError, match="stale"):
        verify_linear_binding(edited, inputs)


# ------------------------------------------------------------------ guards (ADR-015 section 7)

def test_run_and_assessment_refuse_the_rigid_layout_explicitly():
    from anymarine.linear_assessment import AssessmentCaseError, AssessmentRefusalError, assess_result, prepare_case
    from anymarine.paired_refinement import PairedRefinementRefusalError, _member_inputs
    inputs = _inputs(_project(), [("surge", "heave"), ()])
    with pytest.raises(LinearLayoutUnsupported, match="L6-2"):
        run_linear(inputs)
    with pytest.raises(AssessmentCaseError, match="six-DOF layouts"):
        prepare_case({}, inputs)
    with pytest.raises(AssessmentRefusalError, match="six-DOF layouts"):
        assess_result(None, None, inputs)
    with pytest.raises(PairedRefinementRefusalError, match="six-DOF layouts"):
        _member_inputs(SimpleNamespace(inputs=inputs))


# ------------------------------------------------------------------ review round 1 regressions

def test_rigid_projects_use_schema_10_and_legacy_manifests_cannot_carry_them(tmp_path):
    project = _project()
    inputs = _inputs(project, [("sway", "roll"), ("sway", "yaw")])
    ProjectStore(tmp_path).save(dataclasses.replace(project, linear_inputs=inputs))
    manifest = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 10
    manifest["schema_version"] = 3                     # what an older application would have accepted
    (tmp_path / "project.json").write_text(json.dumps(manifest), encoding="utf-8")
    from anymarine.persistence import ProjectLoadError
    with pytest.raises(ProjectLoadError, match="schema_version 10"):
        ProjectStore(tmp_path).load()


def test_legacy_projects_keep_their_schema(tmp_path):
    from anymarine.linear import analytic_demo_inputs
    project = default_two_body_project()
    ProjectStore(tmp_path).save(dataclasses.replace(project, linear_inputs=analytic_demo_inputs(project)))
    assert json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))["schema_version"] == 3


def test_rigid_results_are_never_decoded_with_the_legacy_mapping():
    from anymarine.linear import LinearResultIncomplete, verify_linear_result
    inputs = _inputs(_project(), [("sway", "roll"), ("sway", "yaw")])
    with pytest.raises(LinearResultIncomplete, match="cannot be verified yet"):
        verify_linear_result(None, inputs)


def test_imports_refuse_to_replace_rigid_inputs(tmp_path):
    from anymarine.hydro import HydroImportError, _assemble_derived_inputs
    from anymarine.services import ApplicationService, ServiceError
    project = _project()
    project = dataclasses.replace(project, linear_inputs=_inputs(project, [("sway", "roll"), ("sway", "yaw")]))
    service = ApplicationService(tmp_path)
    with pytest.raises(ServiceError, match="six-DOF layouts"):
        service.import_derived_hydro_inputs(project, "d.nc", "o.npz", "k.nc", {})
    with pytest.raises(ServiceError, match="six-DOF layouts"):
        service.import_hydro_inputs(project, "d.nc", "k.nc", {})
    with pytest.raises(HydroImportError, match="rigid_subset_v1"):
        _assemble_derived_inputs(None, project, None, None, None, {}, "0" * 64)


def test_non_finite_dry_mass_assembly_is_refused():
    project = _project(cg=(0.0, 0.0, 1e155))
    with pytest.raises(ValueError):
        _inputs(project, [("surge", "pitch"), ("surge",)], mass_dry=np.eye(3))
