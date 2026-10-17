# Runbook: $project_name

<!--
Skeleton for {{p.target}}/docs/runbook.md (S6.handover). Keep the ten ## headings exactly as
they are and in this order (the acceptance check counts them), and the ### Scheduled jobs
subsection under Deploy. Replace every fill hint; a filled copy keeps none. Names and
references only: setting names, secret reference names (secrets.<field>, env:NAME or
pass:<path>), never a value. Cite Microsoft Learn pages by title, never copy them.
Delete this comment.

Sources: engagement.json (hosting, auth, database, database.stage1, secrets by name,
care_plan, source.kind), app/config/settings/*.py (every env("...") name), app/<slug>/*/jobs.py,
analysis/rules.json (workflows, kept reports), docs/deploy/, docs/staging.md,
docs/stage1-runbook.md (when Stage 1 was sold), equivalence/README.md.
-->

<!-- fill: two or three sentences: who this runbook is for (the developer or MSP who runs the app), the engagement facts it is written from (hosting.provider and managed_by, auth.mode, database.primary and verify_also), and a demo-status paragraph when commercial.tier is demo. -->

## Run it locally

<!-- fill: prerequisites (uv; Python 3.13 from .python-version; PostgreSQL 16 or SQL Server 2022; system libraries for WeasyPrint: libpangoft2-1.0-0 and libharfbuzz-subset0; for SQL Server: libodbc2 and libltdl7, the driver itself comes with mssql-python), then the commands from app/: uv sync; DATABASE_URL (or PG_URL plus the default database name); migrate; import_dataset of synthetic seed 1; create_demo_users with DEMO_PASSWORD; runserver; the tests (pytest --create-db, playwright install chromium, --ignore=tests/e2e on SQL Server); ruff. -->

## Configuration and secrets

<!-- fill: one table row per setting the app reads from the environment (grep env(...) and os.environ in config/ and <slug>/): name, needed in production or not, what it is, and what fills it: "plain setting" or the engagement secret reference (secrets.production_db_url_ref for DATABASE_URL, secrets.entra.client_secret_ref for DJANGO_MICROSOFT_CLIENT_SECRET; say "not set" for a null reference and "no slot in the engagement" for a secret without one, such as DJANGO_SECRET_KEY). Then the development and test variables that must never be set in production (PG_URL, MSSQL_URL, MP_ONLY_AREAS, MP_DB_SUFFIX, MP_TEST_DB_PREFIX, E2E_*, EQUIVALENCE_*). Rule: values live only in the host's settings or secret store. -->

## Deploy

<!-- fill: the deploy steps for hosting.provider, short, linking docs/deploy/README.md for the detail: build (uv sync --locked --no-dev), check --deploy, migrate, collectstatic, start command (gunicorn config.wsgi:application), health check /health/, first admin user. azure-app-service: cite "Deploy a Python (Django, Flask, or FastAPI) web app to Azure App Service" and "Configure a Python app for Azure App Service" by title. Say who runs it (hosting.managed_by). -->

### Scheduled jobs

<!-- fill: one table row per JOBS entry of app/<slug>/*/jobs.py: command (exactly as in jobs.py), schedule (cron syntax as in jobs.py), rule id, what it does in one line. Then how to run it on hosting.provider: azure-app-service: a WebJob; render: a cron job; fly: a scheduled machine; digitalocean: a job; none: a systemd timer example (unit and timer files). When no jobs.py exists: "This app has no scheduled jobs." -->

## Go-live and smoke checklist

<!-- fill: Access kinds: in database.stage1.freeze_window the client saves a fresh copy at database.stage1.fresh_copy; in a plain terminal `mp-data stage1 <records>/engagement.json --target-db secret:production_db_url_ref --dataset fresh-copy`, then the same with `--verify --require-rows --report <records>/golive-report.md`; then, with DATABASE_URL from secrets.production_db_url_ref, `manage.py migrate --fake-initial` and `manage.py migrate`; when Stage 1 was sold and its database is the production database, skip the load and run only the two migrate lines; then the parallel period (database.stage1.rollback) with Access read-only or relinked, ending on the agreed date. xlsm: the go-live load is agreed with the client (proposal section 12). Write "<records>" as the expanded records path. Then the smoke checklist: one checkbox item per analysis/rules.json workflow (id, title, what to click and what must show) and one per kept report (name, how to open it, PDF check). -->

## Backups and restore drill

<!-- fill: the host's managed database backups (what to turn on, retention), plus `manage.py dbbackup` and `manage.py dbrestore` (django-dbbackup; DJANGO_DBBACKUP_DIR; PostgreSQL uses pg_dump, which the host needs; other engines fall back to dumpdata/loaddata JSON) for a portable copy; the restore drill: restore into a scratch database, point a staging copy at it, run the smoke checklist, record the date and result (records log.md); how often (care_plan). -->

## Users and roles

<!-- fill: the roles from app/<slug>/core/local/roles.py (name, label, what it may do, the rules it mirrors); groups are synced on every migrate; how to add a user in the admin and give a role; MFA with allauth (TOTP, recovery codes) and whether it is required; registration closed (DJANGO_ACCOUNT_ALLOW_REGISTRATION); create_demo_users is for demos only; any link between users and legacy records (for example employee e-mail). -->

## Microsoft sign-in

<!-- fill: auth.mode. microsoft-entra: the allauth Microsoft provider; app registration (cite "Quickstart: Register an application with the Microsoft identity platform" by title); redirect URI https://<host>/accounts/microsoft/login/callback/; DJANGO_MICROSOFT_CLIENT_ID, DJANGO_MICROSOFT_TENANT_ID, DJANGO_MICROSOFT_CLIENT_SECRET (by reference secrets.entra.client_secret_ref); the secret's expiry (secrets.entra.secret_expires) and how to rotate it; the groups claim and DJANGO_ENTRA_GROUP_ROLES from auth.groups_to_roles. local: say Microsoft sign-in is off and how to turn it on later with the same steps. -->

## Updates and Django LTS

<!-- fill: Django 5.2 LTS: apply its security releases (pin bump in pyproject.toml, uv lock, tests, deploy); end of support April 2028; plan the move to Django 6.2 LTS after April 2027; other packages (uv lock --upgrade-package, read release notes, run the suite and the equivalence harness); pipeline.json uv_exclude_newer keeps the lock reproducible; Python 3.13 support window; who does it (care_plan or client). -->

## Equivalence checks

<!-- fill: the vendored harness in ../equivalence/ on the synthetic seeds (link equivalence/README.md; the command for one cell; datasets seed-1..3 x databases); the engagement dataset runs only on our side; when to re-run (after every change to a kept query or report); what recorded the goldens, as the report's Method names it, and the kept units flagged needs_access_confirmation without a confirmation run; the current figures with a link to docs/equivalence-report.md. -->

## Troubleshooting

<!-- fill: a table of symptom, cause and fix: ImproperlyConfigured about the database (DATABASE_URL unset); OSError about libpangoft2 or libgobject (WeasyPrint system libraries); libodbc.so.2 or libltdl.so.7 not found (SQL Server); 400 Bad Request (DJANGO_ALLOWED_HOSTS); CSRF failure behind a proxy (DJANGO_CSRF_TRUSTED_ORIGINS, X-Forwarded-Proto); static files 404 (collectstatic); sign-in loop (e-mail not verified); a role missing its permissions (run migrate to resync roles); /health/ not 200; e2e browser missing (playwright install). -->
