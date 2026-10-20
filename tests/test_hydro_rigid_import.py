"""Six-DOF rigid_subset_v1 hydro import (ADR-015 slice L6-3, M-a/M-b) on synthetic, analytically known sources.

Source per body: a 12 x 6 m box half-submerged (draft 2.5 m, V = 180 m^3), optionally offset so that the centre of
buoyancy (and the CG above it) lie off the reference point (nonzero baseline moment: exercises the ADR-004 chart
conversion), CG 0.75 m above the CB (roll/pitch stable with gravity), a passive Lorentzian radiation model
B(w) = C lam / (lam^2 + w^2), the attached ANYhydro hydrostatic contract (PHYSICAL_WRENCH + rate map, provenance
rho/g) and a Bartlett-tapered kernel. Surge, sway and yaw get the owner's declared weak mooring.
"""
from __future__ import annotations

import dataclasses
import json
import math

import numpy as np
import pytest

from conftest import requires_solver

anyhydro = pytest.importorskip("anyhydro")
# needs the ANYhydro L6-4 features (hydrostatic contract rate map/chart conversion, Bartlett window): skip, not
# fail, against an older installed ANYhydro
pytest.importorskip("anyhydro.hydrostatics_exact")

from anymarine.domain import (Body, EquilibriumMetadata, LINEAR_LAYOUT_RIGID, ProjectModel,  # noqa: E402
                              _jsonable_value, default_box_mesh)
from anymarine.hydro import HydroAdmissionError, HydroGeometryError, HydroOptionsError  # noqa: E402
from anymarine.hydro_rigid import import_rigid_hydro_inputs  # noqa: E402
from anymarine.linear import run_linear, verify_linear_result  # noqa: E402
from anymarine.mass_properties import BodyMassProperties  # noqa: E402

pytestmark = requires_solver

RHO, G = 1025.0, 9.81
V = 12.0 * 6.0 * 2.5
MASS = RHO * V
W = MASS * G
INERTIA = ((MASS * 5.0, 0.0, 0.0), (0.0, MASS * 13.0, 0.0), (0.0, 0.0, MASS * 16.0))
OMEGAS = np.linspace(0.05, 3.0, 120)
LAM = 0.8
DT, N_TIME, H = 0.05, 2001, 60.0
DOFS = ("surge", "sway", "heave", "roll", "pitch", "yaw")


def _body(body_id, x0=0.0, cb_offset=(0.0, 0.0)):
    """Box hull whose centroid sits at cb_offset (horizontal) from the body origin; CG above the CB."""
    mesh = default_box_mesh((12.0, 6.0, 5.0))
    shifted = type(mesh)(positions=tuple((x + cb_offset[0], y + cb_offset[1], z) for x, y, z in mesh.positions),
                         triangles=mesh.triangles)
    cg = (cb_offset[0], cb_offset[1], -0.5)
    return Body(body_id=body_id, name=body_id, reference_position=(x0, 0.0, 0.0), mass=MASS, mesh=shifted,
                mass_properties=BodyMassProperties(cg, INERTIA))


def _project(bodies=None):
    bodies = bodies or (_body("vessel"),)
    return ProjectModel(name="rigid import fixture", bodies=tuple(bodies),
                        equilibrium=EquilibriumMetadata(waterline_z=0.0, description="analytic", source="synthetic"))


def _posed(body, positions):
    R0 = np.array(body.reference_orientation, float)
    r0 = np.array(body.reference_position, float)
    return np.array([[sum(R0[i][k] * p[k] for k in range(3)) + r0[i] for i in range(3)] for p in positions])


def _independent_world_mesh(body):
    """Oracle world mesh: sum_k R0[i, k] x[k] + reference position, written out here (not the importer's builder)."""
    vertices = _posed(body, body.mesh.positions)
    faces = np.asarray(body.mesh.triangles, dtype=np.int32)
    return anyhydro.HydroPanelMesh(vertices=vertices, faces=faces, face_sizes=np.full(len(faces), 3, np.uint8),
                                   body_id=body.body_id, outward_normals_verified=False,
                                   source_geometry_refs=("anymarine-body-mesh+reference-pose",), mesh_hash="")


