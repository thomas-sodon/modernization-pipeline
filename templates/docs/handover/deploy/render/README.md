# Deploy: $project_name on Render

<!--
Skeleton for {{p.target}}/docs/deploy/README.md when engagement hosting.provider is "render"
(S6.handover). Copy render.yaml beside it into {{p.target}}/docs/deploy/ and fill it too;
Render reads a Blueprint from render.yaml at the repo root or at the path set in the
Blueprint, so say where it goes. Keep the ## headings. Setting names only, never values.
Replace every fill hint; a filled copy keeps none. Delete this comment.
-->

<!-- fill: one paragraph: a Render web service (Python 3.13, root directory <target.root>/app), a Render PostgreSQL database or the client's own, who runs it (hosting.managed_by), and links to render.yaml and docs/runbook.md. -->

## Blueprint

<!-- fill: how to apply render.yaml (New, Blueprint, pick the repo), what it creates, and which env vars are marked sync: false so the client enters them in the dashboard from the names in the runbook. -->

## Build and start

<!-- fill: build command (uv sync --locked --no-dev, collectstatic), pre-deploy command (migrate), start command (gunicorn), health check path /health/; WeasyPrint's system libraries on Render's native runtime, or a Docker runtime when they are missing. -->

## Scheduled jobs

<!-- fill: each JOBS entry as a Render cron job (type cron, its schedule, startCommand python manage.py <command>); a startup-only job (@reboot) goes into the pre-deploy command. -->

## Backups

<!-- fill: Render PostgreSQL backups and point-in-time recovery for the plan, plus the restore drill from docs/runbook.md. -->
