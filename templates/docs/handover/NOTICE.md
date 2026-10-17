# Notices: $project_name

<!--
Skeleton for {{p.target}}/NOTICE.md, the app's notice file (S6.handover). Keep the ##
headings and both table header lines exactly. Replace every fill hint; a filled copy keeps
none. Work the package rows out with a throwaway script (never added to a repo) that reads
`uv export --frozen --no-dev --no-hashes --no-emit-project` in app/ and each package's
licence from its installed metadata (importlib.metadata: License-Expression, License,
classifiers, and the licence file when those are vague). Take the htmx version from the file
django-htmx ships (django_htmx/static/django_htmx/htmx-2*.js) and the ODBC driver version
from mssql-python-odbc's installed files. Delete this comment.
-->

<!-- fill: one paragraph: what this file lists (the runtime packages the app installs, the third-party files and behaviour it bundles or follows, the generator it started from, and the credit for the original application) and the licence of our own code (demo: MIT, root LICENSE; client: owned by the client, no open-source licence granted). -->

## Runtime packages

<!-- fill: one row per line of `uv export --frozen --no-dev --no-hashes --no-emit-project`, in its order, with the exact package name as uv prints it, the pinned version and an SPDX licence expression. Below the table, name packages that install only on some platforms (from the export's markers). These packages are installed from PyPI at deploy time; this repository does not contain them. -->

| Package | Version | Licence |
|---|---|---|

## Bundled third-party files

<!-- fill: one row per bundled or followed third-party component: `Jackcess` (version from core/THIRD_PARTY.md, Apache-2.0, behaviour reference for vbacompat in core/), `htmx` (2.x from django-htmx's file, 0BSD, inside django-htmx), `Microsoft ODBC Driver 18 for SQL Server` (version from mssql-python-odbc, Microsoft proprietary licence, bundled by mssql-python through mssql-python-odbc) and every vendored asset under app/<slug>/<area>/static/<area>/vendor/ with its version exactly as pipeline.json stack.app_assets pins it, its licence, its path and the sha256 its THIRD_PARTY.md records (for example `| Chart.js | 4.5.1 | MIT | ... |`). Then name anything loaded from a CDN at run time and not bundled. -->

| Component | Version | Licence | Where |
|---|---|---|---|

## THIRD_PARTY.md files

<!-- fill: one ### heading per THIRD_PARTY.md under app/ (excluding .venv), named by its path from this folder (app/<slug>/<area>/THIRD_PARTY.md), followed by its full text in a fenced block of four backticks. -->

## Project template

<!-- fill: cookiecutter-django: tag and commit from pipeline.json stack.app_template, what came from it (project layout, settings, users app, templates), licence BSD-3-Clause, and the full licence text from the template's LICENSE in a fenced block. -->

## Source credit

<!-- fill: the original application: name, publisher and engagement source.licence in words. Access demo: names and structure only (no data, VBA or query text); trademarks. Client: the client's own application, named as the client wishes. -->
