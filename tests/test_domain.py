"""Domain validation refusals."""

from __future__ import annotations

import pytest

from anymarine.domain import (
    AnalysisSettings,
    Body,
    BodyMesh,
    EquilibriumMetadata,
    ProjectModel,
    ResultRecord,
    default_box_mesh,
    default_two_body_project,
)


def test_body_requires_name():
    mesh = default_box_mesh((1.0, 1.0, 1.0))
    with pytest.raises(ValueError):
        Body(body_id="b1", name="  ", reference_position=(0, 0, 0), mass=1.0, mesh=mesh)


def test_body_rejects_bad_reference_position():
    mesh = default_box_mesh((1.0, 1.0, 1.0))
    with pytest.raises(ValueError):
        Body(body_id="b1", name="A", reference_position=(0, 0), mass=1.0, mesh=mesh)
    with pytest.raises(ValueError):
        Body(body_id="b1", name="A", reference_position=(0, 0, float("nan")), mass=1.0, mesh=mesh)


def test_body_rejects_nonpositive_mass():
    mesh = default_box_mesh((1.0, 1.0, 1.0))
    with pytest.raises(ValueError):
        Body(body_id="b1", name="A", reference_position=(0, 0, 0), mass=0.0, mesh=mesh)
    with pytest.raises(ValueError):
        Body(body_id="b1", name="A", reference_position=(0, 0, 0), mass=-5.0, mesh=mesh)


def test_mesh_rejects_out_of_range_triangles():
    with pytest.raises(ValueError):
        BodyMesh(positions=((0, 0, 0), (1, 0, 0), (0, 1, 0)), triangles=((0, 1, 5),))


def test_mesh_rejects_empty():
    with pytest.raises(ValueError):
        BodyMesh(positions=(), triangles=((0, 1, 2),))
    with pytest.raises(ValueError):
        BodyMesh(positions=((0, 0, 0),), triangles=())


def test_project_rejects_duplicate_body_ids():
    mesh = default_box_mesh((1.0, 1.0, 1.0))
    body = Body(body_id="dup", name="A", reference_position=(0, 0, 0), mass=1.0, mesh=mesh)
    with pytest.raises(ValueError):
        ProjectModel(name="P", bodies=(body, body))


def test_settings_reject_nonpositive():
    with pytest.raises(ValueError):
        AnalysisSettings(duration=0.0)
    with pytest.raises(ValueError):
        AnalysisSettings(dt=-0.1)


def test_equilibrium_rejects_nan_waterline():
    with pytest.raises(ValueError):
        EquilibriumMetadata(waterline_z=float("nan"))


def test_result_rejects_unknown_kind():
    with pytest.raises(ValueError):
        ResultRecord(
            result_id="r1",
            kind="bem_qualified",
            input_hash="0" * 64,
            body_ids=("b1",),
            times=(0.0,),
            positions={"b1": ((0.0, 0.0, 0.0),)},
            orientations={"b1": ((0.0, 0.0, 0.0),)},
            channels={},
        )


def test_result_rejects_mismatched_sample_counts():
    with pytest.raises(ValueError):
        ResultRecord(
            result_id="r1",
            kind="synthetic_unqualified",
            input_hash="0" * 64,
            body_ids=("b1",),
            times=(0.0, 1.0),
            positions={"b1": ((0.0, 0.0, 0.0),)},
            orientations={"b1": ((0.0, 0.0, 0.0),)},
            channels={},
        )


def test_default_project_is_two_body_synthetic():
    project = default_two_body_project()
    assert len(project.bodies) == 2
    assert len({body.body_id for body in project.bodies}) == 2
    assert project.equilibrium.source == "synthetic"
