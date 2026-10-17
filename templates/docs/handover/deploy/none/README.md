# Deploy: $project_name on your own Linux host

<!--
Skeleton for {{p.target}}/docs/deploy/README.md when engagement hosting.provider is "none"
(S6.handover): a generic production checklist for one Linux server, with no provider files.
Keep the ## headings. Replace every fill hint; a filled copy keeps none. Setting names only,
never values. Delete this comment.
-->

<!-- fill: one paragraph: hosting.provider none means a Linux host the client or its MSP runs (hosting.managed_by says who); gunicorn behind a reverse proxy that ends TLS; one database server; link docs/runbook.md for configuration, go-live, backups and jobs. Demo: say no host is set up. -->

## The host

<!-- fill: a table: operating system; Python 3.13 through uv; system libraries (WeasyPrint: libpangoft2-1.0-0, libharfbuzz-subset0; SQL Server: libodbc2, libltdl7; PostgreSQL backups: postgresql-client matching the server for pg_dump); the database (PostgreSQL 16 or SQL Server 2022 / Azure SQL; created empty by the client's DBA); reverse proxy (nginx or Caddy, X-Forwarded-Proto); service manager (systemd); a service account. -->

## Production checklist

<!-- fill: a checkbox list, in order: code checked out at the release tag; `uv sync --locked --no-dev`; environment file readable by the service account only, filled from the names in the runbook's Configuration and secrets section; DJANGO_SETTINGS_MODULE=config.settings.production; `manage.py check --deploy` clean; `migrate --noinput`; `collectstatic --noinput`; first admin user; gunicorn under systemd (unit example); reverse proxy with HTTPS and HSTS; /health/ answers 200; scheduled jobs installed (runbook, Scheduled jobs); database backups on and one restore drill done; logs collected; DEBUG off; registration closed; MFA for admins. -->

## systemd service

<!-- fill: an example unit for gunicorn: WorkingDirectory=<checkout>/<target.root>/app, EnvironmentFile=<the service's environment file>, ExecStart=<uv> run --no-sync gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3, User=<service account>, Restart=on-failure. -->

## Updating

<!-- fill: back up (`manage.py dbbackup`), check out the new release, repeat uv sync, check --deploy, migrate, collectstatic, restart the service, check /health/ and run the smoke checklist. -->
