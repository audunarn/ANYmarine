"""Six-DOF rigid_subset_v1 hydro import (ADR-015 slice L6-3, M-a/M-b) on a synthetic, analytically known source.

Source: a 12 x 6 m box half-submerged (draft 2.5 m, V = 180 m^3, CB at z = -1.25 below the reference point), CG at
z = -2.0 (roll and pitch stable with gravity), a passive Lorentzian radiation model B(w) = C lam / (lam^2 + w^2), the
attached ANYhydro hydrostatic contract and a Bartlett-tapered kernel. Surge, sway and yaw get the owner's declared
weak mooring (external stiffness records).
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
                              default_box_mesh)
from anymarine.hydro import HydroAdmissionError, HydroGeometryError, HydroOptionsError  # noqa: E402
from anymarine.hydro_rigid import _world_mesh, import_rigid_hydro_inputs  # noqa: E402
from anymarine.linear import run_linear, verify_linear_result  # noqa: E402
from anymarine.mass_properties import BodyMassProperties  # noqa: E402

pytestmark = requires_solver

RHO, G = 1025.0, 9.81
V = 12.0 * 6.0 * 2.5
MASS = RHO * V
INERTIA = ((MASS * 5.0, 0.0, 0.0), (0.0, MASS * 13.0, 0.0), (0.0, 0.0, MASS * 16.0))
OMEGAS = np.linspace(0.05, 3.0, 120)
LAM = 0.8
DT, N_TIME, H = 0.05, 2001, 60.0
DOFS = ("surge", "sway", "heave", "roll", "pitch", "yaw")


def _project(cg=(0.0, 0.0, -2.0), mass=MASS):
    body = Body(body_id="vessel", name="Box vessel", reference_position=(0.0, 0.0, 0.0), mass=mass,
                mesh=default_box_mesh((12.0, 6.0, 5.0)), mass_properties=BodyMassProperties(cg, INERTIA))
    return ProjectModel(name="rigid import fixture", bodies=(body,),
                        equilibrium=EquilibriumMetadata(waterline_z=0.0, description="analytic", source="synthetic"))


def _radiation():
    scale = np.array([MASS, MASS, MASS, INERTIA[0][0], INERTIA[1][1], INERTIA[2][2]]) * 0.3
    D = np.diag(np.sqrt(scale))
    Q = 0.9 * np.eye(6) + 0.1 * np.ones((6, 6))                       # PSD correlation
    C = D @ Q @ D * LAM                                                # B(0) scale
    a_inf = np.diag(scale) * 4.0                                        # A(w) stays well away from zero
    return C, a_inf


def _source(tmp_path, project, *, skew_kernel=False, heave_area=72.0):
    C, a_inf = _radiation()
    added = np.array([a_inf - C / (LAM ** 2 + w ** 2) for w in OMEGAS])
    damping = np.array([C * LAM / (LAM ** 2 + w ** 2) for w in OMEGAS])
    excitation = np.array([[[2e5 * (1 + 0.1 * i) * np.exp(1j * (0.2 * i + 0.05 * k)) for i in range(6)]]
                           for k, _ in enumerate(OMEGAS)])
    K = np.zeros((6, 6))
    K[2, 2] = RHO * G * heave_area
    K[3, 3] = RHO * G * (12 * 6 ** 3 / 12 + V * (-1.25))
    K[4, 4] = RHO * G * (6 * 12 ** 3 / 12 + V * (-1.25))
    body = project.bodies[0]
    hbody = anyhydro.HydroBody(body_id=body.body_id, name=body.name, mesh=_world_mesh(anyhydro, body), lid_mesh=None,
                               rotation_center=(0.0, 0.0, 0.0), center_of_mass=(0.0, 0.0, -2.0),
                               enabled_dofs=anyhydro.body_dof_keys(body.body_id))
    request = anyhydro.HydroRequest(omegas=OMEGAS.copy(), wave_directions=np.array([0.0]), water_depth=math.inf,
                                    rho=RHO, gravity=G, include_zero_frequency=False, include_infinite_frequency=False,
                                    backend_settings={"fixture": "rigid-import-analytic"})
    ds = anyhydro.assemble_hydro_dataset(
        (hbody,), request, added_mass=added, radiation_damping=damping, excitation_force=excitation,
        hydrostatic_stiffness=K, rotation_center=np.zeros((1, 3)), center_of_mass=np.array([[0.0, 0.0, -2.0]]),
        center_of_buoyancy=np.array([[0.0, 0.0, -1.25]]), added_mass_infinite=a_inf,
        added_mass_infinite_source="analytic-lorentzian", backend="analytic-test-fixture", backend_version="1.0.0",
        backend_commit="unknown", qualification_status="development_fixture")
    from anyhydro.hydrostatic_contract import HydrostaticContract, attach_hydrostatic_contract
    contract = HydrostaticContract(
        role="BUOYANCY_ONLY", force_basis="PHYSICAL_WRENCH", rate_map="spatial_increments_exp_chart_v1",
        stiffness_derivative="INCREMENTAL_ABOUT_EQUILIBRIUM", bodies=(body.body_id,),
        baseline_wrench={body.body_id: (0.0, 0.0, RHO * G * V, 0.0, 0.0, 0.0)}, provenance={"source": "analytic box"},
        full_source=None)
    ds = attach_hydrostatic_contract(ds, contract)
    settings = anyhydro.RadiationSettings(dt=DT, n_time=N_TIME, symmetry_tol=1e-6, passivity_tol=1e-6,
                                          low_tail_policy="linear_to_zero_extension", extension_name="linear_to_zero",
                                          time_window=H, time_window_shape="bartlett",
                                          quadrature_method="piecewise_linear_exact",
                                          execution_limits=anyhydro.RadiationExecutionLimits(max_time_samples=N_TIME))
    kernel = anyhydro.radiation_kernel_from_dataset(ds, settings)
    dataset_path, kernel_path = tmp_path / "ds.nc", tmp_path / "kernel.nc"
    anyhydro.save_hydro_dataset(ds, dataset_path)
    anyhydro.save_radiation_kernel(kernel, kernel_path)
    return dataset_path, kernel_path


def _mooring(project, period=100.0):
    m_eff = np.array([MASS + 4.0 * 0.3 * MASS] * 2 + [INERTIA[2][2] * (1 + 4.0 * 0.3)])
    k = m_eff * (2 * math.pi / period) ** 2
    return dict(kind="stiffness", dofs=["vessel:surge", "vessel:sway", "vessel:yaw"], matrix=np.diag(k).tolist(),
                source="declared weak mooring (owner decision 2026-10-15), natural periods 100 s")


def _options(project, **overrides):
    opt = dict(dofs={"vessel": list(DOFS)}, length_scale=12.0, gravity=G, n_steps=801,
               criteria=dict(omega_min=0.4, omega_max=1.6, block_rel_tol=0.1, modal_rel_tol=0.1,
                             energetic_fraction=1e-3, cluster_fraction=0.01),
               external_matrices=[_mooring(project)],
               wave=dict(omega=float(OMEGAS[40]), heading=0.0, amplitude=1.0, phase=0.0))
    opt.update(overrides)
    return opt


def test_six_dof_import_assembles_contract_restoring_gravity_and_admits(tmp_path):
    project = _project()
    ds_path, k_path = _source(tmp_path, project)
    inputs, details = import_rigid_hydro_inputs(project, ds_path, k_path, _options(project))
    assert inputs.layout == LINEAR_LAYOUT_RIGID and inputs.ndof == 6
    K = np.asarray(inputs.stiffness)
    W = MASS * G
    # restoring = buoyancy (chart = source, zero baseline moment) + gravity (2 W on roll/pitch, 0 on yaw) + mooring
    assert K[2, 2] == pytest.approx(RHO * G * 72.0)
    assert K[3, 3] == pytest.approx(RHO * G * (216.0 - 225.0) + 2.0 * W, rel=1e-12)
    assert K[4, 4] == pytest.approx(RHO * G * (864.0 - 225.0) + 2.0 * W, rel=1e-12)
    assert K[5, 5] == pytest.approx(_mooring(project)["matrix"][2][2], rel=1e-12)
    assert np.allclose(K, K.T)
    assert details["equilibrium_residuals"]["vessel"]["force"] <= 1e-9 * W
    assert details["skew_ratios"]["kernel_max"] <= 1e-12
    assert json.loads(json.dumps(details))["format"] == "anymarine-rigid-hydro-import-v1"
    assert np.any(np.asarray(inputs.forces))


def test_imported_inputs_run_through_the_rigid_route(tmp_path):
    project = _project()
    ds_path, k_path = _source(tmp_path, project)
    inputs, _ = import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, n_steps=401))
    result = run_linear(inputs, producing_bodies=project.bodies)
    verify_linear_result(result, inputs)


def test_neutral_modes_without_declared_stiffness_are_refused(tmp_path):
    project = _project()
    ds_path, k_path = _source(tmp_path, project)
    with pytest.raises(HydroAdmissionError, match="K_total"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, external_matrices=[]))


def test_equilibrium_and_gravity_declarations_are_checked(tmp_path):
    project = _project()
    ds_path, k_path = _source(tmp_path, project)
    with pytest.raises(HydroAdmissionError, match="not in equilibrium"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, gravity=9.80))
    off = _project(cg=(0.5, 0.0, -2.0))                       # CG not above CB: moment residual
    with pytest.raises(HydroAdmissionError, match="not in equilibrium"):
        import_rigid_hydro_inputs(off, ds_path, k_path, _options(off))


def test_gate_kernel_contract_and_option_refusals(tmp_path):
    project = _project()
    ds_path, k_path = _source(tmp_path, project)
    strict = _options(project, criteria=dict(omega_min=0.4, omega_max=1.6, block_rel_tol=1e-9, modal_rel_tol=1e-9,
                                             energetic_fraction=1e-3, cluster_fraction=0.01))
    with pytest.raises(HydroAdmissionError, match="full-block reconstruction gate"):
        import_rigid_hydro_inputs(project, ds_path, k_path, strict)
    with pytest.raises(HydroAdmissionError, match="cover every lag"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, n_steps=N_TIME + 1))
    with pytest.raises(HydroOptionsError, match="gravity"):
        opts = _options(project)
        del opts["gravity"]
        import_rigid_hydro_inputs(project, ds_path, k_path, opts)
    with pytest.raises(HydroOptionsError, match="grid values"):
        import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, wave=dict(omega=0.123, heading=0.0,
                                                                                         amplitude=1.0)))
    moved = dataclasses.replace(project, bodies=(dataclasses.replace(project.bodies[0], reference_position=(0.1, 0, 0)),))
    with pytest.raises(HydroGeometryError):
        import_rigid_hydro_inputs(moved, ds_path, k_path, _options(moved))


def test_rectangular_window_kernels_are_refused(tmp_path):
    project = _project()
    ds_path, _ = _source(tmp_path, project)
    ds = anyhydro.load_hydro_dataset(ds_path)
    settings = anyhydro.RadiationSettings(dt=DT, n_time=N_TIME, symmetry_tol=1e-6, passivity_tol=1e-6,
                                          low_tail_policy="linear_to_zero_extension", extension_name="linear_to_zero",
                                          time_window=H, quadrature_method="piecewise_linear_exact",
                                          execution_limits=anyhydro.RadiationExecutionLimits(max_time_samples=N_TIME))
    rect = tmp_path / "rect.nc"
    anyhydro.save_radiation_kernel(anyhydro.radiation_kernel_from_dataset(ds, settings), rect)
    with pytest.raises(HydroAdmissionError, match="Bartlett"):
        import_rigid_hydro_inputs(project, ds_path, rect, _options(project))
