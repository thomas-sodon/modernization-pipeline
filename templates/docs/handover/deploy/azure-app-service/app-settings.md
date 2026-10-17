# App settings: $project_name on Azure App Service

<!--
Skeleton for {{p.target}}/docs/deploy/app-settings.md (S6.handover, hosting.provider
azure-app-service). Keep the table header. One row per setting of the runbook's
Configuration and secrets section, plus the App Service settings below. Names and
references only, never values. Replace every fill hint; a filled copy keeps none.
Delete this comment.
-->

<!-- fill: one paragraph: enter these in the Web App's Environment variables (App settings); secrets as Key Vault references (@Microsoft.KeyVault(SecretUri=...)) to the secret named after the engagement reference; nobody pastes a value into a file, prompt or chat. -->

| Name | Value or source | Notes |
|---|---|---|
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` | Oryx installs requirements.txt |
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` | |
| `DATABASE_URL` | Key Vault reference (secrets.production_db_url_ref) | <!-- fill: postgres:// or mssql:// --> |
| `DJANGO_SECRET_KEY` | Key Vault reference | <!-- fill: its own reference --> |

<!-- fill: the remaining rows: DJANGO_ALLOWED_HOSTS (<app>.azurewebsites.net and the custom domain), DJANGO_CSRF_TRUSTED_ORIGINS, DJANGO_ADMIN_URL, the HSTS and SSL settings, CONN_MAX_AGE, DJANGO_DBBACKUP_DIR (under /home, which persists), e-mail, Microsoft sign-in (DJANGO_MICROSOFT_CLIENT_ID, DJANGO_MICROSOFT_TENANT_ID, DJANGO_MICROSOFT_CLIENT_SECRET as a Key Vault reference to secrets.entra.client_secret_ref, DJANGO_ENTRA_GROUP_ROLES), WEBSITES_PORT only for a custom container. -->
