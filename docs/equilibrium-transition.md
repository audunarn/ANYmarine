# Static equilibrium adoption (development)

`ApplicationService.adopt_equilibrium` returns a new project from a complete,
current `equilibrium_development` result. It never writes the active project.
Use the existing explicit save operation after accepting the returned project.
The application service is headless; importing its records does not require Qt
or an optional BEM backend.

```python
from anymarine.services import ApplicationService
from anymarine.equilibrium_transition import AdoptionDomain, EquilibriumRegeneration

service = ApplicationService()
adopted = service.adopt_equilibrium(
    project, static_result,
    domain=AdoptionDomain(water_depth=10.0, waterline_z=0.0),
    regenerate=regenerate_development_dependencies,
    should_cancel=cancellation_requested,
)
service.save_current(adopted, project_directory)
```

The caller supplies a regenerator; the example names above are caller-owned
functions. There is no automatic physical hydrodynamic regeneration in this API.
The callback receives a detached candidate and immutable request. It returns only
`EquilibriumRegeneration(potential_flow=..., lids=...)`, with the exact original
dependency inventory. It has no authority to save, launch a backend, or change
mechanical material, loads, attachments, geometry or constitutive data.

Final body positions and rotation matrices become the new mechanical reference.
The actual final line nodes are retained. Rates reset to zero; each new native
run starts fresh history under the solver's existing prehistory contract.
Body-local mesh, centre of gravity, inertia, link lengths, line material and
prestrain remain unchanged. Numerical lids and potential-flow frames,
coefficients, kernel and excitation must be explicitly regenerated. Their old
bindings are removed from the callback candidate.

The transition admits only the declared fully submerged finite-depth domain.
It rechecks transformed body vertices, member endpoints, line centres, waterline,
environment and contact bed; it never clamps wetting or penetration. Attached
thin sheets and shared-wave adoption are explicitly unsupported. Shared wave
realizations, excitation and Morison components are never silently dropped.
Production hydrodynamic sources remain unadmitted by the native route.

Before returning, the service checks public stationary force and moment balance
at the original result's stored tolerances. It also checks continuity of every
physical force component across the coordinate change. Reference buoyancy and
fluid preload must survive regeneration; gravity, links and lines cannot absorb
a lost hydrostatic baseline. A structurally valid coefficient binding alone is
insufficient. Cancellation, stale inputs, callback failure, incompatible data and
failed balance leave the original project unchanged.

An immutable receipt records parent/result identity, geometry, regenerated
inputs, the domain and the balance audit. Native compilation revalidates both
bindings and domain. Editing a body, fluid data or waterline requires fresh
regeneration and audit. Changing only run duration does not change equilibrium.
Schema 9 preserves reference rotations and receipts, including those inside
retained producing snapshots, and refuses downgrade. Original result arrays and
their producing snapshots remain authoritative and become stale for new inputs.

ANY3dView receives the actual reference rotation and translation. Stored native
replay uses authoritative matrices. The legacy synthetic Euler replay refuses
rotated references; translated-only hydrodynamic import/linear consumers likewise
refuse unsupported rotated bindings.

The sealed fixture under `tests/fixtures/equilibrium-transition` records an actual
fully submerged synthetic-fluid static solve, successful adoption, save/reopen,
three native steps and stored-array replay. It is **unqualified development
evidence**, not a physical BEM or full demonstrator acceptance. The regenerated
fixture defines new synthetic data; it does not relabel physical coefficients.
Broader wetting/contact, physical regeneration, physical regeneration and the
remaining W5/W7/W8–W10 gates stay open. Failed attempts are preserved under
`C:/ANYworkspaceWork/anymarine-evidence/equilibrium-transition-20261010`.

## Qt workflow

Select a complete current static equilibrium result in the project tree, then
choose **Jobs > Adopt selected static equilibrium…**. Enter the declared fully
submerged water depth and waterline. Supply a regenerated-dependencies JSON file
when the original project has fluid data or numerical lids, then choose **Queue
adoption**. The Jobs/Diagnostics panel shows progress and any refusal.

The artifact has exactly `potential_flow` and `lids`: the former is a public
`NativePotentialFlowInputs.to_dict()` record (or null for a dry project); the
latter maps body IDs to public body-local `BodyMesh.to_dict()` lid records.
Unknown fields, malformed arrays and boolean coordinates/indices are refused.
The exact original fluid/lid inventory is required. An old frame/baseline or
unadmitted physical source is refused in the worker; the GUI does not synthesize
or relabel physical data. Physical regeneration remains a separate source gate.

The worker receives captured project, result, domain and regenerated data, not
live file paths or widgets. It rechecks identity, audits separate physical forces
at the stored original tolerances and returns a sealed project artifact. Rendering
and transport admission remain on the GUI thread; no solver audit runs there.
Only a validated success can replace an unchanged project. Any intervening edit
(including display metadata or result catalog changes) preserves the current
project and detaches the returned output, whose location remains in diagnostics.

Adoption displays the new reference geometry and clears active replay. Earlier
result arrays/producing geometry remain stored and stale; selecting them replays
their original model. **Save** persists the new initial state. Native dynamics
remains a separate explicit job. Cancellation, deadline, worker crash and closure
keep the original project and full logs. A candidate written before late
cancellation remains incomplete evidence and receives no success seal.

The installed Windows check verified automatic GPU startup, reference transforms,
line nodes and stored replay through public ANY3dView APIs; software replay was
also checked. This does not establish scientific qualification or Linux delivery.
