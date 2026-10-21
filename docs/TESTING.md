# Running the tests (ANYmarine)

Use the interpreter that has PySide6 and `any3dview` (here `py -3.14`) with the sibling `anyhydro` `src` on
`PYTHONPATH` (the `test_hydro_*` modules import it). One command per tier, from the repository root:

```
py -3.14 tools/dev_checks.py                 # fast tier: tests not recorded as slow; about 90 s here
py -3.14 tools/dev_checks.py --tier full     # every test; about 6 min here (one 330 s test sets the floor)
```

- Each test file runs in its own pytest process (a big file is split by test id), 8 at a time, each with its own
  `--basetemp` and a hard timeout. A Qt crash or hang therefore stays inside its job: the run reports those tests
  as `MISSING` and exits 1 instead of dying or hanging.
- Tiers come from `reports/ci/durations.json`: a test at or above 10 s is `slow` (set by `tests/conftest.py`);
  `slow_files` lists whole files kept out of the fast tier. Today that is `tests/test_native_member_authoring_qt.py`,
  whose `test_production_handler_adds_member_updates_project_and_preview` ends the interpreter with a Windows access
  violation and then hangs pytest. It still runs in the full tier and fails visibly there. `python tools/dev_checks.py --verify`
  checks that the fast and slow tiers together are exactly the full selection.
- Refresh the table: `py -3.14 tools/dev_checks.py --tier full --update-durations`
  (needs a clean run, or `--expect-failures FILE` naming the ids that fail on this machine; a test outside
  that list that fails still fails the run).
- Options: `--jobs N`, `--shard k/N`, `--timeout S`, `--keep`, `--workdir DIR`, `--write-failures FILE`.
- Limit: `--workdir` deletion checks path and marker but is not race-free against a concurrent writer of the same path.
- The ANYmarine GitHub CI runs six headless files (`.github/workflows/ci.yml`, under a minute); this tooling is
  for the local full suite.
- Known limit: a timeout kills the job's whole process tree. On Windows a process that a finished job left running
  in the background is not killed (no process group to signal; a Job Object would be needed); on Linux it is.
