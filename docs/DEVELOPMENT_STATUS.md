# Development status in the application (public route)

ANYmarine ships a **dated development status snapshot** so application users
can see which features and examples are available and what the latest
hydrodynamic development state is, without access to developer
evidence directories.

This is a development snapshot, **not** a dynamic qualification claim and not
a project result or assessment. It never creates a `ResultRecord` and never
marks failed evidence as completed.

## Where to see it

- **Startup**: after `run_gui.py` startup the status bar shows a compact
  permanent line, e.g.
  `Development snapshot 2026-10-10: 12 loadable example/feature routes,
  hydrodynamics unqualified (W5 NO_GO) - details under Help > Development status`.
- **Help > Development status...**: opens a dialog with
  - a capability table (model-view, read-only): Area, Feature, Status
    (`available`, `development`, `unsupported`), Menu path and Note;
  - the hydrodynamic development status text;
  - provenance identifiers for the underlying source reports.

## Data and loader

- The snapshot is the bundled, versioned `src/anymarine/development_status.json`
  (`format: anymarine-development-status-v1`, `schema_version: 1`), so wheel
  builds contain the same data as the checkout.
- It is loaded through the **Qt-free public loader**
  `anymarine.development_status.load_development_status()`. The loader uses
  only the standard library: loading or opening the status never imports
  Capytaine, ANYsolver or any numerical backend and never runs calculations.
  Numerical libraries remain Qt-free; all Qt widgets stay on the GUI thread.
- The loader validates format, schema, finiteness of all numbers, internal
  consistency (e.g. `qualified` and `general_numerical_admission` must be
  false in a truthful snapshot) and rejects stale working absolute paths, so
  the portable GUI data carries no machine-local paths. Corrupt or missing
  data fails clearly: the startup line reports
  `Development status data unavailable (corrupt or missing)` and the Help
  dialog shows an explicit error.

## Capability statuses

| Status | Meaning |
|---|---|
| `available` | Loadable now from the GUI (examples, stored waves, viewer). All are development demonstrations, unqualified. |
| `development` | A development route exists (jobs, imports, HAMS field workflow) but is optional/unqualified and may require trusted native builds. |
| `unsupported` | Not available yet or not admitted (CAD/mesh import, qualified marine analysis, physical six-DOF source admission). |

The bundled snapshot matches the actual menus: stored synthetic replay,
native coupled-fluid/mechanics/Morison/seeded-sea/mooring/static examples,
attached-plate geometry (geometry only), calculated incident/diffraction
stored waves, stored HAMS incident/total/scattered/prescribed-motion
radiation point displays, and the optional, unqualified native HAMS field
workflow.

## Hydrodynamic status (snapshot 2026-10-10)

- The full roadmap is incomplete; broader numerical development is paused by
  user decision. W5 decision: **NO_GO**; general numerical admission: **false**.
- Latest energy diagnostic `energy-point-02`: finished with exit code 1;
  summary status `energy_point_comparison_failed_unqualified`
  (computation `completed_unqualified`); registered comparison `failed`.
  Registered predicates: added-mass block **failed**, radiation-damping block
  **failed**, mean power **failed**. Added mass 10 passed / 6 failed;
  radiation damping 8 passed / 8 failed; 4x4 coupled matrices retained.
- Mean power: candidate `+0.0008646219627273671 W`, HAMS reference
  `+0.0015879742100973749 W`, comparison **failed**. A positive candidate
  sign does not imply admission.
- Checker mock tests: 50 passed (receipt `energy-tests-receipt-energy-tests-08`,
  exit code 0; mock native only, no BEM solver case execution, not
  qualification).
- Independent stored-array audit: **COMPLETE**, 100/100 consistency checks
  passed; it reproduces all three failed predicates. This is consistency
  checking, **not physical acceptance**. The audit owner timed out before the
  final handoff; no unfinished extra diagnostic ran.
- Next development question: reconcile the reference case, lid/projection
  treatment and energy accounting before a new numerical attempt. No
  speculative cause is established.

## Provenance

The snapshot records source identifiers, not raw files. The raw evidence
files do **not** ship with the application:

- `anyhydro-w5-e1-energy-check-20261010-energy-point-02-summary`
  (format `anyhydro-e1-energy-check-controls-v1`, status
  `energy_point_comparison_failed_unqualified`, case hash
  `4c5309232d7e549b548ba3274ee5edfd17351f5b5ac4edc4fb06c9b2f68c214b`)
- `anyhydro-w5-e1-energy-check-20261010-audit02-run-02`
  (format `anyhydro-audit02-independent-energy-audit-v1`, audit state
  `COMPLETE`, audit script sha256
  `6139d28d3ea9ea065964ce057d24243b7cd4d6afeac9da592b9b32b7d23b1b66`,
  executed 2026-10-10T17:02:12.767816+00:00)

The root publication snapshot preserves compact source hashes and results
alongside this document. No physics, sign, tolerance or authority change is
made by publishing this status route.
