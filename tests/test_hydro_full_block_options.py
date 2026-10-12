"""Full-block (W5 K1-K3) reconstruction criteria in the hydro import options."""
from __future__ import annotations

import pytest

from anymarine import hydro

FULL_BLOCK = {
    "form": "full_block",
    "omega_min": 2.0,
    "omega_max": 6.0,
    "block_rel_tol": 0.02,
    "modal_rel_tol": 0.02,
    "energetic_fraction": 1e-3,
    "cluster_fraction": 0.01,
}


def test_full_block_criteria_parse_with_and_without_modal_reference():
    for criteria in (FULL_BLOCK, dict(FULL_BLOCK, modal_reference="spectral_radius")):
        parsed = hydro._parse_options({"n_steps": 11, "criteria": criteria})["criteria"]
        assert parsed["form"] == "full_block"
        assert parsed["modal_reference"] == "spectral_radius"
        assert parsed["block_rel_tol"] == 0.02


@pytest.mark.parametrize("bad", [
    dict(FULL_BLOCK, modal_reference="own_eigenvalue"),
    dict(FULL_BLOCK, form="other"),
    {k: v for k, v in FULL_BLOCK.items() if k != "modal_rel_tol"},
    dict(FULL_BLOCK, added_mass_abs_tol=0.1),
])
def test_invalid_full_block_criteria_are_refused(bad):
    with pytest.raises(hydro.HydroOptionsError):
        hydro._parse_options({"n_steps": 11, "criteria": bad})


def test_legacy_criteria_unchanged():
    legacy = {"omega_min": 0.5, "omega_max": 4.0, "added_mass_abs_tol": 50.0,
              "added_mass_rel_tol": 0.05, "damping_abs_tol": 50.0, "damping_rel_tol": 0.05}
    assert hydro._parse_options({"n_steps": 11, "criteria": legacy})["criteria"] == legacy
