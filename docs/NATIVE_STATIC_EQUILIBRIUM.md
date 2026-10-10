# Coupled static equilibrium development workflow

Choose **File > Load coupled static equilibrium example**, then **Jobs > Run
coupled static equilibrium job**. Loading creates two bodies, a linear link and
three anchored lines. The job runs the installed public ANYsolver coupled static
development API in an isolated child process supervised by QProcess. This is an
unqualified example using synthetic coupled potential-flow coefficients.

The example stores nine frames: the initial geometry, four fixed-fairlead net
weight stages and four stages releasing temporary body supports. Replay shows
continuation **stage indices**, never seconds. Each stored frame contains body
identity, position, rotation matrix and free-line-node positions. The final audit
records physical force/moment residuals, component decomposition, full release
of temporary supports and the potential-flow geometry binding. Current/contact
stages are included when the producing input requests those capabilities.

Save/reopen preserves the immutable result and its producing input snapshot.
Editing a project during a job cannot change that run. Staleness compares the
complete project, static controls and execution limits. Detached replay uses
the producing body/line geometry even if current native inputs were removed.
Cancellation and failure retain logs and accepted line/coupled stages in an
incomplete sidecar; incomplete evidence never becomes a completed result.

The Qt and headless paths use the same application services. For a saved project:

```text
python -m anymarine.cli run-equilibrium --project PATH --wall-seconds 60
```

Optional `--static-spec controls.json` supplies the explicit scales, tolerances,
release fractions and iteration/search limits. Defaults are the development
CoupledStaticSpec: force/moment scales 100, absolute tolerances 1e-8, relative
tolerance 1e-8, release fractions 0.25/0.5/0.75/1, 40 Newton iterations and 20
line-search trials per stage. The default wall limit is 60 seconds and stored
frame limit is 1001. Exact controls and execution limits are bound into the run
hash. Headless imports, persistence and transport validation do not import Qt
or execute the numerical backend.

Changed equilibrium geometry displays **coefficients require regeneration**.
The application does not adopt solved geometry or silently reuse old bound
coefficients as dynamics at that geometry. Static stages have no wave clock;
wave-preview modes require a dynamic result or cleared replay. Existing incident
and disturbance previews remain available through their existing workflows.

This workflow does not establish scientific qualification, uniqueness/stability
of a neutral mechanism, large-excursion potential-flow validity, friction or W6
contact/refinement acceptance. Those gates and historical failures remain open.
