# modernization-pipeline

Tools, templates and the stage driver that turn an MS Access or Excel VBA app into a Django app
that is proven to behave the same. Read docs/proposal.md, docs/contracts.md and docs/runbook.md.

- Each stage is one run of .claude/workflows/run-stage.js for one engagement. Workflow tasks never commit.
- Private engagement data lives in $HOME/engagements/<id>/private and is never committed.
  You may read only the design zone: private/out/, private/review/ and private/synthetic/.
  Everything else under private/ is the data zone: only `mp-data <command>` reads it.
- The env file is $HOME/.config/modernization/env (PATH, UV_PYTHON_PREFERENCE, JAVA_HOME, PG_URL,
  MSSQL_URL, LD_LIBRARY_PATH, PLAYWRIGHT_BROWSERS_PATH). It holds no secrets. Never print it.
  Secrets live in $HOME/.config/modernization/data.env: never read it.
- Use uv only. Python is 3.13 (uv-managed). There is no sudo.
- Your shell resets between calls: run each command in the form your prompt gives it
  (`mp-run --eng ... --slot ... -- '<command>'`, a standalone `mp-data` call, or in the first S0 the braced form).
- Database containers: mp-pg16 (127.0.0.1:55432) and mp-mssql22 (127.0.0.1:14330).
  PG_URL and MSSQL_URL log in as mp_agent, which every mpdata_ database refuses.
