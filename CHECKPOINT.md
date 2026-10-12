# ANYmarine restart checkpoint — ACTIVE (Claude), 2026-10-12

Development resumed by the user and handed to Claude. Codex is the independent
reviewer (GPT 6.1 Sol high default; GPT 6 Astra high for very complex reviews).
Goal and governing roadmap: [Implementation plan](docs/IMPLEMENTATION_PLAN.md);
living record: [TASK.md](TASK.md). Repositories C:/Github/ANYmarine and
C:/Github/ANYhydro; no ANYsolver edits. PySide6 Qt Widgets, ANY3dView viewer,
MPL-2.0 owned code; Apache-2.0 upstream Fortran preserved. W5 **NO_GO**, general
numerical admission false; full W5–W10, thin-sheet and release requirements open.

## Source and Git state

- ANYhydro main dc319ff (calibration-step A_inf), a81cb34 (declaration verifier), pushed.
- ANYmarine main 0da13f9 pushed (shared A_inf W5 rule); 2d84b14 (R5 protocol) pushed;
  RAO slices 1-6 local commits c98d6c2..HEAD, not pushed (review pending).
- Superseded worker component oracle archived (not in repo):
  C:/ANYworkspaceWork/anymarine-evidence/w5-free-surface-repair-20261011/superseded-component-oracle.

## Verified

Free-surface failure attributed and repaired; see TASK.md top section and
C:/ANYworkspaceWork/anymarine-evidence/w5-prony-attribution-20261012/ATTRIBUTION.md.

## Next action

1. W5 v5 (evidence w5-physical-20261013b) running: on completion read the gates, record, then
   reinstall ANYhydro dc319ff into the evidence runtime (INSTALL.md pattern). W5 NO_GO.
2. R5 passed under the adopted protocol (w5-r5-hams-uncertainty-20261012); independent review.
3. RAO reporting: review slices 1-6, then mooring (7), drag (8), GUI (9), docs (10); owner
   adoption of the proposed RAO limits. See TASK.md top and docs/RAO_REPORTING.md.
Tests on Windows: runtime python with PYTHONPATH=C:/Github/ANYhydro/src;C:/Github/ANYmarine/src
and a writable --basetemp. Linux native runtime: /var/tmp/anyhydro-e1-installed-repair-20261011.
