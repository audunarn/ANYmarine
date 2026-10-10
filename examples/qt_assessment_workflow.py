"""Prepare a real stored project for the Qt assessment workflow.

Role: ``analytic_rehearsal_only``. The radiation kernel is the explicit
exponential analytic construction shared with ``linear_assessment.py``
(not BEM, not a W5 qualification claim). The example registers an
assessment case BEFORE the solve, stores the actual linear result in a
saved project and writes the registered case artifact, so the Qt
application (``python -m anymarine.qtapp``) can open the project and run
the complete Assessment menu workflow — assess the selected stored
result, save an exclusive snapshot, reopen and replay it — against real
development evidence.

No criteria are inferred from observed data and no automatic grids or
campaigns run here; the explicit case specification is the one authority.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# Reuse the admitted analytic rehearsal fixtures from the sibling
# example (same directory); no physics is duplicated or re-derived.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from linear_assessment import (  # noqa: E402
    DT_COARSE,
    N_COARSE,
    WINDOWS,
    _regular_wave_inputs,
    _spec,
)

from anymarine.linear_assessment import (  # noqa: E402
    MODE_REGULAR_WAVE,
    write_case_artifact,
)
from anymarine.services import ApplicationService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=None,
        help="output root (default: a fresh temporary directory)",
    )
    args = parser.parse_args()
    root = Path(args.out) if args.out else Path(
        tempfile.mkdtemp(prefix="qt-assessment-example-")
    )
    root.mkdir(parents=True, exist_ok=True)
    service = ApplicationService()

    print(f"output root: {root}")
    print(
        "role: analytic_rehearsal_only — analytic exponential radiation, "
        "development output, unqualified; not BEM, not a W5 claim"
    )

    project = service.new_project("Qt assessment workflow example")
    inputs = _regular_wave_inputs(project, dt=DT_COARSE, n_steps=N_COARSE)
    project = service.set_linear_inputs(project, inputs)

    # The case is registered BEFORE the solve, exactly as the Qt workflow
    # requires: explicit specification, explicit target, no inference.
    case = service.prepare_linear_assessment(
        project,
        _spec(
            "qt-assessment-example-case",
            MODE_REGULAR_WAVE,
            inputs,
            windows=WINDOWS,
        ),
    )
    case_path = root / "registered-case.json"
    write_case_artifact(case, case_path)
    print(f"registered case artifact: {case_path} (case {case.case_id})")

    # The actual linear solve and its stored result (public service route).
    result = service.run_linear_local(project)
    project = service.add_result(project, result)
    project_root = root / "project"
    service.save(project, project_root)
    print(f"stored project: {project_root} (result {result.result_id})")
    print(
        "next (GUI): python -m anymarine.qtapp -> open "
        f"{project_root} -> Assessment menu -> Register/Assess/Pair/Reopen; "
        f"the registered case artifact is {case_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
