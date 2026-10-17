# Legal check: $project_name

<!--
Skeleton for {{p.target}}/docs/legal-check.md, demo engagements only (commercial.tier
"demo", S6.handover). Keep the five ## headings exactly (the acceptance check counts them).
S6.handover fills the first three sections and writes the Pre-flip checks table with its
Date, Commit and Result columns empty; the operator fills those columns and the Decision
at the S6 demo gate (pipeline docs/runbook.md section 6, step 10). Replace every fill hint;
a filled copy keeps none (the Decision gets the placeholder sentence its hint gives).
Delete this comment.
-->

<!-- fill: one paragraph: what is checked (that everything this repository publishes for the engagement is our own work, derived from third-party names and structure only, third-party material under a licence that allows it and credited, or synthetic data) and that the repository becomes public at the S6 gate. -->

## What is published

<!-- fill: a table | Path | What it is | Class | Note | with one row per top-level path under <target.root>/ (README.md, NOTICE.md, analysis/, app/, docs/, equivalence/, goldens/ and any other) and one per root file or folder of the repo (README.md, NOTICE.md, LICENSE, .gitignore, .gitattributes, .pre-commit-config.yaml, .github/). Class is one of: own work; derived from third-party names and structure; third-party licensed (credited in NOTICE.md); synthetic data. A path that mixes classes names its main class and the rest in Note. -->

## Derived from third-party material

<!-- fill: what was derived and how: the rules (paraphrased from the scrubbed design, no code, query or macro text), object, table and column names (used as identifiers), the before wireframes (reconstructed from form definitions, no data), the reference functions (our rewrites of behaviour, using names only), the moved table structure (legacy models), the goldens' parameter names, and what recorded the goldens (the reference engine and version from the report's Method, and the units not confirmed on the original, consistent with the README and walkthrough wording); what was never copied (data values, VBA, query SQL, macro text, images, the source file); the tools that read the source (pipeline only, never published). -->

## Licences

<!-- fill: our code: MIT (root LICENSE), our own work only; the source: its licence status (engagement source.licence, in words) and the trademarks; every third-party component with its licence and where it is credited (NOTICE.md tables, THIRD_PARTY.md texts, cookiecutter-django BSD-3-Clause text); copyleft packages (LGPL, GPL alternatives) and why they are fine (installed from PyPI at deploy time, unmodified, not in the repo); anything that does not fit (for example a generator default such as a "Not open source" licence field) and how it is resolved. -->

## Pre-flip checks

<!-- fill: a table | # | Check | Command | Date | Commit | Result | with one row per pre-flip check of the S6 demo gate (pipeline docs/runbook.md section 6, steps 3 to 9: fetch all, delete merged stage branches, no unmerged branch, guard with --history, no forbidden file name in history, Actions runs deleted, PR texts read and fixed), each with its command written out. Leave Date, Commit and Result empty: the operator fills them on the publish branch. -->

## Decision

<!-- fill: one line: "Left for the operator at the S6 demo gate: publish (flip the repository to public) only when every pre-flip check passed; otherwise use the fallback in pipeline docs/runbook.md section 6." The operator replaces it with the decision, the date and their name. -->
