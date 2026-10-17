<!--
Skeleton for {{p.target_repo}}/NOTICE.md in a demo target repo (commercial.tier "demo",
S6.handover). Two parts:

1. The shared header, everything above the first engagement marker. No engagement run
   writes it: it stays byte-identical to the target repo's main branch
   (`git show origin/main:NOTICE.md`), including the setup stub of pipeline
   docs/runbook.md 0.5 ("# Notices" and one line). Claims about "every app" do not belong
   in it; each engagement credits its own app's material inside its own section.
2. One section per engagement, between the HTML-comment marker lines "engagement:<id>" and
   "/engagement:<id>". A run rewrites only its own section and keeps every other section
   byte-identical. Check before reporting done: with the run's own section emptied, the
   file equals origin/main's with that section emptied.

Below is the stub header as setup writes it (do not change it) and the skeleton of one
engagement section. The cross-app components (our MIT code, cookiecutter-django, Jackcess
for Access sources, htmx, the ODBC driver) are listed inside the section, for this
engagement's app only.

When target.root is '.', there is no root NOTICE: the app NOTICE.md is the only one.
The demo root LICENSE is the file LICENSE beside this skeleton, with $year (the S6 year)
and $author_name (pipeline.json stack.app_template.answers.author_name) filled. The first
demo run that finds no root LICENSE creates it; later runs keep it byte-identical. Clients
get no LICENSE: their app README carries the Ownership section instead.
Replace every fill hint; a filled section keeps none. Delete this comment.
-->

# Notices

Third-party material and credits, one section per engagement, written at its S6 stage.

<!-- engagement:$engagement_id -->
## $project_name ([$root/]($root/))

- **Our code.** The code and documents written for this demo in `$root/` are under the MIT licence in [LICENSE](LICENSE). That licence covers our own work only; the third-party material below keeps its own licence.
- **Source credit.** <!-- fill: Access demo: the original's names and structure are Microsoft's, published without a licence, and used as names and structure only (no data, VBA, query or macro text; the repo does not contain the source file). Excel demo: the original project's name, author, the copyright line exactly as its LICENSE states and the full MIT permission text. -->
- **Trademarks.** <!-- fill: Access demo: Microsoft, Access and Northwind are trademarks of the Microsoft group of companies; not affiliated with or endorsed by Microsoft. Excel demo: Microsoft and Excel likewise. -->

| Component | Licence | How the $project_name app uses it |
|---|---|---|
| cookiecutter-django | BSD-3-Clause | <!-- fill: tag and commit from pipeline.json stack.app_template; the project layout, settings and users app the app starts from; full licence text in $root/NOTICE.md --> |
| Jackcess | Apache-2.0 | <!-- fill: only when $root/app/<slug>/core/THIRD_PARTY.md names it: version from it; behaviour reference for core/vbacompat.py, no Jackcess code included; otherwise delete this row --> |
| htmx 2 | 0BSD | <!-- fill: version from the file django-htmx ships; served by django-htmx, not copied into the repo --> |
| Microsoft ODBC Driver 18 for SQL Server | Microsoft proprietary licence | <!-- fill: bundled by mssql-python through mssql-python-odbc (driver version from its installed files); installed from PyPI at deploy time, used only on SQL Server, not in the repo --> |
| <!-- fill: one row per vendored asset under $root/app/<slug>/<area>/static/<area>/vendor/: name --> | <!-- fill: licence --> | <!-- fill: version, where, and the area THIRD_PARTY.md that records it --> |

Everything else, the <!-- fill: count from uv export --> runtime Python packages with their versions and licences, the full text of each `THIRD_PARTY.md` and the cookiecutter-django licence, is in [$root/NOTICE.md]($root/NOTICE.md).
<!-- /engagement:$engagement_id -->
