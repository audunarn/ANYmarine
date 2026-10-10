# Retained W5 prospective workflow

`anymarine.w5_workflow` connects the existing public ANYhydro source-study and
radiation services to ANYmarine's derived import, linear execution, source-bound
assessment and paired snapshot services. It owns no BEM, mechanical integrator,
radiation convolution or replacement marine model. Only the original two-body
surge/heave slice is admitted by this route; full-six diagnostics are separate.

Create an explicit JSON specification, then use the shared service or CLI:

```python
from anymarine.services import ApplicationService
service = ApplicationService()
service.prepare_w5_case(specification, fresh_output_directory)
```

```text
python -m anymarine.w5_workflow prepare --spec case-spec.json --out fresh-evidence
python -m anymarine.w5_workflow inspect --out fresh-evidence
```

The closed specification declares a public `SourceStudyCase`, coarse/fine
`RadiationSettings`, `ReconstructionCriteria`, nested response grids and their
explicit execution limits, link stiffness, free-decay initial states, regular
waves and adjacent fit windows, existing assessment controls, and the named
frequency/tail/horizon/A_inf/correction/refinement declarations. No scientific
defaults are supplied. Criteria are checked by the same public owner as ordinary
assessment preparation, before expensive source work. Both reconstruction
matrices must pass; a damping-only reconstruction is never sufficient.

Preparation validates the entire case before creating an exclusive output,
freezes its canonical content hash, and calls `prepare_source_study`. It does
not instantiate or execute a backend. Source preparation failure keeps the
registered evidence with a failed status. Existing outputs are never overwritten.

Execution is explicit:

```python
service.execute_w5_case(output_directory, backend=explicit_backend,
                        execution_limits=explicit_backend_limits,
                        should_cancel=cancel_callback)
```

Execute only for a named prospective numerical allowance in an isolated,
process-tree-bounded supervisor. Qt callers must launch that headless route in a
child process; this synchronous service is not a GUI-thread numerical worker.
Preparation, a case file, or unused seconds in an old pool grant no run authority.
The workflow makes one source-study call, re-adjudicates its raw evidence, and
stops before response work on source failure. It then builds both kernels, imports
the exact first-slice hull/lid geometry, saves ALL source-bound cases BEFORE
any response solve, and preserves results/reports and paired snapshots.
Cancellation/crashes/NoGo keep partial evidence; only a fresh prepared record
can execute, with no automatic retry or silent retuning.

`directional-damping.json` reports changes in the fixed finest retained damping
eigenbasis with its full projected matrices. Degenerate subspaces may rotate;
this is reporting, not a new acceptance tolerance or authenticated error bound.
Regular-wave source assessments retain the existing measured reciprocity
correction sensitivity checks. Raw backend arrays and historical reports remain
unchanged. No new full-six passivity policy is adopted.

`checks_completed` means the declared DEVELOPMENT checks ran successfully.
It is not scientific qualification or W5 acceptance. Frequency-band/tail
adequacy, off-diagonal radiation force observability in actual stored response
channels, and independent scientific assessment remain explicitly open even in
that terminal state. Declarations, three pilot frequencies, mocked tests, a
same-coefficient TD/FD match and a passed global-band mesh metric do not prove
those gates. Prospective extended-band/horizon sensitivity evidence must still
be supplied and independently adjudicated before qualifying the first slice.
Nonzero cross-body source coefficient blocks do not themselves demonstrate
nonzero cross-body radiation forces for the selected response trajectory.
The original free-decay energy, RAO <3%/<3 degrees, off-diagonal force and explicit
zero/finite/infinite treatment requirements are unchanged.
