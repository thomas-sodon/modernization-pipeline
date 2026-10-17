#!/usr/bin/env bash
# Skeleton for {{p.target}}/docs/deploy/startup.sh (S6.handover, hosting.provider
# azure-app-service). Set it as the Web App's startup command. Replace every "fill:" line
# with the engagement's command, or delete it; a filled copy keeps no "fill:" line. It reads
# settings from the environment only (App Service app settings, Key Vault references), never
# from a file in the repo.
set -euo pipefail

# fill: cd to the app folder App Service deployed (the folder that holds manage.py)
cd "${APP_PATH:-/home/site/wwwroot}"

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.production}"

# fill: install WeasyPrint's system libraries here when the image lacks them (libpangoft2-1.0-0, libharfbuzz-subset0), or use a custom container
python manage.py migrate --noinput
python manage.py collectstatic --noinput
# fill: one line per JOBS entry whose schedule is @reboot (python manage.py <command>); scheduled ones are WebJobs

exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers "${GUNICORN_WORKERS:-3}" --timeout 120 --access-logfile - --error-logfile -
