# Deploy: $project_name on DigitalOcean App Platform

<!--
Skeleton for {{p.target}}/docs/deploy/README.md when engagement hosting.provider is
"digitalocean" (S6.handover). Copy app.yaml beside it into {{p.target}}/docs/deploy/ and
fill it too (an App Platform app spec). Keep the ## headings. Setting names only, never
values. Replace every fill hint; a filled copy keeps none. Delete this comment.
-->

<!-- fill: one paragraph: an App Platform app with a web service from <target.root>/app (Python buildpack or a Dockerfile with WeasyPrint's and ODBC's libraries), a managed PostgreSQL database or the client's own, who runs it (hosting.managed_by), and links to app.yaml and docs/runbook.md. -->

## Create the app

<!-- fill: `doctl apps create --spec docs/deploy/app.yaml` (or the control panel's import of the spec); secrets as encrypted environment variables (type SECRET) entered by the client from the names in the runbook; `doctl apps update` for later spec changes. -->

## Build and start

<!-- fill: build command, run command (gunicorn), a PRE_DEPLOY job that runs migrate, the health check path /health/. -->

## Scheduled jobs

<!-- fill: each JOBS entry as an App Platform job (a scheduled job with its cron schedule where the platform offers it, else a POST_DEPLOY job or an external scheduler); a startup-only job (@reboot) runs as a POST_DEPLOY job. -->

## Backups

<!-- fill: the managed database's daily backups and point-in-time restore, plus the restore drill from docs/runbook.md. -->
