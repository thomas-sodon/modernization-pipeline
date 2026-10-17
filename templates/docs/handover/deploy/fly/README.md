# Deploy: $project_name on Fly.io

<!--
Skeleton for {{p.target}}/docs/deploy/README.md when engagement hosting.provider is "fly"
(S6.handover). Copy fly.toml beside it into {{p.target}}/docs/deploy/ and fill it too; it
must stay valid TOML (the acceptance check parses it with tomllib). Keep the ## headings.
Setting names only, never values. Replace every fill hint; a filled copy keeps none.
Delete this comment.
-->

<!-- fill: one paragraph: a Fly app built from a Dockerfile in <target.root>/app (Python 3.13 slim with WeasyPrint's and ODBC's libraries), Fly Postgres or the client's own database, who runs it (hosting.managed_by), and links to fly.toml and docs/runbook.md. -->

## Launch

<!-- fill: `fly launch --no-deploy --copy-config --config docs/deploy/fly.toml` from <target.root>/app, `fly secrets set` for each secret by name (values typed by the client, never in a file), `fly deploy`; the release command runs migrate. -->

## Settings

<!-- fill: non-secret settings in [env] of fly.toml; secrets with `fly secrets set NAME=...` from the names in the runbook's Configuration and secrets section. -->

## Scheduled jobs

<!-- fill: each JOBS entry as a scheduled Machine (`fly machine run <image> --schedule <hourly|daily|weekly|monthly> python manage.py <command>`); a cron schedule finer than that needs a small supercronic process instead; a startup-only job (@reboot) goes into the release command. -->

## Backups

<!-- fill: the database's snapshots (Fly Postgres volume snapshots or the managed provider's backups), plus the restore drill from docs/runbook.md. -->
