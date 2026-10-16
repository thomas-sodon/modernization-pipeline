# modernization-pipeline

Tools, templates and the stage driver that turn an MS Access or Excel VBA app into a Django app
that is proven to behave the same. Read docs/proposal.md and docs/runbook.md; read
docs/contracts.md before changing any file format, CLI or path rule.

- Each stage is one run of .claude/workflows/run-stage.js for one engagement. Workflow tasks never commit.
- Private engagement data lives in $HOME/engagements/<id>/private and is never committed. Zones (decisions L2):
  - design zone, which you may read: private/out/, private/review/ and private/synthetic/;
  - data zone: sources/, datasets/, workbook/, reference/, goldens/, corpus/, results/, logs/, assets/
    and scrub-map.json. Only mp-data touches it: never open, list, cat, query or copy it.
- A data-plane call is one standalone `mp-data ...` command with no shell around it (no pipe, &&, ;,
  redirection, substitution, cd or env prefix). Code it runs (the Django app under equivalence,
  app-function rewrites) can write only into the data zone, TMPDIR and the call's declared outputs.
- Debug on synthetic seeds with `uv run toolkit ...` (design zone and committed files only).
- Client sessions allow only `mp-run --eng <eng> --slot <slot> -- '<command>'` and mp-data Bash calls.
- The env file $HOME/.config/modernization/env (PATH, UV_PYTHON_PREFERENCE, JAVA_HOME, PG_URL and
  MSSQL_URL for the mp_agent role, LD_LIBRARY_PATH, PLAYWRIGHT_BROWSERS_PATH) holds no secrets.
  $HOME/.config/modernization/data.env holds the secrets: never read it.
- Engagement rows live only in mpdata_* databases, which PG_URL and MSSQL_URL (mp_agent) cannot open.
  Docker containers: mp-pg16 (127.0.0.1:55432) and mp-mssql22 (127.0.0.1:14330).
- Use uv only (Python 3.13, uv-managed); there is no sudo and no pip into the system Python.
- Your shell resets between calls: start every command with cd <absolute path> (in client sessions,
  inside the mp-run quotes), or use the command form your prompt gives.
- Every toolkit command uses toolkit.engagement.load and toolkit.quiet and passes
  toolkit/tests/test_quiet.py (one JSON summary line under MP_QUIET=1).
- Secrets are referenced (env:NAME, pass:<path>), never printed.
- Never open or copy access2flow (no licence).
