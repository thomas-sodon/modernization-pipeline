# Deploy: $project_name on Azure App Service

<!--
Skeleton for {{p.target}}/docs/deploy/README.md when engagement hosting.provider is
"azure-app-service" (S6.handover). Copy startup.sh and app-settings.md beside it into
{{p.target}}/docs/deploy/ and fill them too. Keep the ## headings. Cite Microsoft Learn
pages by title ("Deploy a Python (Django, Flask, or FastAPI) web app to Azure App Service",
"Configure a Python app for Azure App Service", "Run background tasks with WebJobs in Azure
App Service"); never copy them. Setting names only, never values. Replace every fill hint;
a filled copy keeps none. Delete this comment.
-->

<!-- fill: one paragraph: a Linux App Service plan running Python 3.13, the database (Azure Database for PostgreSQL flexible server or Azure SQL Database), who runs it (hosting.managed_by), and links to startup.sh, app-settings.md and docs/runbook.md. -->

## Resources

<!-- fill: a table: resource group, App Service plan (Linux, SKU), Web App (runtime Python 3.13), database server and database, Key Vault for secrets (Key Vault references in app settings), Log Analytics or App Service logs, custom domain and managed certificate; who owns each (client). -->

## Build and start

<!-- fill: how the code gets there (GitHub Actions or zip deploy from <target.root>/app); App Service's build (Oryx) installs from requirements.txt, so the deploy step writes it with `uv export --frozen --no-dev --no-hashes --no-emit-project > requirements.txt`; SCM_DO_BUILD_DURING_DEPLOYMENT; the startup command is startup.sh (migrate, collectstatic, gunicorn on the port App Service gives); WeasyPrint's Pango libraries: install them in startup.sh when the image lacks them, or use a custom container. -->

## Settings

<!-- fill: link app-settings.md; secrets as Key Vault references named after the engagement's secret references; the health check path /health/ in the Web App's Health check blade. -->

## Scheduled jobs

<!-- fill: each JOBS entry as a WebJob (triggered with a CRON expression in settings.job, or continuous), running `python manage.py <command>` with the app's settings; a startup-only job (schedule @reboot) runs from startup.sh instead. -->

## Backups

<!-- fill: the database's automated backups (retention days, point-in-time restore), App Service backups if wanted, and the restore drill from docs/runbook.md. -->