def _blocks(body):
    """Per-body canonical-order source blocks: A_inf, C (B(0) scale), physical source stiffness, baseline, CB.

    The stiffness is the physical derivative of the buoyancy wrench about the co-moving reference point with respect
    to spatial increments (a wall-sided box): waterplane moments about the reference point, V z_b on roll/pitch and
    the rotation of the CB lever under yaw (K[3,5] = -W x_b, K[4,5] = -W y_b, K[5,3] = K[5,4] = 0; not symmetric).
    """
    scale = np.array([MASS, MASS, MASS, INERTIA[0][0], INERTIA[1][1], INERTIA[2][2]]) * 0.3
    D = np.diag(np.sqrt(scale))
    Q = 0.9 * np.eye(6) + 0.1 * np.ones((6, 6))
    C = D @ Q @ D * LAM
    a_inf = np.diag(scale) * 4.0
    xb, yb = body.mass_properties.cg_body[0], body.mass_properties.cg_body[1]
    zb, A = -1.25, 72.0
    K = np.zeros((6, 6))
    K[2, 2] = RHO * G * A
    K[2, 3] = K[3, 2] = RHO * G * A * yb
    K[2, 4] = K[4, 2] = -RHO * G * A * xb
    K[3, 3] = RHO * G * (12 * 6 ** 3 / 12 + A * yb ** 2 + V * zb)
    K[4, 4] = RHO * G * (6 * 12 ** 3 / 12 + A * xb ** 2 + V * zb)
    K[3, 4] = K[4, 3] = -RHO * G * A * xb * yb
    K[3, 5] = -W * xb
    K[4, 5] = -W * yb
    baseline = (0.0, 0.0, W, W * yb, -W * xb, 0.0)
    return a_inf, C, K, baseline, (xb, yb, zb)


