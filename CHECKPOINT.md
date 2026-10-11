# ANYmarine restart checkpoint — ACTIVE (Claude), 2026-10-11

Development resumed by the user and handed to Claude. Codex is the independent
reviewer (GPT 6.1 Sol high default; GPT 6 Astra high for very complex reviews).
Goal and governing roadmap: [Implementation plan](docs/IMPLEMENTATION_PLAN.md);
living record: [TASK.md](TASK.md). Repositories C:/Github/ANYmarine and
C:/Github/ANYhydro; no ANYsolver edits. PySide6 Qt Widgets, ANY3dView viewer,
MPL-2.0 owned code; Apache-2.0 upstream Fortran preserved. W5 **NO_GO**, general
numerical admission false; full W5–W10, thin-sheet and release requirements open.

## Source and Git state

- ANYhydro local main 36cd502 (primitive persistence, verified + Codex-reviewed),
  on top of published 30404e4. Not pushed.
- ANYmarine: this checkpoint/TASK/plan update on top of published 247b28c. GUI
  unchanged from verified 4cd9a6c.
- Untracked ANYhydro experimental files kept: tools/check_free_surface_components.py,
  tests/test_free_surface_components.py (six Codex-found defects, see TASK.md).

## Verified this session

Worker replay's 94 mismatches = replay-harness defect (wrong pole channel); saved
data consistent; root replay passes; core identity unchanged; four free-surface
Robin failures preserved (2.6666/2.9019 hull, 2.9248/3.1494 lid). Windows focused
tests 85 passed / 7 skipped. Evidence:
C:/ANYworkspaceWork/anymarine-evidence/w5-free-surface-repair-20261011
(replay-diagnosis.md, root-replay-primitives-01.json, codex-review-01.md,
physical-e1-primitives-01.json).

## Next action

Repair the experimental component oracle per codex-review-01.md, then attribute
the surface Robin residual among frozen Prony fit, panel/quadrature integration
and evaluation contract. Lead: kh = 34.4, residual ~3e-4 relative ≈ 4-term Prony
fit accuracy. No tolerance change, clipping or favourable subset replacement.
Linux runtime for native checks: /var/tmp/anyhydro-e1-installed-repair-20261011
(Ubuntu-24.04).
