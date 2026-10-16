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
