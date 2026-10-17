# Data policy: $project_name

<!--
Skeleton for {{p.target}}/docs/data-policy.md (S6.handover). Keep the ## headings. Replace
every fill hint; a filled copy keeps none. Written from engagement.json data_policy
(redact, execution, scrub_design, commit_data, ai_terms, client_consent), docs/contracts.md
section 1 (Private directory zones, Databases) and the app's settings. Names and counts
only. Delete this comment.
-->

<!-- fill: one paragraph: which data this page is about (the client's source file and data, and what the running app stores) and the engagement's data policy in plain words (redaction always on; execution local or cloud-allowed; design scrubbed or not; data never committed). Demos: say the source is public sample data handled as client data as a rehearsal. -->

## How your data was handled

<!-- fill: the two zones of the private directory on the operator host (design zone: scrubbed model, inventory, reference catalog, synthetic outputs; data zone: source, datasets, reference database, goldens, results, logs, scrub map); only the pipeline's data-plane tool (mp-data) reads the data zone and its output is a summary with counts, never values; failures on your data are redacted; engagement rows enter only local databases named mpdata_<db_name>_* that the development login cannot open; nothing from the data zone is committed (the guard checks every commit; a pre-commit hook rejects source and sample file types); AI sessions saw only the design zone, synthetic data and counts (ai_terms, client_consent). -->

## What is in this repository

<!-- fill: what is committed: the paraphrased rules and keep/drop list, the app code, the synthetic seeds and their expected outputs, the manifest with HMAC digests (not outputs) of your data's goldens, the parameter names of the goldens, results.json counts, screenshots on synthetic data. No rows, values, VBA, query or macro text. -->

## What the running app stores

<!-- fill: the business tables moved from the original (legacy models: list the kept tables by count and name groups), users and their e-mail addresses, allauth sign-in records and MFA authenticators, sessions, audit history (django-simple-history), import and export through the admin (django-import-export), dbbackup files in DJANGO_DBBACKUP_DIR, server logs (console). Which of these hold personal data. -->

## Backups and access

<!-- fill: backups: the host's managed database backups and dbbackup copies, where they are kept and for how long; who can reach the database and the host (hosting.managed_by, care_plan), who has admin accounts in the app; link docs/access-and-ownership.md and docs/data-retention.md. -->
