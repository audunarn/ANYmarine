"""Numerical lid admission in the six-DOF hydro import (ADR-015 L6-4a).

The box hull of test_hydro_rigid_import.py gets a z = 0 waterplane lid (two triangles per cell, DOWN normals). The
lid is numerical geometry only: the hydrostatic contract comes from the wet hull, so the restoring is identical to
the lidless import; the lid identity is proven against the dataset and carried in the provenance.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from conftest import requires_solver

pytest.importorskip("anyhydro")
pytest.importorskip("anyhydro.hydrostatics_exact")

from anymarine.domain import BodyMesh, HydroBridgeProvenance  # noqa: E402
from anymarine.hydro import HydroGeometryError, HydroLidGeometryError  # noqa: E402
from anymarine.hydro_rigid import import_rigid_hydro_inputs  # noqa: E402
from anymarine.linear import linear_input_hash  # noqa: E402
from anymarine.services import ApplicationService  # noqa: E402

from test_hydro_rigid_import import _body, _options, _project, _source  # noqa: E402

pytestmark = requires_solver


def _lid(cb_offset=(0.0, 0.0), z=0.0, nx=4, ny=2, upward=False):
    """Waterplane lid of the 12 x 6 box in body coordinates, triangulated, DOWN normals unless ``upward``."""
    xs = np.linspace(-6.0, 6.0, nx + 1) + cb_offset[0]
    ys = np.linspace(-3.0, 3.0, ny + 1) + cb_offset[1]
    positions = tuple((float(x), float(y), z) for y in ys for x in xs)
    triangles = []
    for j in range(ny):
        for i in range(nx):
            a, b = j * (nx + 1) + i, j * (nx + 1) + i + 1
            c, d = (j + 1) * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i
            down = ((a, c, b), (a, d, c))                 # clockwise seen from above: normal -z
            triangles.extend(tuple(reversed(t)) for t in down) if upward else triangles.extend(down)
    return BodyMesh(positions=positions, triangles=tuple(triangles))


def _lidded(cb_offset=(1.0, -0.5), **lid):
    return dataclasses.replace(_body("vessel", cb_offset=cb_offset), hydro_lid=_lid(cb_offset, **lid))


def test_lid_admitted_with_restoring_identical_to_the_lidless_import(tmp_path):
    lidless = _project((_body("vessel", cb_offset=(1.0, -0.5)),))
    lidded = _project((_lidded(),))
    plain, _ = import_rigid_hydro_inputs(lidless, *_source(tmp_path, lidless, tag="n")[:2], _options(lidless))
    inputs, provenance = import_rigid_hydro_inputs(lidded, *_source(tmp_path, lidded, tag="l")[:2], _options(lidded))
    for name in ("stiffness", "mass_dry", "added_mass_infinite", "damping", "radiation_kernel", "forces"):
        assert np.array_equal(np.asarray(getattr(inputs, name)), np.asarray(getattr(plain, name))), name
    assert provenance["lid_mesh_hashes"][0] is not None and len(provenance["lid_mesh_hashes"]) == 1
    assert HydroBridgeProvenance.from_dict(provenance).lid_mesh_hashes == tuple(provenance["lid_mesh_hashes"])


def test_lid_on_one_side_only_or_a_different_lid_is_refused(tmp_path):
    lidless = _project((_body("vessel", cb_offset=(1.0, -0.5)),))
    lidded = _project((_lidded(),))
    lidded_source = _source(tmp_path, lidded, tag="l")[:2]
    with pytest.raises(HydroGeometryError, match="one side only"):
        import_rigid_hydro_inputs(lidless, *lidded_source, _options(lidless))
    with pytest.raises(HydroGeometryError, match="one side only"):
        import_rigid_hydro_inputs(lidded, *_source(tmp_path, lidless, tag="n")[:2], _options(lidded))
    finer = _project((_lidded(nx=6),))
    with pytest.raises(HydroGeometryError, match="not the project's world-space lid"):
        import_rigid_hydro_inputs(finer, *lidded_source, _options(finer))


@pytest.mark.parametrize("lid, message", [(dict(z=-0.1), "off the equilibrium waterplane"),
                                          (dict(upward=True), "does not point DOWN")])
def test_invalid_lid_geometry_is_refused(tmp_path, lid, message):
    project = _project((_lidded(**lid),))
    with pytest.raises(HydroLidGeometryError, match=message):
        import_rigid_hydro_inputs(project, *_source(tmp_path, project)[:2], _options(project))


def test_auxiliary_lid_policy_is_refused(tmp_path):
    project = _project((dataclasses.replace(_lidded(), hydro_lid_policy="submerged_0p1m_v1"),))
    with pytest.raises(HydroLidGeometryError, match="auxiliary lid policy"):
        import_rigid_hydro_inputs(project, *_source(tmp_path, project)[:2], _options(project))


def test_lidded_import_saves_reopens_and_reverifies(tmp_path):
    source_dir, root = tmp_path / "source", tmp_path / "project"
    source_dir.mkdir()
    project = _project((_lidded(),))
    ds_path, k_path, _, _ = _source(source_dir, project)
    service = ApplicationService(root)
    service.save(project, root=root)
    imported = service.import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, n_steps=401))
    service.save(imported)
    reopened = ApplicationService(root).load(root)
    assert reopened.hydro_provenance.lid_mesh_hashes == imported.hydro_provenance.lid_mesh_hashes
    assert linear_input_hash(reopened.linear_inputs) == imported.hydro_provenance.linear_input_hash
    stripped = dict(imported.hydro_provenance.to_dict())
    del stripped["lid_mesh_hashes"]
    forged = dataclasses.replace(imported, hydro_provenance=HydroBridgeProvenance.from_dict(stripped))
    from anymarine.services import ServiceError
    with pytest.raises(ServiceError, match="lid_mesh_hashes"):
        service.verify_hydro_provenance(forged)


# ---------------------------------------------------------------------------------------------- review round 1

def test_lid_beyond_the_hull_waterplane_is_refused(tmp_path):
    """A lid extending past the waterline would change radiation and excitation; it is refused before any solve."""
    big = dataclasses.replace(_body("vessel"), hydro_lid=BodyMesh(
        positions=((-12.0, -6.0, 0.0), (12.0, -6.0, 0.0), (12.0, 6.0, 0.0), (-12.0, 6.0, 0.0)),
        triangles=((0, 2, 1), (0, 3, 2))))
    project = _project((big,))
    with pytest.raises(HydroLidGeometryError, match="outside the (hull )?waterplane"):
        import_rigid_hydro_inputs(project, *_source(tmp_path, project)[:2], _options(project))


def test_rotated_pose_and_mixed_lidded_lidless_bodies(tmp_path):
    """The lid follows the reference orientation (R0 x + r0): a yawed lidded body and a lidless body in one import."""
    yaw = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    a = dataclasses.replace(_body("a"), reference_orientation=yaw, hydro_lid=_lid())
    b = _body("b", x0=40.0)
    project = _project((a, b))
    inputs, provenance = import_rigid_hydro_inputs(project, *_source(tmp_path, project)[:2], _options(project))
    assert inputs.ndof == 12
    assert provenance["lid_mesh_hashes"][0] is not None and provenance["lid_mesh_hashes"][1] is None
    identity = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    unrotated = _project((dataclasses.replace(a, reference_orientation=identity), b))
    with pytest.raises(HydroGeometryError):
        import_rigid_hydro_inputs(unrotated, *_source(tmp_path, project, tag="r")[:2], _options(unrotated))


@pytest.mark.parametrize("z, admitted", [(5e-10, True), (2e-9, False)])
def test_lid_plane_tolerance_boundary(tmp_path, z, admitted):
    project = _project((_lidded(z=z),))
    source = _source(tmp_path, project)[:2]
    if admitted:
        import_rigid_hydro_inputs(project, *source, _options(project))
    else:
        with pytest.raises(HydroLidGeometryError, match="off the equilibrium waterplane"):
            import_rigid_hydro_inputs(project, *source, _options(project))


def test_degenerate_lid_faces_are_refused(tmp_path):
    lid = _lid()
    try:
        repeated = BodyMesh(positions=lid.positions, triangles=lid.triangles + ((0, 0, 1),))
    except ValueError:
        pytest.skip("BodyMesh itself refuses repeated-index triangles")
    project = _project((dataclasses.replace(_body("vessel"), hydro_lid=repeated),))
    with pytest.raises(HydroLidGeometryError, match="degenerate"):
        import_rigid_hydro_inputs(project, *_source(tmp_path, project)[:2], _options(project))


def test_an_invented_lid_identity_on_a_lidless_import_is_refused(tmp_path):
    from anymarine.services import ServiceError
    source_dir, root = tmp_path / "source", tmp_path / "project"
    source_dir.mkdir()
    project = _project()
    ds_path, k_path, _, _ = _source(source_dir, project)
    service = ApplicationService(root)
    service.save(project, root=root)
    imported = service.import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, n_steps=401))
    forged = dataclasses.replace(imported, hydro_provenance=HydroBridgeProvenance.from_dict(
        dict(imported.hydro_provenance.to_dict(), lid_mesh_hashes=["a" * 64])))
    with pytest.raises(ServiceError, match="lid_mesh_hashes"):
        service.verify_hydro_provenance(forged)


def _ring(points):
    return [[points[i], points[(i + 1) % len(points)]] for i in range(len(points))]


def test_containment_handles_concave_waterlines_and_holes():
    """Unit check of the face containment on an L-shaped waterline and on a square ring (outer + hole)."""
    from anymarine.hydro_rigid import require_lid_inside_waterline
    ell = np.array(_ring([(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]), float)
    inside = np.array([[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]], float)
    require_lid_inside_waterline(inside, [[0, 2, 1], [0, 3, 2]], ell, "ell", 1e-9)
    notch = np.array([[2, 0, 0], [4, 4, 0], [0, 4, 0]], float)            # spans the re-entrant corner
    with pytest.raises(HydroLidGeometryError):
        require_lid_inside_waterline(notch, [[0, 1, 2]], ell, "ell", 1e-9)
    ring = np.array(_ring([(0, 0), (6, 0), (6, 6), (0, 6)]) + _ring([(2, 2), (2, 4), (4, 4), (4, 2)]), float)
    over_hole = np.array([[0, 0, 0], [6, 0, 0], [6, 6, 0], [0, 6, 0]], float)
    with pytest.raises(HydroLidGeometryError):
        require_lid_inside_waterline(over_hole, [[0, 2, 1], [0, 3, 2]], ring, "ring", 1e-9)
    in_hole = np.array([[2.5, 2.5, 0], [3.5, 2.5, 0], [3, 3.5, 0]], float)
    with pytest.raises(HydroLidGeometryError):
        require_lid_inside_waterline(in_hole, [[0, 2, 1]], ring, "ring", 1e-9)
    strip = np.array([[0, 0, 0], [6, 0, 0], [6, 2, 0], [0, 2, 0]], float)
    require_lid_inside_waterline(strip, [[0, 2, 1], [0, 3, 2]], ring, "ring", 1e-9)


def test_waterline_of_an_open_wet_hull_cut_at_z0():
    """A wet hull cut at the waterline (boundary edges on z = 0, as the L01 panels) yields its waterline."""
    from anymarine.hydro_rigid import _waterline_segments
    v = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0], [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1]],
                 float)
    tris = [[4, 6, 5], [4, 7, 6], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4],
            [3, 4, 7]]
    segments = _waterline_segments(v, np.array(tris), 1e-9)
    assert segments.shape == (4, 2, 2)
    ends = sorted(tuple(point) for point in np.round(segments.reshape(-1, 2), 12).tolist())
    assert ends == sorted([(-1.0, -1.0), (1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (1.0, 1.0), (-1.0, 1.0), (-1.0, 1.0),
                           (-1.0, -1.0)])


# ---------------------------------------------------------------------------------------------- review round 2

def test_a_hole_whose_vertices_lie_on_the_lid_edges_is_refused():
    """Review round 2 exact case: a triangular hole with every vertex on the lid face's boundary."""
    from anymarine.hydro_rigid import require_lid_inside_waterline
    region = np.array(_ring([(-1, -1), (11, -1), (11, 11), (-1, 11)]) + _ring([(0, 0), (5, 0), (0, 5)]), float)
    face = np.array([[0, 0, 0], [0, 10, 0], [10, 0, 0]], float)
    with pytest.raises(HydroLidGeometryError, match="over a hole"):
        require_lid_inside_waterline(face, [[0, 1, 2]], region, "hole", 1e-9)


