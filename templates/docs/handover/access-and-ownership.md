# Access and ownership: $project_name

<!--
Skeleton for {{p.target}}/docs/access-and-ownership.md (S6.handover). Keep the four ##
headings exactly (the acceptance check counts them). Replace every fill hint; a filled
copy keeps none. Written from engagement.json (target, hosting, auth, secrets by reference
name only, care_plan, environment.windows_box). Never a secret value. Delete this comment.
-->

<!-- fill: one paragraph: the rule (the client owns the repository, the host, the database and the app registration; we hold only what the care plan needs after acceptance) and the engagement facts this page is written from. Demos: say who owns what in the demo. -->

## Accounts and owners

<!-- fill: a table with one row per account or resource: what (GitHub repo target.repo, CI, hosting account for hosting.provider, production database, backups storage, Entra app registration for auth.mode microsoft-entra, e-mail/SMTP, domain and certificate, the Windows + Access machine), owner (client, MSP or us), who administers it, and the state at acceptance. -->

## Credentials we hold

<!-- fill: a table with one row per engagement secret reference that is set (secrets.github_push.ref with its expiry, stage1_target_url_ref, staging_db_url_ref, production_db_url_ref, entra.client_secret_ref with secret_expires): the reference name (env:NAME or pass:<path>, never the value), what it opens, where it is stored (data.env or pass on the operator host), and when it is revoked or rotated. Null references: say "not set". Also our own logins (git, gh, hosting console) and local database roles. -->

## Transfer steps

<!-- fill: numbered steps, each with who does it: transfer or confirm the repo (GitHub transfer or the client's own org; branch protection; Actions secrets none); hosting account and billing to the client; database admin login rotated by the client's DBA; Entra app registration owned by the client's tenant, client secret rotated; DJANGO_SECRET_KEY rotated; our admin accounts in the app removed or disabled; push token revoked; Windows-box copies deleted (environment.windows_box.copies). -->

## After acceptance

<!-- fill: what access we keep: care_plan.access_after_acceptance when set; else none. Care plan null or not purchased: we keep no access and hold no credentials; the data retention clock (docs/data-retention.md). The S6 gate sets care_plan.access_after_acceptance and corrects this section in a follow-up PR when it differs. -->
