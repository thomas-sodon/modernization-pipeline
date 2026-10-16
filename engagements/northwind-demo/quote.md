# Fixed quote: Northwind

Fixed price for rebuilding the Northwind Access application (Public demo: Microsoft Northwind 2.0 Developer Edition (Access)) as a web application (Stage 2), based on spec version 1.0 of `analysis/rules.json` (sha256 `5fa8ea6f512ce9535dd39b790488b0614bfec02f37fcd1dcccaad48488efbe7f`). Quote date: 2026-10-16.

**Simulated quote.** This is a public demo engagement: nothing is sold or invoiced, and the figures show how a client quote is built from the signed spec.

## Scope summary

- `orders` (Orders, Invoicing and Shipping): 50 rules, 203 objects kept.
- `inventory` (Products and Inventory): 30 rules, 68 objects kept.
- `purchasing` (Purchasing and receiving): 26 rules, 49 objects kept.
- `reports` (Sales and Catalog Reports): 16 rules, 35 objects kept.

Must keep, each carried by one workflow that an end-to-end test walks through:

- order entry → invoice (printable PDF) → shipping (`order_to_ship`)
- inventory levels and restocking (`inventory_restock`)
- purchase order → receiving → stock update (`purchase_order_to_stock`)
- the invoice report plus at most 4 other reports chosen at S1 (`kept_reports`)

Foundation: sign-in, roles mirroring the app's privileges, plus the audit fields every table carries. Database: PostgreSQL, also verified on SQL Server. Sign-in: local. Hosting: none (the repository is the deliverable). The signed spec is `analysis/rules.md`; the object list is `analysis/keep-drop.md`.

## Hours and price

<!-- estimate:begin -->
| Offer | Hours (low / point / high) | Price USD (low / point / high) |
|---|---|---|
| Stage 2 (stage2-standard) | 128.5 / 160.6 / 200.8 | 16,800 / 20,900 / 26,200 |
| Stage 1 alone | 16.5 / 20.6 / 25.8 | 2,200 / 2,700 / 3,400 |

Rate: 130 USD per hour. Quoted price: 20,900 USD (the point estimate).
Price note: none.

| Area | Hours |
|---|---|
| inventory | 19.5 |
| orders | 49.8 |
| purchasing | 13.4 |
| reports | 11.1 |
<!-- estimate:end -->

## Price tier and fixed price

- Price tier: stage2-standard (several modules, integrations; 18,000 to 35,000 USD for 110 to 200 hours).
- Fixed price: **20,900 USD**.
- No clamp: the point price 20,900 USD is inside the tier range.
- Amount payable: **20,900 USD**.

## Inclusions

- The four kept areas with all 122 rules of the signed spec, their screens, saved queries, 27 services, the printable invoice (PDF) and the four kept reports.
- The foundation: local sign-in, roles mirroring the application's privileges, audit fields.
- The tables of the kept areas moved to the new database with their relations, code lists and constraints.
- Proof: 88 rule tests, equivalence tests that compare the new app's results with the recorded results of the old one (23 rules), and 4 end-to-end workflow tests (11 rules), on PostgreSQL and SQL Server.
- The equivalence report, before-and-after screenshots, the runbook and the handover documents.

## Exclusions

- The 192 dropped objects listed in `analysis/keep-drop.md` (Access-only plumbing, demo-data tools, help and learning screens, the employee email and phone lists).
- New features or changed behaviour beyond the signed rules; they are change orders.
- Hosting, domains and running costs (no hosting in this engagement), and the care plan.
- Stage 1 alone (moving the tables and re-linking the Access screens), priced separately in the table above.
- Data clean-up, and training beyond the handover walkthrough.

## Risks

- **VBA present** (health check): 6,886 lines of VBA hold most of the behaviour; complexity is high (55 forms plus reports). Every recovered rule is tied to its sources and proven by a test.
- **Open questions**: 35 of 35 questions are answered in `answers.md`, and the rules state the answers. The client kept several behaviours that look like defects so that the new app matches the old one: the sales reports' end date compared at midnight (Q-reports-002), the vendor tab that hides unsubmitted purchase orders (Q-purchasing-002) and the employee's recent orders taken from the 20 latest overall (Q-orders-004). Changing any of them later is a change order.
- **Answer read against the design**: Q-reports-006 (catalog category pictures) was asked on a wrong premise. The spec follows the client's choice to keep the original, which the design shows is each category's stored image (R-reports-012). The client confirms this reading at sign-off; a different choice before signature rewords R-reports-012, after signature it is a change order.
- **Rule confidence**: 0 rules have low confidence; 14 have medium confidence (R-orders-021, R-orders-026, R-orders-027, R-orders-036, R-orders-037, R-orders-038, R-orders-042, R-orders-044, R-orders-045, R-orders-048, R-inventory-030, R-purchasing-025, R-purchasing-026, R-reports-010). Their tests or equivalence units confirm them in S2 and S5.
- **No Windows machine with Access** is available, so the old app's behaviour is recorded from the design and the reference engine, not by running Access; report layouts are proven by view tests rather than recorded output.

## Assumptions

- The assumptions of `analysis/rules.md` hold, including the client's answers to all open questions in `answers.md`.
- The rules describe the current behaviour, including behaviour that looks like a defect, except where an answer changes it.
- The client answers any new question in `answers.md` and reviews each stage gate within five working days.
- One data back end; no linked tables, integrations or external links.

## Change orders

After signature, any new or changed rule, any changed keep or drop decision, and any answer that changes a rule is a change order. Each one is written up as `change-orders/CO-NNN.md`, priced on the rules it adds or changes with `toolkit estimate --delta <signed rules sha256>` at the same rate, and approved at its own gate before work on it starts. Answers that do not change a rule are free.

## Payment milestones

| Milestone | Due | Share | Amount USD |
|---|---|---|---|
| Stage 2 | S1 gate | 30% | 6,270 |
| Stage 2 | S5 gate | 40% | 8,360 |
| Stage 2 | S6 gate | 30% | 6,270 |
| **Total** | | **100%** | **20,900** |

Each amount is its share of the fixed price.

## Validity

This quote is valid for 30 days from 2026-10-16, until 2026-11-15.
