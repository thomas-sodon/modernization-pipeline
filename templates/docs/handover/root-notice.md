<!--
Skeleton for {{p.target_repo}}/NOTICE.md in a demo target repo (commercial.tier "demo",
S6.handover). Two parts:

1. The shared header, everything above the first engagement marker. While the root NOTICE
   header is still the setup stub of pipeline docs/runbook.md 0.5 ("# Notices" and one
   line), the run replaces the stub with the shared header below. Once a header like this
   one is there, later runs keep it byte-identical.
2. One section per engagement, between the HTML-comment marker lines "engagement:<id>" and
   "/engagement:<id>". A run rewrites only its own section and keeps every other section
   byte-identical.

When target.root is '.', there is no root NOTICE: the app NOTICE.md is the only one.
The demo root LICENSE is the file LICENSE beside this skeleton, with $year (the S6 year)
and $author_name (pipeline.json stack.app_template.answers.author_name) filled. Clients
get no LICENSE: their app README carries the Ownership section instead.
Replace every fill hint; a filled file keeps none. Delete this comment.
-->

# Notices

This repository holds demo engagements of the modernization pipeline: each folder rebuilds one legacy Office application as a Django app. This file credits third-party material; each app folder's `NOTICE.md` has the full detail.

## Our code

The code and documents written for these demos are under the MIT licence in [LICENSE](LICENSE). That licence covers our own work only. Third-party material keeps its own licence, listed below and in each app's `NOTICE.md`.

## Third-party material in every app

| Component | Licence | How it is used |
|---|---|---|
| cookiecutter-django | BSD-3-Clause | <!-- fill: tag and commit from pipeline.json stack.app_template; the generated project layout and settings each app starts from; the full licence text is in each app's NOTICE.md --> |
| Jackcess | Apache-2.0 | <!-- fill: version from core/THIRD_PARTY.md; behaviour reference for core/vbacompat.py (no Jackcess code included) --> |
| htmx 2 | 0BSD | <!-- fill: version from the file django-htmx ships; served by django-htmx, not copied into the repo --> |
| Microsoft ODBC Driver 18 for SQL Server | Microsoft proprietary licence | <!-- fill: bundled by mssql-python through mssql-python-odbc; installed from PyPI at deploy time, not in the repo --> |

Each app's `NOTICE.md` lists its runtime Python packages with their versions and licences, the third-party files it bundles, and the full text of every `THIRD_PARTY.md` in its code.

<!-- fill: then the engagement sections, each between its own marker lines. Section text per engagement: -->
<!-- fill: Access demo: the original's names and structure are Microsoft's, published without a licence, and used as names and structure only (no data, VBA or query text); Microsoft, Access and Northwind are Microsoft trademarks; link <root>/NOTICE.md. -->
<!-- fill: Excel demo: the original project's name, author, copyright line exactly as its LICENSE states and the full MIT permission text; every vendored asset (name, version, licence, where: <root>/app/<slug>/<area>/static/<area>/vendor/); link <root>/NOTICE.md. -->
