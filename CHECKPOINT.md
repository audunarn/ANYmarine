# ANYmarine restart checkpoint — ACTIVE (Claude), 2026-10-12

Development resumed by the user and handed to Claude. Codex is the independent
reviewer (GPT 6.1 Sol high default; GPT 6 Astra high for very complex reviews).
Goal and governing roadmap: [Implementation plan](docs/IMPLEMENTATION_PLAN.md);
living record: [TASK.md](TASK.md). Repositories C:/Github/ANYmarine and
C:/Github/ANYhydro; no ANYsolver edits. PySide6 Qt Widgets, ANY3dView viewer,
MPL-2.0 owned code; Apache-2.0 upstream Fortran preserved. W5 **NO_GO**, general
numerical admission false; full W5–W10, thin-sheet and release requirements open.

## Source and Git state

- ANYhydro main 81c04c6 (validated Prony fit, John oracle, portable CI classification)
  on top of pushed 36cd502.
- ANYmarine: records on top of pushed 76a6c0b. GUI unchanged from verified 4cd9a6c.
- Superseded worker component oracle archived (not in repo):
  C:/ANYworkspaceWork/anymarine-evidence/w5-free-surface-repair-20261011/superseded-component-oracle.

## Verified

Free-surface failure attributed and repaired; see TASK.md top section and
C:/ANYworkspaceWork/anymarine-evidence/w5-prony-attribution-20261012/ATTRIBUTION.md.

## Next action

The Prony fix does not explain the 03c energy gap (near/far moves <= 2e-3). Decide the W5
hydrodynamic source route: compare the custom retained panel assembly with production
Capytaine BEM (validated kernel) on the 03c geometry against HAMS and near/far flux.
Tests on Windows: runtime python with PYTHONPATH=C:/Github/ANYhydro/src (an installed
non-editable anyhydro otherwise shadows the source) and a writable --basetemp.
Linux native runtime: /var/tmp/anyhydro-e1-installed-repair-20261011 (Ubuntu-24.04).
