# Stage 1 cutover plan: $project_name to $database_titles

<!--
Skeleton for {{p.target}}/docs/stage1-cutover.md (S3.move). Fill each $placeholder from the
engagement (database.stage1: fresh_copy, freeze_window, relink_by, rollback; secrets by reference
name only), docs/stage1-runbook.md and the analysis/rules.json workflows. Keep the seven ##
headings exactly as they are and the two commands of Load and verify exactly as written below
(only $engagement_json and $report are filled). Names and settings only: no data values, no
secret values. Delete this comment.
-->

$intro

## Freeze window

<!-- database.stage1.freeze_window: when users stop working in the app, how they are told, and
how the operator checks that nobody has the file open. -->

$freeze_window

## Fresh copy

<!-- Copy the back-end file at the start of the freeze to database.stage1.fresh_copy (a path under
the private sources/ directory on the operator host); compact a copy, never the original;
record its size and sha256. -->

$fresh_copy

## Load and verify

Run both commands in a plain terminal on the operator host, outside any Claude session. The first
creates the schema in the empty database the DBA provided and loads the fresh copy; the second
checks it and writes the report.

```
mp-data stage1 $engagement_json --target-db secret:stage1_target_url_ref --dataset fresh-copy
mp-data stage1 $engagement_json --target-db secret:stage1_target_url_ref --dataset fresh-copy --verify --require-rows --report $report
```

If the load stops part-way, have the DBA drop and recreate the empty database, then run both
commands again.

$load_and_verify

## Relink

<!-- Follow docs/stage1-runbook.md "Relink the tables" on every machine (database.stage1.relink_by
names who); the front-end changes go in before the relinked copy is handed out. List each entry
of the scan's "Writes to tables named at run time" here, with the decision on whether it still
runs after the cutover. When such a write runs at first-run startup, add a step, before users
open the relinked front end, that disables it or checks that the first-run setting in the loaded
data marks one-time processing as done. -->

$relink

## Smoke checklist

<!-- One item per kept workflow (analysis/rules.json workflows) or main form, each a short
action and the expected result. -->

$smoke_checklist

## Rollback

<!-- database.stage1.rollback: the trigger, the steps (reopen the pre-cutover copy), what
happens to rows entered on the server since, and who decides. -->

$rollback

## Sign-off

<!-- Who signs, the report's sha256, the date. -->

$sign_off
