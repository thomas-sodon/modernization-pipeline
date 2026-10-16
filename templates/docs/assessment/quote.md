# Fixed quote: $project_name

<!-- Header: who the quote is for, the spec it prices (analysis/rules.json spec version and sha256 from estimate.json rules_sha256), and, for a demo engagement (commercial.quote_is_simulated true), one line saying the quote is simulated and nothing is sold or invoiced. -->
$intro

## Scope summary

<!-- Scope summary: the kept areas with their rule counts, the must-keep items, the foundation items (sign-in, roles), the databases the app runs on and the hosting choice. Point to analysis/rules.md for the signed spec and analysis/keep-drop.md for the object list. -->
$scope_summary

## Hours and price

<!-- Hours and price: the block between the estimate markers is written only by `toolkit estimate render <records>/estimate.json --into <records>/quote.md`; never edit it by hand. -->
<!-- estimate:begin -->
<!-- estimate:end -->

## Price tier and fixed price

<!-- Price: the tier row of pipeline.json pricing_tiers that estimate.json names, its price range, and the fixed price = estimate.json price_usd.point (the pricing rule, clamped to the tier range). State any clamp in one line (estimate.json price_note), or say there is none. Remove the "Assessment credit" line when commercial.assessment_credit_usd is null; otherwise the amount payable is the fixed price minus the credit. -->
- Price tier: $tier ($tier_scope; $tier_range).
- Fixed price: **$fixed_price_usd USD**.
- $clamp_line
Assessment credit: -$assessment_credit_usd USD
- Amount payable: **$payable_usd USD**.

## Inclusions

<!-- Inclusions: what the fixed price buys: the kept areas and their workflows, the foundation, the tests that prove the rules (rule tests, equivalence against the recorded behaviour, end-to-end tests), the documents, the databases, the handover. -->
$inclusions

## Exclusions

<!-- Exclusions: what the price does not cover: dropped and out-of-scope objects (keep-drop.md), new features, hosting and running costs unless quoted, data clean-up, training beyond the handover walkthrough, the care plan. -->
$exclusions

## Risks

<!-- Risks: the health-check risk flags and complexity, the open questions (count, and those that could change a rule), the low-confidence rules (and the medium ones when there are no low ones), and how each risk is handled. -->
$risks

## Assumptions

<!-- Assumptions: the main assumptions of analysis/rules.md that the price depends on, and the client's duties (answers to open questions, test data, access, timely review at each gate). -->
$assumptions

## Change orders

<!-- Change-order policy: after signature any new or changed rule is a change order: written in records change-orders/CO-NNN.md, priced with `toolkit estimate --delta <signed rules sha256>` on the added and changed rules, and approved at its own gate before work starts. -->
$change_orders

## Payment milestones

<!-- Payment milestones: one row per engagement commercial.milestones entry: when it is due and its amount = pct x (fixed price - assessment credit), rounded to whole USD; the amounts add up to the amount payable. -->
$milestones

## Validity

<!-- Validity: the quote is valid for 30 days from its date; give the last valid date. -->
$validity
