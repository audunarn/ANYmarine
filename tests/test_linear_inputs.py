"""Immutable linear development inputs: validation, hashing, demo builder."""

from __future__ import annotations

import dataclasses
import math

import pytest

from anymarine.domain import (
    LINEAR_MAX_FRAMES,
    LINEAR_SOURCE_ANALYTIC_DEMO,
    LinearBodyBinding,
    LinearDevelopmentInputs,
    LinearProvenance,
)
from anymarine.linear import (
    _DEMO_COUPLING_AINF,
    _DEMO_COUPLING_KERNEL,
    analytic_demo_inputs,
    linear_input_hash,
)

# Valid 64-hex placeholder for the immutable mesh geometry identity that
# LinearBodyBinding requires since the geometry-binding repair.
MESH_HASH = "1" * 64


def make_inputs(**overrides) -> LinearDevelopmentInputs:
    """Minimal valid 4-DOF two-body inputs; overrides replace fields."""

    binding_a = LinearBodyBinding(
        body_id="body-a", name="Body A",
        reference_position=(-15.0, 0.0, 0.0), mass=2.0e6,
        mesh_hash=MESH_HASH,
    )
    binding_b = LinearBodyBinding(
        body_id="body-b", name="Body B",
        reference_position=(15.0, 0.0, 0.0), mass=1.0e6,
        mesh_hash=MESH_HASH,
    )
    n_steps = 4
    fields = dict(
        bindings=(binding_a, binding_b),
        equilibrium_hash="2" * 64,
        dt=0.1,
        t0=0.0,
        n_steps=n_steps,
        forces=tuple((1.0, 0.0, 0.0, 0.0) for _ in range(n_steps)),
        mass_dry=((2e6, 0, 0, 0), (0, 2e6, 0, 0), (0, 0, 1e6, 0), (0, 0, 0, 1e6)),
        added_mass_infinite=((1e5, 0, 0, 0), (0, 1e5, 0, 0), (0, 0, 5e4, 0), (0, 0, 0, 5e4)),
        damping=((1e4, 0, 0, 0), (0, 1e4, 0, 0), (0, 0, 5e3, 0), (0, 0, 0, 5e3)),
        stiffness=((1e5, 0, 0, 0), (0, 2e5, 0, 0), (0, 0, 5e4, 0), (0, 0, 0, 1e5)),
        radiation_kernel=tuple(
            ((0.0, 0.0, 0.0, 0.0), (0.0, float(k), 0.0, 0.0),
             (0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, float(k)))
            for k in range(n_steps)
        ),
        kernel_times=tuple(0.1 * k for k in range(n_steps)),
        q0=(0.0, 0.0, 0.0, 0.0),
        v0=(0.0, 0.0, 0.0, 0.0),
        provenance=LinearProvenance(
            source=LINEAR_SOURCE_ANALYTIC_DEMO, description="test inputs"
        ),
    )
    fields.update(overrides)
    return LinearDevelopmentInputs(**fields)


# --------------------------------------------------------------------- demo


def test_demo_requires_exactly_two_bodies(service):
    project = service.new_project("demo")
    one_body = project.with_edits(bodies=project.bodies[:1])
    with pytest.raises(Exception):
        analytic_demo_inputs(one_body)


def test_demo_is_deterministic_and_hash_stable(service, project):
    demo_a = analytic_demo_inputs(project)
    demo_b = analytic_demo_inputs(project)
    assert linear_input_hash(demo_a) == linear_input_hash(demo_b)
    dict_a = demo_a.to_dict()
    dict_b = demo_b.to_dict()
    # Wall-clock creation time is the only allowed difference.
    dict_a["provenance"].pop("created_utc")
    dict_b["provenance"].pop("created_utc")
    assert dict_a == dict_b


def test_demo_labels_and_linear_link(service, project):
    demo = analytic_demo_inputs(project)
    ids = [binding.body_id for binding in demo.bindings]
    assert ids == [body.body_id for body in project.bodies]
    assert demo.labels == (
        f"{ids[0]}:surge", f"{ids[0]}:heave", f"{ids[1]}:surge", f"{ids[1]}:heave",
    )
    # Linear mechanical link: off-diagonal surge coupling, symmetric K.
    assert demo.stiffness[0][2] != 0.0
    assert demo.stiffness[0][2] == demo.stiffness[2][0]
    assert demo.provenance.source == LINEAR_SOURCE_ANALYTIC_DEMO
    assert "not BEM output" in demo.provenance.description


def test_demo_kernel_formula(service, project):
    demo = analytic_demo_inputs(project)
    for k in (0, 1, 7, demo.n_steps - 1):
        expected = demo.radiation_kernel[0][1][1] * math.exp(-0.6 * k * demo.dt)
        assert demo.radiation_kernel[k][1][1] == pytest.approx(expected, rel=1e-12)
        assert demo.kernel_times[k] == pytest.approx(k * demo.dt, rel=1e-12)


