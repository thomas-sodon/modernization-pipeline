<!--
Skeleton for the engagement's section of {{p.target_repo}}/README.md in a demo target repo
(commercial.tier "demo", S6.handover). It goes between the two marker lines, the HTML
comments "engagement:<id>" and "/engagement:<id>", each on a line of its own; the run
rewrites only the text between its own markers and keeps the shared header and every other
engagement's section byte-identical. When target.root is '.', there are no markers: the app
README is the root README. Paths are relative to the repo root (<target.root>/...).
Keep the ### headings. Replace every fill hint; a filled section keeps none. Counts, names
and rates only. Delete this comment.
-->

## $project_name: $source_title

<!-- fill: one paragraph on the original application: its kind (source.kind, for example "an Access database template (.accdt)") and the inventory counts from docs/health-check.md (tables, queries, forms, reports, modules, macros, data macros, VBA lines). No data, no VBA or query text. Then one sentence on what the Django app keeps (areas and must-keep workflows). -->

### Before and after

<!-- fill: before: link <target.root>/docs/img/before/index.md (say the pictures are reconstructed from the form definitions, not screenshots), and embed one wireframe of the main workflow when it exists. After: embed two or three of <target.root>/docs/img/after/<workflow id>-<NN>-<step>.png and link <area id>-main.png per kept area; say they show synthetic seed-1 data only. -->

### Equivalence checks

<!-- fill: the headline from <target.root>/equivalence/results.json, copied exactly: the lowest cell rate over the equivalence cells (gate.min_cell_rate, how many cells: datasets x databases), the pooled rate, the rule-test cells (passed of total per database) and the e2e cell(s). Claim no more than the report: say what recorded the goldens as the report's Method names it (the reference engine, such as UCanAccess for Access sources; "the original app" only when a recorded confirmation run on it exists), give how many kept units are flagged needs_access_confirmation in <target.root>/goldens/manifest.json with no confirmation run, and say a pass on them shows agreement with the reference engine only. Say the checks cover the compared outputs, rules and workflows, not every behaviour; never "prove the same results". Link <target.root>/docs/equivalence-report.md. -->

### Check it yourself

<!-- fill: how a buyer re-runs the synthetic-seed checks without our toolkit: uv, Python 3.13, a PostgreSQL server URL in PG_URL, `cd <target.root>/app && uv sync`, the harness line from <target.root>/equivalence/README.md for seed-1 on postgresql, and `uv run pytest -q --create-db` for the rule and end-to-end tests. Link <target.root>/README.md for more. -->

### Walkthrough

Video walkthrough: link added at the S6 gate

<!-- fill: one line pointing at <target.root>/docs/walkthrough.md (the script) and <target.root>/NOTICE.md (licences and credits). Keep the line above exactly as it is: the operator replaces it with the unlisted video link at the S6 gate. -->