def _source(tmp_path, project, *, permutation=None, explicit_b0=None, dataset_lid=False, provenance_g=G, tag="s",
            contract=True, stiffness_skew=0.0, a_inf_skew=0.0, damping_skew=0.0, symmetry_tol=1e-6, extend=None):
    bodies = project.bodies
    n = 6 * len(bodies)
    a_inf, C, K = np.zeros((n, n)), np.zeros((n, n)), np.zeros((n, n))
    baselines, hbodies, cbs = {}, [], []
    for k, body in enumerate(bodies):
        a, c, kk, base, cb = _blocks(body)
        kk[3, 4] += stiffness_skew                          # unsymmetric source term (refusal tests)
        s = slice(6 * k, 6 * k + 6)
        a_inf[s, s], C[s, s], K[s, s] = a, c, kk
        baselines[body.body_id] = base
        cbs.append(np.asarray(body.reference_position) + np.array(cb))
        mesh = _independent_world_mesh(body)
        lid = None
        if body.hydro_lid is not None:                      # the project's own lid, built independently
            lid_vertices = _posed(body, body.hydro_lid.positions)
            lid_faces = np.asarray(body.hydro_lid.triangles, dtype=np.int32)
            lid = anyhydro.HydroPanelMesh(vertices=lid_vertices, faces=lid_faces,
                                          face_sizes=np.full(len(lid_faces), 3, np.uint8), body_id=body.body_id,
                                          outward_normals_verified=False, source_geometry_refs=("lid",), mesh_hash="")
        elif dataset_lid:
            lid_vertices = np.array([[-6, -3, 0], [6, -3, 0], [6, 3, 0], [-6, 3, 0]], float)
            lid_vertices += np.asarray(body.reference_position, float)
            lid = anyhydro.HydroPanelMesh(vertices=lid_vertices, faces=np.array([[0, 1, 2, 3]]),
                                          face_sizes=np.array([4], np.uint8), body_id=body.body_id,
                                          outward_normals_verified=False, source_geometry_refs=("lid",), mesh_hash="")
        hbodies.append((body, mesh, lid))
    # optional antisymmetric offsets (refusal tests): one on A_inf and every A(w) keeps the reconstruction
    # consistent; one on B(w) reaches the kernel when the producer's symmetry tolerance is loose
    skew_unit = np.zeros((n, n))
    skew_unit[3, 4], skew_unit[4, 3] = 1.0, -1.0
    a_inf = a_inf + a_inf_skew * skew_unit
    added = np.array([a_inf - C / (LAM ** 2 + w ** 2) for w in OMEGAS])
    damping = np.array([C * LAM / (LAM ** 2 + w ** 2) + damping_skew * skew_unit * LAM / (LAM ** 2 + w ** 2)
                        for w in OMEGAS])
    excitation = np.array([[[2e5 * (1 + 0.1 * i) * np.exp(1j * (0.2 * i + 0.05 * o)) for i in range(n)]]
                           for o, _ in enumerate(OMEGAS)])
    hydro_bodies = tuple(anyhydro.HydroBody(body_id=b.body_id, name=b.name, mesh=mesh, lid_mesh=lid,
                                            rotation_center=tuple(b.reference_position),
                                            center_of_mass=tuple(np.asarray(b.reference_position)
                                                                 + np.asarray(b.mass_properties.cg_body)),
                                            enabled_dofs=anyhydro.body_dof_keys(b.body_id))
                         for b, mesh, lid in hbodies)
    request = anyhydro.HydroRequest(omegas=OMEGAS.copy(), wave_directions=np.array([0.0]), water_depth=math.inf,
                                    rho=RHO, gravity=G, include_zero_frequency=False, include_infinite_frequency=False,
                                    backend_settings={"fixture": "rigid-import-analytic"})
    ds = anyhydro.assemble_hydro_dataset(
        hydro_bodies, request, added_mass=added, radiation_damping=damping, excitation_force=excitation,
        hydrostatic_stiffness=K, rotation_center=np.array([b.reference_position for b in bodies], float),
        center_of_mass=np.array([np.asarray(b.reference_position) + np.asarray(b.mass_properties.cg_body)
                                 for b in bodies]),
        center_of_buoyancy=np.array(cbs), added_mass_infinite=a_inf,
        added_mass_infinite_source="analytic-lorentzian", backend="analytic-test-fixture", backend_version="1.0.0",
        backend_commit="unknown", qualification_status="development_fixture")
    if permutation is not None:                       # the same labelled data in another stored DOF order
        perm = [6 * k + i for k in range(len(bodies)) for i in permutation]
        ds = ds.isel(influenced_dof=perm, radiating_dof=perm)
    from anyhydro.hydrostatic_contract import HydrostaticContract, attach_hydrostatic_contract
    provenance = {"source": "analytic box"}
    if provenance_g is not None:
        provenance.update(rho=RHO, gravity=provenance_g)
    hydrostatic = HydrostaticContract(
        role="BUOYANCY_ONLY", force_basis="PHYSICAL_WRENCH", rate_map="spatial_increments_exp_chart_v1",
        stiffness_derivative="INCREMENTAL_ABOUT_EQUILIBRIUM", bodies=tuple(b.body_id for b in bodies),
        baseline_wrench=baselines, provenance=provenance, full_source=None)
    if extend is not None:                            # extra dataset blocks (e.g. mean drift) before any binding
        ds = extend(ds)
    if contract:
        ds = attach_hydrostatic_contract(ds, hydrostatic)
    low = dict(low_tail_policy="linear_to_zero_extension", extension_name="linear_to_zero")
    if explicit_b0 is not None:
        low = dict(low_tail_policy="explicit_zero_sample", zero_frequency_damping=explicit_b0)
    settings = anyhydro.RadiationSettings(dt=DT, n_time=N_TIME, symmetry_tol=symmetry_tol, passivity_tol=1e-6, time_window=H,
                                          time_window_shape="bartlett", quadrature_method="piecewise_linear_exact",
                                          execution_limits=anyhydro.RadiationExecutionLimits(max_time_samples=N_TIME),
                                          **low)
    kernel = anyhydro.radiation_kernel_from_dataset(ds, settings)
    dataset_path, kernel_path = tmp_path / f"{tag}-ds.nc", tmp_path / f"{tag}-kernel.nc"
    anyhydro.save_hydro_dataset(ds, dataset_path)
    anyhydro.save_radiation_kernel(kernel, kernel_path)
    return dataset_path, kernel_path, ds, kernel


def _mooring(project, period=100.0):
    records = []
    for b in project.bodies:
        m_eff = np.array([MASS * (1 + 4.0 * 0.3)] * 2 + [INERTIA[2][2] * (1 + 4.0 * 0.3)])
        k = m_eff * (2 * math.pi / period) ** 2
        records.append(dict(kind="stiffness", dofs=[f"{b.body_id}:surge", f"{b.body_id}:sway", f"{b.body_id}:yaw"],
                            matrix=np.diag(k).tolist(),
                            source="declared weak mooring (owner decision 2026-10-15), natural periods 100 s"))
    return records