def test_demo_respects_frame_bound(service, project):
    with pytest.raises(ValueError, match="bound"):
        analytic_demo_inputs(project, n_steps=LINEAR_MAX_FRAMES + 1)


# ------------------------------------------------------------- record checks


def test_record_is_deeply_immutable():
    inputs = make_inputs()
    with pytest.raises(dataclasses.FrozenInstanceError):
        inputs.dt = 0.2
    kernel_row = inputs.radiation_kernel[0][0]
    assert isinstance(kernel_row, tuple)
    source = make_inputs()
    assert source.radiation_kernel[1][1][1] == 1.0


def test_record_rejects_bad_bindings():
    binding_a = LinearBodyBinding("a", "A", (0, 0, 0), 1.0, MESH_HASH)
    with pytest.raises(ValueError):
        make_inputs(bindings=(binding_a,))
    with pytest.raises(ValueError):
        make_inputs(bindings=(binding_a, binding_a))
    with pytest.raises(ValueError):
        LinearBodyBinding("a", "A", (0, 0, 0), -1.0, MESH_HASH)
    with pytest.raises(ValueError):
        LinearBodyBinding("a", "A", (0.0, math.inf, 0.0), 1.0, MESH_HASH)
    with pytest.raises(ValueError):
        LinearBodyBinding("a", "A", (0, 0, 0), 1.0, "not-a-hash")


def test_record_rejects_bad_grids_and_shapes():
    with pytest.raises(ValueError, match="cover"):
        make_inputs(radiation_kernel=make_inputs().radiation_kernel[:2])
    with pytest.raises(ValueError, match="kernel_times"):
        make_inputs(kernel_times=(0.0, 0.05, 0.2, 0.3))
    with pytest.raises(ValueError, match="n_steps"):
        make_inputs(n_steps=LINEAR_MAX_FRAMES + 1)
    with pytest.raises(ValueError):
        make_inputs(n_steps=1)
    with pytest.raises(ValueError):
        make_inputs(dt=0.0)
    with pytest.raises(ValueError):
        make_inputs(dt=float("nan"))
    with pytest.raises(ValueError):
        make_inputs(forces=((1.0, 0.0, 0.0),))
    with pytest.raises(ValueError):
        make_inputs(mass_dry=((1.0, 0.0), (0.0, 1.0)))
    with pytest.raises(ValueError):
        make_inputs(q0=(0.0, 0.0, 0.0, float("inf")))
    with pytest.raises(ValueError):
        make_inputs(
            provenance=LinearProvenance(source="mystery", description="x")
        )


def test_record_rejects_nonfinite_matrices():
    good = make_inputs()
    bad = tuple(
        tuple(float("nan") if (r, c) == (0, 0) else value for c, value in enumerate(row))
        for r, row in enumerate(good.mass_dry)
    )
    with pytest.raises(ValueError):
        make_inputs(mass_dry=bad)


def test_record_grid_is_recomputed_from_step_count():
    inputs = make_inputs()
    assert inputs.times == pytest.approx((0.0, 0.1, 0.2, 0.3))
    assert inputs.times == tuple(inputs.t0 + k * inputs.dt for k in range(inputs.n_steps))


def test_roundtrip_preserves_hash_and_freezes():
    inputs = make_inputs()
    data = inputs.to_dict()
    rebuilt = LinearDevelopmentInputs.from_dict(data)
    assert linear_input_hash(rebuilt) == linear_input_hash(inputs)
    # Mutating the source dict after construction cannot change the record.
    data["dt"] = 999.0
    data["bindings"][0]["mass"] = 123.0
    assert rebuilt.dt == 0.1
    assert rebuilt.bindings[0].mass == 2.0e6


def test_hash_covers_numerical_inputs_but_not_wall_clock():
    a = make_inputs()
    b = make_inputs(
        provenance=LinearProvenance(
            source=LINEAR_SOURCE_ANALYTIC_DEMO,
            description="test inputs",
            created_utc="2099-01-01T00:00:00+00:00",
        )
    )
    assert linear_input_hash(a) == linear_input_hash(b)
    changed = make_inputs(
        damping=tuple(
            tuple(value * 1.5 for value in row) for row in a.damping
        )
    )
    assert linear_input_hash(changed) != linear_input_hash(a)
    renamed_binding = LinearBodyBinding(
        "body-a", "Renamed", (-15.0, 0.0, 0.0), 2.0e6, MESH_HASH
    )
    rebound = make_inputs(
        bindings=(renamed_binding, a.bindings[1]),
        # binding order defines DOF order; keep the rest identical
        radiation_kernel=a.radiation_kernel,
        kernel_times=a.kernel_times,
        forces=a.forces,
        mass_dry=a.mass_dry,
        added_mass_infinite=a.added_mass_infinite,
        damping=a.damping,
        stiffness=a.stiffness,
    )
    assert linear_input_hash(rebound) != linear_input_hash(a)
