# Derived-operator hydrodynamic import (development, unqualified)

Status: artifact development only. Nothing here is scientific qualification;
every result produced through this route is labelled linear_development and
unqualified. The raw 96-panel pilot stays refused. The legacy raw import route
(`import_hydro_inputs` / `import-hydro`) is unchanged; the derived route is a
separate explicit admission, never a fallback or an averaging repair of it.

## Route

- Library: `anymarine.hydro.import_derived_hydro_inputs(project,
  dataset_path, operators_path, kernel_path, options)`.
- Service: `ApplicationService.import_derived_hydro_inputs(...)` copies the
  exact raw bytes of all three sources into the project as content-addressed
  sidecars (`hydro/<sha256>.nc`, `.npz`, `.npz`-typed operators, `.nc` kernel)
  before the provenance is frozen.
- CLI: `python -m anymarine import-derived-hydro --project --dataset
  --operators --kernel --options`.
- Qt: Jobs menu action "Import derived hydrodynamic inputs..." (four file
  prompts) calling the same service; the legacy import-hydro action is
  untouched.

## Admission (shared single path)

The import and every later re-verification run the same code path
(`_load_derived_triple` + `_assemble_derived_inputs` in `anymarine/hydro.py`);
no hydrodynamic arithmetic is cloned in the GUI, CLI or worker:

1. Exact raw bytes of dataset/operators/kernel are captured BEFORE decode,
   hashed (raw sha256), decoded on private byte-identical copies, and the
   original files are re-checked before the import returns.
2. The derived artifact is loaded through the public ANYhydro
   `load_derived_operators(..., source_dataset=raw)` (full rederivation
   proof, never hash-only); `artifact.admitted` is required.
3. Retained DOF keys must equal the exact project body order
   surge/heave keys; hull/lid/reference geometry proof shared with the raw
   route.
4. Kernel binding: derived content hash, raw dataset hash, DOF/body order,
   and `kernel.added_mass_infinite == artifact.derived_added_mass_infinite`
   exactly; all symmetry/passivity diagnostic counts zero; BOTH A+B
   reconstruction recomputed through the public comparator with the raw
   source supplied; kernel covers n_steps (no truncation or interpolation).
5. Effective SI mechanical arrays: admitted derived Ainf, projected
   unmodified hydrostatic stiffness, dry diagonal body masses, optional
   explicit surge link, zero extra frequency-domain damping. Regular-wave
   forcing from the exact stored projected complex excitation with the raw
   route's negative-exponent/time/phase convention.

## Source ownership and verification

Metadata mirrors and bookkeeping hashes alone are not source ownership.
`ApplicationService.verify_hydro_provenance` (called on load, import, run
and snapshot) additionally recomplies the effective inputs from the stored
authoritative sidecars through the public ANYhydro source proof
(`anymarine.hydro.recompile_derived_inputs`) under the record's declared
controls (criteria, wave, surge link, grid, initial state, execution limits)
and requires the recompiled full input hash to equal the record's stored
hash. Changing an effective array and re-hashing
`HydroBridgeProvenance.linear_input_hash` is therefore refused: the dry
mass is bound to the current bodies, and Ainf/H/kernel/forces are bound to
the admitted raw/operator/kernel sources and declared controls.

The frozen derived bundle (additive optional field on
`HydroBridgeProvenance`, omitted when None so legacy records keep their
exact serialized identities) carries the artifact identity, raw operators
sha256, sidecar reference, complete explicit settings and report, and the
declared q0/v0/execution limits. Roles registry: exactly 2 roles
(dataset, kernel) for legacy projects, exactly 3 (+ operators) for derived
projects; missing/corrupt/traversal sidecars refuse the load. Save-as
copies all three artifacts self-contained; the original source files may
disappear after a successful import.

## Detached job snapshots

`linear_job_snapshot` embeds the exact raw bytes of the three sidecars
(base64, sha256-checked) alongside the materialised inputs and the frozen
provenance. The worker (`anymarine.worker`) recompiles the effective
inputs from the embedded bytes through the same public source proof and
refuses the job (exit 2, no output) unless they reproduce the declared
input hash. A forged effective array with re-hashed snapshot bookkeeping
does not execute, even after the originals and the saved sidecars
disappear. Snapshots without a derived bundle (legacy raw and analytic
jobs) keep their existing contracts.

Derived intent is claimed on both sides of the snapshot and either claim
requires the complete intent: the inputs' own provenance details
(`route == "derived_operators"`) and the structured hydro provenance's
frozen derived bundle. Stripping one side (the whole `hydro_provenance`
record or just its `derived_bundle`) to bypass the source proof is
refused, and so is an inconsistent claim (bundle without the input-owned
route or vice versa). Before anything is decoded, the worker validates the
structured record as a frozen `HydroBridgeProvenance` and hashes the
actual embedded dataset/operators/kernel payload bytes against the
record's declared `*_raw_sha256` identities; declared bookkeeping alone is
never accepted as source proof. There is no automatic fallback, and
explicitly supplied non-hydro legacy inputs keep working unchanged.

## Tests

`tests/test_derived_import.py` (analytic 12-DOF fixture with a tiny
retained asymmetric damping perturbation that the raw route refuses and
the declared reciprocal-average policy admits): admission and refusal
checks (foreign raw source, forged operators bytes, wrong retained order,
foreign kernel, non-admitted artifact, criteria failure), exact SI
arrays/phasor/link, provenance bundle freezing and legacy omission,
stripped/forged evidence, effective-operator tamper not cured by rehash,
new-artifact staleness, snapshot evidence and detached-worker forgery
refusal, detached-worker stripped derived intent (whole provenance record,
derived bundle, source payloads) and inconsistent raw byte identity
refusals with an unmodified-snapshot positive control, roles/save-as/deleting originals/corrupt/traversal sidecars,
result force balance and replay round trip, actual CLI import+run, actual
Qt action -> service -> JobManager -> child -> replay, and one explicit
1002-frame derived child job through the shared snapshot/execution
limits. All results remain unqualified linear_development.
