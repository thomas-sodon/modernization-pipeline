# CO-NNN: <title>
requested: <YYYY-MM-DD>
base_rules_sha256: <sha256 of the approved rules.json>
rules_sha256_after: <sha256 of rules.json with this change>
rules_md_sha256_after: <sha256 of rules.md with this change>
approved:

## Request
<!-- Request: written by the orchestrator before the run: what the client asked for, in their words, and who asked. -->

## Rules added
<!-- Rules added: one line per new rule: its new id (never a reused or renumbered one), statement, trigger and test kind. Write "None." when there are none. -->

## Rules changed
<!-- Rules changed: one line per amended rule: its existing id, what changed and why. Write "None." when there are none. -->

## Objects changed
<!-- Objects changed: objects whose decision, target component or reason changed, and any changed workflows, services or tables (and, for workbooks, data-model.json). Write "None." when there are none. -->

<!-- Estimate: the block between the markers is written only by `toolkit estimate render <records>/change-orders/CO-NNN.estimate.json --into <records>/change-orders/CO-NNN.md`; its price is delta.price_usd of the estimate made with `toolkit estimate --delta <base_rules_sha256>`. The header lines above stay at column 0, and approved: stays empty until the change-order gate. -->
<!-- estimate:begin -->
<!-- estimate:end -->
