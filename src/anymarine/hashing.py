"""Canonical input hashing for stale-result detection.

The input hash covers every project input that can change a result: body
identity/geometry/mass, equilibrium metadata and analysis settings. Stored
results carry the exact hash of the inputs that produced them; any later edit
changes the hash and the result is flagged stale.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .domain import AnalysisSettings, BodyMesh, EquilibriumMetadata, ProjectModel


def canonical_json(data: Any) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace."""

    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def mesh_hash(mesh: BodyMesh) -> str:
    """sha256 over the canonical mesh data: the immutable geometry identity."""

    payload = canonical_json(mesh.to_dict()).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def equilibrium_hash(equilibrium: EquilibriumMetadata) -> str:
    """sha256 over the canonical equilibrium metadata (waterline etc)."""

    payload = canonical_json(equilibrium.to_dict()).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def project_input_dict(project: ProjectModel, settings: AnalysisSettings | None = None) -> dict:
    """Plain-dict view of all result-relevant project inputs.

    *settings* overrides the project's own settings when a run uses effective
    settings different from the stored ones; the hash must identify the
    inputs that actually produced (or will produce) a result.
    """

    settings = settings if settings is not None else project.settings
    bodies = []
    for body in project.bodies:
        entry = {
            "body_id": body.body_id,
            "name": body.name,
            "reference_position": list(body.reference_position),
            "mass": float(body.mass),
            "mesh": body.mesh.to_dict(),
        }
        if body.reference_orientation != ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.)):
            entry["reference_orientation"] = [list(row) for row in body.reference_orientation]
        if body.hydro_lid is not None:
            # The numerical lid is result-relevant geometry: a lid edit must
            # change the project input hash exactly like a hull edit. Omitted
            # when None so lidless projects keep their exact legacy hashes.
            entry["hydro_lid"] = body.hydro_lid.to_dict()
        if body.hydro_lid_policy != "free_surface_v1":
            entry["hydro_lid_policy"] = body.hydro_lid_policy
        if body.hydro_surfaces:
            entry["hydro_surfaces"] = [s.to_dict() for s in body.hydro_surfaces]
        if body.mass_properties is not None:
            # Optional declared mechanical inputs invalidate all dependent runs.
            # Absence is omitted so every legacy input hash is unchanged.
            entry["mass_properties"] = body.mass_properties.to_dict()
        bodies.append(entry)
    return {
        **({"equilibrium_adoption": project.to_dict()["equilibrium_adoption"]} if project.equilibrium_adoption is not None else {}),
        **({"line_equilibrium_inputs": project.line_equilibrium_inputs.to_dict()} if project.line_equilibrium_inputs is not None else {}),
        **({"native_inputs": project.native_inputs.to_dict()} if project.native_inputs is not None else {}),
        "bodies": bodies,
        "equilibrium": project.equilibrium.to_dict(),
        "settings": settings.to_dict(),
    }


def input_hash(project: ProjectModel, settings: AnalysisSettings | None = None) -> str:
    """sha256 over the canonical project input dictionary.

    Pass *settings* to hash the effective settings of a run that overrides the
    project's stored settings; without it the project's own settings are used.
    """

    payload = canonical_json(project_input_dict(project, settings)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def is_stale(project: ProjectModel, result_input_hash: str) -> bool:
    """True when stored result inputs differ from current project inputs."""

    return result_input_hash != input_hash(project)

