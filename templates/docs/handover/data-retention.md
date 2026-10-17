# Data retention: $project_name

<!--
Skeleton for {{p.target}}/docs/data-retention.md (S6.handover). Keep the four ## headings
exactly (the acceptance check counts them) and name `mp-data purge`, its --dry-run and the
destruction record path. Replace every fill hint; a filled copy keeps none. Written from
engagement.json (data_policy.retention_days_after_acceptance, care_plan.private_data,
environment.windows_box.copies, database, db_name) and docs/contracts.md sections 1
(Databases) and 15 (Destruction record). Counts and names only. Delete this comment.
-->

<!-- fill: one paragraph: what this page promises (what we hold of the client's data, where, until when, and how its deletion is proved). -->

## What we hold

<!-- fill: a table: item (the source file(s) and data copy with their sha256 pins; the dataset export; the reference database; the goldens of the client's data and their parameter values; data-plane results and logs; the scrub map; the design zone: scrubbed model, inventory, reference catalog, synthetic outputs; the local databases mpdata_<db_name>_stage1, mpdata_<db_name>_gate, mpdata_test_<db_name>_*, <db_name>_dev, <db_name>, test_<db_name>_*; copies on the Windows + Access machine; the records repo, which holds no data values), whether it holds client data, and why we keep it until the end date. -->

## Where it is

<!-- fill: the private directory (expanded path) on the operator host and its zones; the local database servers (Docker containers mp-pg16 and mp-mssql22 on the operator host); each environment.windows_box.copies entry (path, what) or "none"; what is never anywhere else (no cloud storage, no repository, no ticket or chat). -->

## When it is deleted

<!-- fill: the end date: the acceptance date plus data_policy.retention_days_after_acceptance, unless care_plan.private_data says to keep it encrypted for the care plan; null means no clock (say what applies instead). Who decides an earlier deletion. -->

## How deletion is recorded

<!-- fill: `mp-data purge <records>/engagement.json --dry-run` lists what would go (the private directory's top-level entries with file counts, the databases on every running local server, the Windows-box copies) and deletes nothing; without --dry-run it deletes and writes <records>/destruction-record-<YYYY-MM-DD>.md (a second run on the same day writes -<n>), with the sections Deleted, Databases dropped, Servers not checked, Sources and Copies to delete by hand, counts and names only; we delete the Windows-box copies by hand and note the date beside the record, commit the record in the records repo and send the client a copy. Write <records> as the expanded records path. -->
