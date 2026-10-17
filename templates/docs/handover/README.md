# $project_name

<!--
Skeleton for {{p.target}}/README.md, the app README the client receives (S6.handover).
Keep the ## headings exactly as they are and in this order. Replace every fill hint
with text written from the engagement; a filled copy keeps no fill hint and no
$placeholder. Names, counts and rates only: no data values, no copied VBA, query or
macro text. Delete this comment.

Sources: analysis/rules.md (Summary, Must keep, Workflows), docs/img/after/,
docs/img/before/index.md, equivalence/results.json (copy every figure exactly),
equivalence/README.md, app/pyproject.toml, app/<slug>/*/apps.py, engagement.json
(commercial.tier, source.kind, source.licence, hosting.provider).
-->

<!-- fill: one paragraph: what the app does, paraphrased from the Summary of analysis/rules.md (no data values), and which original application it replaces (source kind, never the client's name for clients). When it mentions the tests, say what they compare against (reference outputs recorded by the engine the equivalence report's Method names, not the original app unless a confirmation run on it is recorded) and link docs/equivalence-report.md; never claim the tests prove the app gives the same results as the original. -->

## What it does

<!-- fill: one bullet per kept area (id and title from analysis/rules.json areas) with one sentence each, then the must-keep workflows (workflow id and title) from analysis/rules.md. Close with one line on the foundation: sign-in (engagement auth.mode) and roles mirroring the original's privileges. -->

## Screens

<!-- fill: after pictures from docs/img/after/: one <area id>-main.png per kept area, then, per workflow, its <workflow id>-<NN>-<step>.png steps in order (link each; embed one or two). Say they show synthetic seed-1 data only. Before pictures: link docs/img/before/index.md; or one line saying the client supplied screenshots (engagement assets.screenshots); or one line saying before pictures are not available for this source kind. -->

## Run it locally

<!-- fill: Python 3.13 through uv; `cd app && uv sync`; DATABASE_URL (postgres:// or mssql://; without it the app uses $PG_URL/<db_name>); `uv run python manage.py migrate`; load synthetic seed 1 with `import_dataset ../goldens/synthetic/seed-1/input --flush`; `DEMO_PASSWORD=... uv run python manage.py create_demo_users`; `uv run python manage.py runserver`. System libraries: WeasyPrint (libpangoft2-1.0-0, libharfbuzz-subset0), SQL Server (libodbc2, libltdl7). Link docs/runbook.md#run-it-locally for the rest. -->

## Run the tests

<!-- fill: `uv run pytest -q --create-db` from app/ (whole suite, end-to-end included, after `uv run playwright install chromium`); on SQL Server add `--ignore=tests/e2e`. Where the rule tests live (<slug>/<area>/tests/test_rules.py, docstring starts with the rule id) and the end-to-end tests (app/tests/e2e/, one per workflow plus the walkthrough). What target CI runs. -->

## Verify equivalence

<!-- fill: how to run the vendored harness on the synthetic seeds, linking equivalence/README.md; the gate figures copied exactly from equivalence/results.json (gate.min_cell_rate, gate.pooled_rate, gate.threshold, and one table row per cell: suite, dataset, db, passed, total, rate); a link to docs/equivalence-report.md for the full report and the known differences. Say what recorded the goldens, as the report's Method names it (for Access sources the UCanAccess reference engine, not Microsoft Access, unless a confirmation run on Access is recorded), and how many kept units are flagged needs_access_confirmation in goldens/manifest.json without a confirmation run (a pass on them shows agreement with the reference engine only). Say the engagement dataset runs only on our side through the data plane. -->

## App layout

<!-- fill: a tree or table of app/: config/ (settings, discovery, health), <slug>/<area>/ one Django app per kept area (from apps.py nav_label), core/, legacy/, domain/, users/, tests/e2e/; ../analysis/, ../goldens/, ../equivalence/, docs/. One line on how an area plugs in (AppConfig feature = True, discovered at start). -->

## Documents

<!-- fill: links: docs/runbook.md (operations), docs/deploy/ (hosting.provider), docs/data-policy.md, docs/access-and-ownership.md, docs/data-retention.md, docs/equivalence-report.md, analysis/rules.md, NOTICE.md; demos also docs/walkthrough.md and docs/legal-check.md. -->

## Ownership

<!-- fill: clients (commercial.tier is not demo): the client owns the code in this folder; no open-source licence is granted; third-party parts keep their own licences (NOTICE.md). Demos: replace this heading and text with `## Licence`: our code is under the MIT licence in the repository's root LICENSE; third-party material keeps its own licence (NOTICE.md); names and structure of the original are credited there. -->
