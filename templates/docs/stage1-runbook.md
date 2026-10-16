# Stage 1 runbook: relink $project_name to $database_titles

<!--
Skeleton for {{p.target}}/docs/stage1-runbook.md (S3.move). Fill each $placeholder from the
engagement (database.primary, database.verify_also, database.stage1, secrets by reference name
only), from docs/stage1-frontend-changes.md and, when it exists, analysis/rules.json workflows.
Keep the seven ## headings exactly as they are. Names, counts and settings only: no data values,
no passwords, no secret values, no copied VBA or query text. Delete this comment.
-->

$intro

## Before you start

<!-- Who does what and in which order; the Windows + Access machine and its Office build; the
empty target database the DBA creates; the secret reference that holds its URL
(secrets.stage1_target_url_ref, named, never the value); the cutover plan
(docs/stage1-cutover.md); Microsoft SSMA for Access as the Windows alternative for SQL Server
clients. -->

$before_you_start

## ODBC driver and Office bitness

<!-- database.stage1.odbc_driver and office_bitness, or "not set". SQL Server: Microsoft ODBC
Driver 18 for SQL Server. PostgreSQL: psqlODBC (Unicode) with 'True is -1' on, 'Bools as Char'
off and 'Row Versioning' on. The driver's bitness matches Office's on every machine. -->

$odbc

## Connection string

<!-- A DSN-less connection string per dialect, with placeholders for server, port and database
and without the password (Windows authentication, or the password asked at link time and not
saved). -->

$connection_string

## Relink the tables

<!-- Every table of schema.json, by name; the link step; renaming the dbo_ or public_ prefix
back to the original name; checking each link. -->

$relink

## Front-end changes

<!-- One entry per change in docs/stage1-frontend-changes.md: what to change, where (object,
procedure and line) and why. Give each entry of the scan's "Writes to tables named at run time"
subsection its own item (both dialects): where it is called from, what it changes once relinked,
and that the process owner decides whether it still runs; the cutover lists it under "Relink". -->

$frontend_changes

## Backups and restore

<!-- pg_dump and pg_restore, or SQL Server BACKUP DATABASE and RESTORE; where the files go; a
restore test; the front-end file copies. -->

$backups

## Rollback

<!-- Relink to the old back end (or reopen the pre-cutover copy), what happens to rows entered on
the server since, and who decides. -->

$rollback