def _options(project, **overrides):
    opt = dict(dofs={b.body_id: list(DOFS) for b in project.bodies}, length_scale=12.0, gravity=G, n_steps=801,
               criteria=dict(omega_min=0.4, omega_max=1.6, block_rel_tol=0.1, modal_rel_tol=0.1,
                             energetic_fraction=1e-3, cluster_fraction=0.01),
               external_matrices=_mooring(project),
               wave=dict(omega=float(OMEGAS[40]), heading=0.0, amplitude=1.0, phase=0.0))
    opt.update(overrides)
    return opt


# ---------------------------------------------------------------------------------------------- admission

def test_six_dof_import_on_the_reference_axis(tmp_path):
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project)
    inputs, provenance = import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))
    details = _jsonable_value(inputs.provenance.details)
    assert inputs.layout == LINEAR_LAYOUT_RIGID and inputs.ndof == 6
    K = np.asarray(inputs.stiffness)
    # restoring = buoyancy (chart = source, zero baseline moment) + gravity (0.5 W on roll/pitch) + mooring
    assert K[2, 2] == pytest.approx(RHO * G * 72.0)
    assert K[3, 3] == pytest.approx(RHO * G * (216.0 - 225.0) + 0.5 * W, rel=1e-12)
    assert K[4, 4] == pytest.approx(RHO * G * (864.0 - 225.0) + 0.5 * W, rel=1e-12)
    assert K[5, 5] == pytest.approx(_mooring(project)[0]["matrix"][2][2], rel=1e-12)
    assert np.allclose(K, K.T)
    assert details["equilibrium_residuals"]["vessel"]["force"] <= 1e-9 * W
    assert details["skew_ratios"]["kernel_max"] <= 1e-12
    assert json.loads(json.dumps(details))["format"] == "anymarine-rigid-hydro-import-v1"
    assert "damping_noise_admitted" not in details          # F2b: a clean source records nothing (identical details)
    assert np.any(np.asarray(inputs.forces))


def test_offset_buoyancy_centre_exercises_the_chart_conversion(tmp_path):
    """Nonzero baseline moment: the source block is not symmetric; chart conversion + gravity make it so."""
    xb, yb = 1.0, -0.5
    project = _project((_body("vessel", cb_offset=(xb, yb)),))
    ds_path, k_path, _, _ = _source(tmp_path, project)
    inputs, provenance = import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))
    details = _jsonable_value(inputs.provenance.details)
    K = np.asarray(inputs.stiffness)
    assert np.allclose(K, K.T, rtol=0, atol=1e-9 * W)
    assert details["skew_ratios"]["K_buoyancy_chart"] <= 1e-12          # the converted block is symmetric
    assert K[3, 3] == pytest.approx(RHO * G * (216.0 + 72.0 * yb ** 2 - 225.0) + 0.5 * W, rel=1e-12)
    # yaw-roll coupling: buoyancy -W x_b / 2 (chart) and gravity +W x_b / 2 cancel
    assert K[3, 5] == pytest.approx(0.0, abs=1e-9 * W)
    assert details["equilibrium_residuals"]["vessel"]["moment"] <= 1e-9 * W * 12.0


def test_dataset_dof_order_does_not_change_the_inputs(tmp_path):
    project = _project((_body("vessel", cb_offset=(1.0, -0.5)),))
    canonical = import_rigid_hydro_inputs(project, *_source(tmp_path, project, tag="c")[:2], _options(project))[0]
    swapped_src = _source(tmp_path, project, permutation=(0, 1, 2, 3, 5, 4), tag="p")
    swapped = import_rigid_hydro_inputs(project, *swapped_src[:2], _options(project))[0]
    for name in ("stiffness", "added_mass_infinite", "mass_dry", "damping"):
        assert np.allclose(np.asarray(getattr(swapped, name)), np.asarray(getattr(canonical, name)),
                           rtol=1e-12, atol=1e-9), name
    assert np.allclose(np.asarray(swapped.radiation_kernel), np.asarray(canonical.radiation_kernel),
                       rtol=1e-12, atol=1e-9)
    assert np.allclose(np.asarray(swapped.forces), np.asarray(canonical.forces), rtol=1e-12, atol=1e-6)


