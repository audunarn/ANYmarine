# Qt assessment workflow — user guide

Development-scope engineering assessment inside the ANYmarine Qt
application. Everything here is **developmental and unqualified**: the
checks are real engineering checks executed by the public application
services in an isolated headless child process, but no run is a W5
qualification claim and the GUI never promotes flags into qualification.

## What the workflow does

1. **Register a case** (`Assessment -> Register assessment case...`):
   choose an explicit case specification JSON and an exclusive (not yet
   existing) artifact target. The case is registered against the
   project's *stored linear inputs* before any assessment; nothing is
   inferred from observed results.
2. **Assess the selected result** (`Assessment -> Assess selected
   result...`): pick the explicitly selected stored linear result and the
   registered case (artifact), plus an optional exclusive snapshot
   target. The assessment, including hydro source reproduction when the
   inputs are hydro-sourced, runs entirely in the child.
3. **Assess a coarse/fine pair** (`Assessment -> Assess coarse/fine
   pair...`): choose distinct coarse and fine project/result/case
   combinations and an exclusive paired snapshot target. Members are
   never interchangeable and replay is member-specific.
4. **Reopen a snapshot** (`Assessment -> Reopen assessment snapshot...`):
   adjudicate a saved single or paired snapshot. The child reconstructs a
   private root from the snapshot's own frozen bytes — the original
   files may have disappeared — and fully reproduces the evidence before
   anything is displayed or replayed.

## What you see

- A table of the actual engineering checks: name, value, limit, unit and
  an honest Pass / NoGo / NOTMEASURED outcome per check, with diagnostics.
- The bound identity: project name, bound input hash, source route,
  result and body identities, case identity.
- A verdict of `pass` or `nogo`. A NoGo is a *validated non-success*:
  its report is preserved and displayed, the job ends in the distinct
  `nogo` state (never `completed`, never a generic error).
- A **DETACHED/STALE** label whenever the currently open project no
  longer matches the submission — including physical edits (body mass,
  reference position, equilibrium) made while the job was queued,
  running or after the evidence is already on screen. Detached evidence
  is never presented as current.

## Guarantees behind the display

- All inputs (project, result, case, hydro sidecars, snapshot bytes) are
  frozen at submission; original files can disappear afterwards.
- The child's whole output is sealed with a canonical SHA256; the seal
  and the trusted producer digest captured from the worker stdout must
  both match before any evidence is admitted, so modified, replaced or
  partial output is refused.
- Assessment evidence is never a new numerical result: nothing is added
  to the project's results and stored time series are never replaced.
- Cancellation, crashes and late output never become completed; logs and
  artifact references are preserved for every refusal.

## Preparing a project for the workflow

Run the example to create a real stored project with a registered case
(analytic rehearsal inputs, development scope only):

```
python examples/qt_assessment_workflow.py --out <folder>
```

Then open `<folder>/project` in the application
(`python -m anymarine.qtapp`) and use the Assessment menu; the
registered case artifact is `<folder>/registered-case.json`.