def _box(lo, hi, top_on_plane=True):
    """Closed axis-aligned box mesh (outward normals), 12 triangles."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    tris = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
            [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    return v, np.array(tris)


def test_closed_hull_with_its_top_face_on_the_plane_keeps_its_waterline():
    """Review round 2: wet side walls meet a coplanar top face on z = 0; the top's diagonal is not waterline."""
    from anymarine.hydro_rigid import _waterline_segments, require_lid_inside_waterline
    v, tris = _box((-1, -1, -1), (1, 1, 0))
    segments = _waterline_segments(v, tris, 1e-9)
    assert segments.shape == (4, 2, 2)
    lid = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], float)
    require_lid_inside_waterline(lid, [[0, 2, 1], [0, 3, 2]], segments, "box", 1e-9)


def test_moonpool_rim_on_the_plane_is_a_waterline_hole():
    """Review round 2: an inner wall whose wet and dry triangles meet on a ring exactly at z = 0 bounds a hole."""
    from anymarine.hydro_rigid import _waterline_segments, require_lid_inside_waterline
    # inner moonpool wall x, y in [-1, 1]: wet part z in [-2, 0], dry part z in [0, 1], rim vertices on z = 0
    rim = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    v = [(x, y, -2.0) for x, y in rim] + [(x, y, 0.0) for x, y in rim] + [(x, y, 1.0) for x, y in rim]
    tris = []
    for k in range(4):
        n = (k + 1) % 4
        tris += [[k, n, 4 + n], [k, 4 + n, 4 + k], [4 + k, 4 + n, 8 + n], [4 + k, 8 + n, 8 + k]]
    hull_v = np.array(v, float)
    inner = _waterline_segments(hull_v, np.array(tris), 1e-9)
    assert inner.shape == (4, 2, 2)                                   # the rim ring survives (wet/dry edge pairs)
    outer = np.array(_ring([(-5, -5), (5, -5), (5, 5), (-5, 5)]), float)
    region = np.concatenate([outer, inner])
    across = np.array([[-5, -5, 0], [5, -5, 0], [5, 5, 0], [-5, 5, 0]], float)
    with pytest.raises(HydroLidGeometryError):
        require_lid_inside_waterline(across, [[0, 2, 1], [0, 3, 2]], region, "moonpool", 1e-9)
    ring_face = np.array([[-5, -5, 0], [5, -5, 0], [5, -2, 0]], float)
    require_lid_inside_waterline(ring_face, [[0, 2, 1]], region, "moonpool", 1e-9)