def test_two_bodies_import_with_body_blocks_by_identity(tmp_path):
    project = _project((_body("a"), _body("b", x0=30.0, cb_offset=(0.5, 0.0))))
    ds_path, k_path, _, _ = _source(tmp_path, project)
    inputs, _ = import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))
    assert inputs.ndof == 12 and inputs.labels[6] == "b:surge"


def test_imported_inputs_run_through_the_rigid_route(tmp_path):
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project)
    inputs, _ = import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, n_steps=401))
    result = run_linear(inputs, producing_bodies=project.bodies)
    verify_linear_result(result, inputs)


# ---------------------------------------------------------------------------------------------- refusals

def test_neutral_modes_without_declared_stiffness_are_refused(tmp_path):
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project)
    with pytest.raises(HydroAdmissionError, match="K_total"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, external_matrices=[]))


def test_explicit_negative_b0_is_refused(tmp_path):
    project = _project()
    try:
        ds_path, k_path, _, _ = _source(tmp_path, project, explicit_b0=-1000.0 * np.eye(6))
    except anyhydro.HydroError:
        pytest.skip("the producer itself refuses a non-PSD explicit B(0)")
    with pytest.raises(HydroAdmissionError, match="passiv|B sample|B\\(0\\)"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))


def test_one_declared_g_and_equilibrium(tmp_path):
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project)
    with pytest.raises(HydroAdmissionError, match="one declared g"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, gravity=G * 1.0005))
    unknown_g = _source(tmp_path, project, provenance_g=None, tag="u")
    with pytest.raises(HydroAdmissionError, match="one declared g"):
        import_rigid_hydro_inputs(project, *unknown_g[:2], _options(project))
    heavy = _project((dataclasses.replace(_body("vessel"), mass=1.01 * MASS),))
    with pytest.raises(HydroAdmissionError, match="not in equilibrium"):
        import_rigid_hydro_inputs(heavy, ds_path, k_path, _options(heavy))
    off = _project((dataclasses.replace(_body("vessel"), mass_properties=BodyMassProperties((0.5, 0.0, -0.5),
                                                                                            INERTIA)),))
    with pytest.raises(HydroAdmissionError, match="not in equilibrium"):
        import_rigid_hydro_inputs(off, ds_path, k_path, _options(off))


def test_kernel_binding_by_a_inf(tmp_path):
    project = _project()
    ds_path, _, _, kernel = _source(tmp_path, project)
    tampered = dataclasses.replace(kernel, added_mass_infinite=np.asarray(kernel.added_mass_infinite) * 1.01)
    path = tmp_path / "tampered.nc"
    anyhydro.save_radiation_kernel(tampered, path)
    with pytest.raises(HydroAdmissionError, match="A_inf|source dataset"):
        import_rigid_hydro_inputs(project, ds_path, path, _options(project))


def test_dataset_lids_are_refused(tmp_path):
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project, dataset_lid=True)
    with pytest.raises(HydroGeometryError, match="lid"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))


def test_external_records_are_admitted_one_by_one(tmp_path):
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project)
    skewed = _mooring(project)
    skewed[0]["matrix"][0][1] = 0.5 * skewed[0]["matrix"][0][0]
    with pytest.raises(HydroAdmissionError, match="skew"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, external_matrices=skewed))
    negative = dict(kind="damping", dofs=["vessel:heave"], matrix=[[-1.0]], source="bad")
    positive = dict(kind="damping", dofs=["vessel:heave"], matrix=[[2.0]], source="good")
    with pytest.raises(HydroAdmissionError, match="damping"):
        import_rigid_hydro_inputs(project, ds_path, k_path,
                                  _options(project, external_matrices=_mooring(project) + [negative, positive]))
    for key, value in (("axes", "body"), ("semantics", "replacement")):
        contradictory = dict(_mooring(project)[0], **{key: value})
        with pytest.raises(HydroOptionsError, match=key):
            import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, external_matrices=[contradictory]))


def test_forcing_must_be_a_grid_value_inside_the_admitted_window(tmp_path):
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project)
    with pytest.raises(HydroOptionsError, match="window"):
        import_rigid_hydro_inputs(project, ds_path, k_path,
                                  _options(project, wave=dict(omega=float(OMEGAS[-1]), heading=0.0, amplitude=1.0)))
    with pytest.raises(HydroOptionsError, match="grid values"):
        import_rigid_hydro_inputs(project, ds_path, k_path,
                                  _options(project, wave=dict(omega=0.9123, heading=0.0, amplitude=1.0)))


def test_gate_coverage_options_geometry_and_window_shape(tmp_path):
    project = _project()
    ds_path, k_path, ds, _ = _source(tmp_path, project)
    strict = _options(project, criteria=dict(omega_min=0.4, omega_max=1.6, block_rel_tol=1e-9, modal_rel_tol=1e-9,
                                             energetic_fraction=1e-3, cluster_fraction=0.01))
    with pytest.raises(HydroAdmissionError, match="full-block reconstruction gate"):
        import_rigid_hydro_inputs(project, ds_path, k_path, strict)
    with pytest.raises(HydroAdmissionError, match="cover every lag"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, n_steps=N_TIME + 1))
    opts = _options(project)
    del opts["gravity"]
    with pytest.raises(HydroOptionsError, match="gravity"):
        import_rigid_hydro_inputs(project, ds_path, k_path, opts)
    moved = _project((dataclasses.replace(project.bodies[0], reference_position=(0.1, 0.0, 0.0)),))
    with pytest.raises(HydroGeometryError):
        import_rigid_hydro_inputs(moved, ds_path, k_path, _options(moved))
    settings = anyhydro.RadiationSettings(dt=DT, n_time=N_TIME, symmetry_tol=1e-6, passivity_tol=1e-6,
                                          low_tail_policy="linear_to_zero_extension", extension_name="linear_to_zero",
                                          time_window=H, quadrature_method="piecewise_linear_exact",
                                          execution_limits=anyhydro.RadiationExecutionLimits(max_time_samples=N_TIME))
    rect = tmp_path / "rect.nc"
    anyhydro.save_radiation_kernel(anyhydro.radiation_kernel_from_dataset(ds, settings), rect)
    with pytest.raises(HydroAdmissionError, match="Bartlett"):
        import_rigid_hydro_inputs(project, ds_path, rect, _options(project))


# ---------------------------------------------------------------------------------------------- review round 2

def test_every_explicit_source_gravity_must_equal_the_run_g(tmp_path):
    """A schema 1.3 dataset also declares g in its wave environment and as a scalar: all declarations count."""
    from anyhydro.hydrostatic_contract import read_hydrostatic_contract
    from anymarine.hydro_rigid import _source_gravity_declarations
    project = _project()
    _, _, ds, _ = _source(tmp_path, project)
    contract = read_hydrostatic_contract(ds)
    assert _source_gravity_declarations(ds, contract) == {"hydrostatic contract provenance": G}
    kochin = ds.assign(g=((), 4.905))
    kochin.attrs["wave_environment"] = json.dumps(dict(water_depth="inf", rho=RHO, g=4.905))
    found = _source_gravity_declarations(kochin, contract)
    assert found == {"hydrostatic contract provenance": G, "wave_environment attribute": 4.905,
                     "dataset scalar g": 4.905}


def test_kernel_body_identity_is_bound(tmp_path):
    project = _project()
    ds_path, _, _, kernel = _source(tmp_path, project)
    path = tmp_path / "foreign.nc"
    anyhydro.save_radiation_kernel(dataclasses.replace(kernel, body_ids=("foreign-body",)), path)
    with pytest.raises(HydroAdmissionError, match="body identities|source dataset"):
        import_rigid_hydro_inputs(project, ds_path, path, _options(project))


def test_gate_and_wave_option_failures_are_typed(tmp_path):
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project)
    empty = _options(project, criteria=dict(omega_min=4.0, omega_max=5.0, block_rel_tol=0.1, modal_rel_tol=0.1,
                                            energetic_fraction=1e-3, cluster_fraction=0.01))
    with pytest.raises((HydroAdmissionError, HydroOptionsError)):
        import_rigid_hydro_inputs(project, ds_path, k_path, empty)
    with pytest.raises(HydroOptionsError, match="finite JSON"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, wave=dict(
            omega=float(OMEGAS[40]), heading=0.0, amplitude=math.nan)))
    for key, bad in (("heading", "east"), ("amplitude", "x"), ("phase", "x")):
        wave = dict(omega=float(OMEGAS[40]), heading=0.0, amplitude=1.0, phase=0.0)
        wave[key] = bad
        with pytest.raises(HydroOptionsError, match=key):
            import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, wave=wave))


def test_a_source_without_hydrostatic_contract_is_refused(tmp_path):
    """M-e: no restoring is composed from a dataset whose hydrostatic semantics are undeclared (ADR-004)."""
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project, contract=False)
    with pytest.raises(HydroAdmissionError, match="ANYhydro source was refused|hydrostatic"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))


def test_indefinite_restoring_is_refused(tmp_path):
    """M-e review: a nonsingular but indefinite K_total (CG high above the CB: roll unstable) is refused, not only
    the neutral (zero-eigenvalue) case."""
    project = _project((dataclasses.replace(_body("vessel"), mass_properties=BodyMassProperties((0.0, 0.0, 3.0),
                                                                                              INERTIA)),))
    ds_path, k_path, _, _ = _source(tmp_path, project)
    with pytest.raises(HydroAdmissionError, match="K_total"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))


def test_skewed_source_buoyancy_stiffness_is_refused(tmp_path):
    """M-e review: the declared symmetrisation refuses a source stiffness whose skew part (after the chart
    conversion) exceeds 1e-2 of the operator - not only skewed external records."""
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project, stiffness_skew=0.5 * RHO * G * 864.0)
    with pytest.raises(HydroAdmissionError, match="K_buoyancy_chart"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))


def test_skewed_added_mass_infinite_is_refused(tmp_path):
    """M-e review round 2: the producer copies A_inf without a symmetry refusal; the importer's own declared
    symmetrisation of A_inf must refuse a skew part above 1e-2 (A(w) carries the same offset: consistent source)."""
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project, a_inf_skew=0.5 * MASS * 13.0)
    with pytest.raises(HydroAdmissionError, match="A_inf"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))


def test_skewed_kernel_samples_are_refused(tmp_path):
    """M-e review round 2: the importer's own per-sample symmetrisation refuses a skewed kernel artifact (bound to
    the dataset, zero producer diagnostics); a skewed source damping is already refused through the producer's
    damping-symmetry diagnostics."""
    project = _project()
    ds_path, _, _, kernel = _source(tmp_path, project)
    samples = np.array(kernel.kernel)
    skew = np.zeros((6, 6))
    skew[3, 4], skew[4, 3] = 1.0, -1.0
    path = tmp_path / "skewed-kernel.nc"
    anyhydro.save_radiation_kernel(dataclasses.replace(kernel, kernel=samples + 0.5 * np.abs(samples).max() * skew),
                                   path)
    with pytest.raises(HydroAdmissionError, match="kernel sample 0: skew part"):
        import_rigid_hydro_inputs(project, ds_path, path, _options(project))
    skewed_source = _source(tmp_path, project, damping_skew=0.5 * MASS * 13.0 * LAM, symmetry_tol=10.0, tag="b")
    with pytest.raises(HydroAdmissionError, match="damping-symmetry violations"):
        import_rigid_hydro_inputs(project, *skewed_source[:2], _options(project))


@pytest.mark.parametrize("installed", ["0.2.0", "0.1.9", "", "dev", "0.2.1.dev0", "0.2.1rc1", "0.2.1.garbage",
                                       "0.2.1.post1", "0.2"])
def test_an_older_anyhydro_is_a_typed_refusal(tmp_path, monkeypatch, installed):
    """The six-DOF route needs anyhydro >= 0.2.1; an older installation is refused with a typed error, not an
    ImportError from a missing ANYhydro feature."""
    from anymarine.hydro import HydroUnavailableError
    project = _project()
    ds_path, k_path, _, _ = _source(tmp_path, project)
    monkeypatch.setattr(anyhydro, "__version__", installed)
    with pytest.raises(HydroUnavailableError, match="anyhydro >= 0.2.1"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))


@pytest.mark.parametrize("installed", ["0.2.1", "0.2.1+local", "0.2.1+local.7", "0.2.9", "0.3.0"])
def test_final_anyhydro_releases_at_or_above_the_minimum_are_accepted(monkeypatch, installed):
    from anymarine.hydro_rigid import _rigid_anyhydro
    monkeypatch.setattr(anyhydro, "__version__", installed)
    assert _rigid_anyhydro() is anyhydro

