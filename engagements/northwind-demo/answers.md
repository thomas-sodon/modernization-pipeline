# Answers to open questions: northwind-demo

One entry per open question of analysis/rules.json. To answer, write the rule (never data values) below the question line, then set `answered:` to the date and add `by: client` or `by: you`.

## Q-orders-001
question: The printed invoice shows the date it is printed, not the stored invoice date, so a reprint carries a new date. Should the invoice show the stored invoice date instead? (Assumption until answered: keep the original behaviour.)
Accept the stated assumption: keep the original behaviour.
answered: 2026-10-16
by: you

## Q-orders-002
question: The original enforces no rules on the invoice, shipped and paid dates (they can be any date, also before the order date). Should the new app require shipped date and paid date to be on or after the order date (and paid on or after shipped)? (Assumption: no extra date checks.)
Accept the stated assumption: no extra date checks.
answered: 2026-10-16
by: you

## Q-orders-003
question: Tax is charged on the line subtotal only, never on the shipping fee, and a shipping fee of zero is accepted while a blank fee blocks invoicing. Is that correct for your business? (Assumption: keep as is.)
Accept the stated assumption: keep as is.
answered: 2026-10-16
by: you

## Q-orders-004
question: The employee screen's recent orders list is built from the 20 most recent orders overall and then narrowed to the employee, so an employee may see fewer than 20 or none. Should it show the employee's own 20 most recent orders instead? (Assumption: keep the current behaviour, as R-orders-027 states and its equivalence unit proves. If you choose the employee's own 20 most recent before sign-off, R-orders-027 is restated and proven by a rule test instead; after sign-off it is a change order.)
Accept the stated assumption: keep the current behaviour, as R-orders-027 states and its equivalence unit proves. If you choose the employee's own 20 most recent before sign-off, R-orders-027 is restated and proven by a rule test instead; after sign-off it is a change order.
answered: 2026-10-16
by: you

## Q-orders-005
question: On the first start the original shifts every date in the database forward so the sample data looks current. Keep this as a one-off start-up command in the new app (useful for demo data), or drop it? (Assumption: keep as a one-off command guarded by the first-run flag.)
Accept the stated assumption: keep as a one-off command guarded by the first-run flag.
answered: 2026-10-16
by: you

## Q-orders-006
question: Saving a company asks 'do you want to save your changes?' while other screens save without asking. Keep the confirmation for companies? (Assumption: keep.)
Accept the stated assumption: keep.
answered: 2026-10-16
by: you

## Q-orders-007
question: Deleting a company that has no orders or purchase orders also deletes its contacts and vendor-product links after one confirmation. Is that cascade wanted? (Assumption: yes.)
Accept the stated assumption: yes.
answered: 2026-10-16
by: you

## Q-orders-008
question: These Access conveniences are dropped and replaced by standard web features: the recently-used list on the ribbon, export of any list to Excel, the mailing-label wizard, the 'click to map' link, e-mailing the company screen, the show-filter and show/hide-columns buttons. Is any of them needed in the new app?
None of them is needed. Standard web features replace them: the xlsx export of lists (admin export), browser bookmarks, and the list filters. No mailing labels, map link or e-mailing of the company screen.
answered: 2026-10-16
by: you

## Q-orders-009
question: Which order list filter should be selected when the list opens? The design does not record the default. (Assumption: Open orders.)
Accept the stated assumption: Open orders.
answered: 2026-10-16
by: you

## Q-orders-010
question: Phone numbers are forced into a US ten-digit format with an optional extension. Keep the US format, or accept international numbers? (Assumption: keep the US format.)
Accept the stated assumption: keep the US format.
answered: 2026-10-16
by: you

## Q-orders-011
question: A new company is tax exempt by default. Should new customers default to Taxable instead? (Assumption: keep exempt.)
Accept the stated assumption: keep exempt.
answered: 2026-10-16
by: you

## Q-orders-012
question: An order can be invoiced only when every line is Allocated (no partial invoices or backorders). Is that still the rule? (Assumption: yes.)
Accept the stated assumption: yes.
answered: 2026-10-16
by: you

## Q-inventory-001
question: Only Approved purchase orders count as on order in the stock figures and the suggested reorder quantity, while the reorder action also treats New and Submitted orders as outstanding. Should New and Submitted orders keep being left out of the on-order figure?
Yes: keep the original behaviour. Only Approved purchase orders count as on order in stock figures and the suggested reorder quantity; the reorder action keeps treating New and Submitted as outstanding.
answered: 2026-10-16
by: you

## Q-inventory-002
question: When a product has no stock take, the old app silently creates an opening zero-quantity stock take the first time stock is calculated. May the new app create that opening stock take when the product is created instead (same figures)?
Yes: the new app may create the opening zero-quantity stock take when the product is created, provided every stock figure stays the same as the original.
answered: 2026-10-16
by: you

## Q-inventory-003
question: Stock takes are always dated at the moment they are saved, so a count made earlier cannot be back-dated. Keep that restriction?
Keep the restriction: a stock take is dated when it is saved.
answered: 2026-10-16
by: you

## Q-inventory-004
question: Is the spreadsheet export of the product list still needed, and is an .xlsx download from the web page acceptable?
Yes, an .xlsx download of the product list from the web page is acceptable and replaces the spreadsheet export.
answered: 2026-10-16
by: you

## Q-inventory-005
question: Discontinued products are still offered when picking a product on an order line. Should they stay selectable for new orders?
Keep the original behaviour: discontinued products stay selectable on order lines.
answered: 2026-10-16
by: you

## Q-inventory-006
question: Audit stamps record the signed-in employee's full name; the old app falls back to the Windows user name when nobody is signed in. The web app always has a signed-in user, so we record that user only. Is that acceptable for every table with audit fields, and which name should stamps from imports or system jobs carry?
Record the signed-in user only, for every table with audit fields; there is no operating-system login fallback. Rows written by imports, start-up jobs and other system jobs (no signed-in user) carry the fixed name "System". Every audit rule states this same behaviour.
answered: 2026-10-16
by: you

## Q-inventory-007
question: Product codes are rebuilt from the fixed NWT prefix, the category code and the product number whenever the category changes. Keep the prefix and the rebuild on category change, or should codes stay fixed once issued?
Keep the NWT prefix and the rebuild of the product code when the category changes.
answered: 2026-10-16
by: you

## Q-inventory-008
question: Sold quantity counts every line of an invoiced order from the invoice date on, whatever the line's own status. Is that the intended point at which stock leaves the warehouse (rather than shipping)?
Yes: keep the original behaviour. Stock counts as sold from the invoice date for every line of an invoiced order.
answered: 2026-10-16
by: you

## Q-purchasing-001
question: Once a purchase order is saved its vendor is locked in every status, yet the app also has a confirm-and-delete-all-lines path for changing the vendor. Should the vendor of a saved New purchase order be changeable (deleting its lines after confirmation), or fixed once the purchase order is saved, as the current app effectively behaves?
Fixed once saved, as the current app behaves. The vendor of a saved purchase order cannot be changed.
answered: 2026-10-16
by: you

## Q-purchasing-002
question: The purchase order tab on a vendor's company page lists only purchase orders that already have a submitter, so New purchase orders do not appear there, while the main purchase order list shows them. Is that intended, or should the vendor tab show every purchase order of the vendor?
Keep the original behaviour: the vendor tab lists only purchase orders that have a submitter.
answered: 2026-10-16
by: you

## Q-purchasing-003
question: Tax on a purchase order is the vendor tax rate from the system settings times the line subtotal, the shipping fee is not taxed, and the tax is stored only when the purchase order is closed. Please confirm, and confirm that the vendor tax rate stays a single system-wide setting.
Confirmed: vendor tax rate times the line subtotal, shipping not taxed, tax stored when the purchase order is closed; the vendor tax rate stays one system-wide setting.
answered: 2026-10-16
by: you

## Q-purchasing-004
question: Any user with the approve-purchase-order privilege can approve, including the person who submitted the purchase order. Should self-approval be blocked?
Keep the original behaviour: self-approval is allowed.
answered: 2026-10-16
by: you

## Q-purchasing-005
question: Receiving is all or nothing: the whole purchase order is received at once and every line gets the same received date. Do you need partial receipts (some lines or part quantities)?
No partial receipts: receiving stays all or nothing.
answered: 2026-10-16
by: you

## Q-purchasing-006
question: The table design describes the submitted, approved, received and payment dates as dates without a time, but the app stores the date and time. Should the new app keep date and time (the line received time matters for stock calculations) or store dates only on the purchase order?
Keep date and time, as the app stores them.
answered: 2026-10-16
by: you

## Q-purchasing-007
question: The received date stays editable while a purchase order is Approved, and the shipping fee and payment method only while it is Received. Is that intended? Also, the payment method is chosen from a fixed list of three (credit card, check, cash): is that the full list?
Intended; keep the editing windows. The payment method list of three (credit card, check, cash) is complete.
answered: 2026-10-16
by: you

## Q-purchasing-008
question: When a line's quantity leaves the product below its target level, the app only warns and still saves the line. Keep it as a warning, or make it a hard stop?
Keep it as a warning; the line still saves.
answered: 2026-10-16
by: you

## Q-reports-001
question: Besides the invoice, we propose keeping four reports: monthly sales per employee, monthly sales per product, quarterly sales per product and the product catalog. We propose dropping the employee email list and the employee phone list, because the employee list page shows the same columns. Please confirm, or name the reports you would swap in.
Confirmed: keep the invoice plus monthly sales per employee, monthly sales per product, quarterly sales per product and the product catalog; drop the employee email and phone lists.
answered: 2026-10-16
by: you

## Q-reports-002
question: Order dates carry a time of day, but the sales reports compare them with an end date at midnight. Orders placed on the end date after midnight are therefore left out, including, with the default period, the latest orders themselves. Should the end date count the whole day (our recommendation), or must the new reports keep the current results?
Keep the current results: the end date is compared at midnight, as in the original. (Equivalence with the original comes first in this demo; a later change order may change it.)
answered: 2026-10-16
by: you

## Q-reports-003
question: The sales reports and the catalog's lifetime sales count every order whatever its status, including orders that are not yet invoiced or shipped. Is that intended, or should some statuses be excluded?
Intended: keep counting every order whatever its status.
answered: 2026-10-16
by: you

## Q-reports-004
question: The quarterly product report's chart is fed by the monthly figures, not the quarterly ones. Its quarter labels put the quarter before the year, so a sort on the label would mix years. Should the new report chart quarterly figures and sort quarters by year then quarter?
Keep the figures the original shows (the chart fed by the monthly figures); sort quarters by year then quarter, since the original order is only a display artefact.
answered: 2026-10-16
by: you

## Q-reports-005
question: The catalog is titled as a seasonal (fall) catalog and needs a full PDF render to fill its table of contents. Is one catalog with a generated table of contents enough, or do you need several seasonal editions, and must the catalog be stored after it is generated?
One catalog with a generated table of contents is enough; it is generated on demand and not stored.
answered: 2026-10-16
by: you

## Q-reports-006
question: The catalog shows a placeholder picture for each category, and the code for real category images is switched off. Should the new catalog show the category images stored with the product categories?
Keep the original: the placeholder picture per category.
answered: 2026-10-16
by: you

## Q-reports-007
question: A saved query totals each salesperson's sales for the current calendar year, without discounts, but nothing in the application uses it. May we drop it, or is a year-to-date figure per salesperson needed elsewhere, for example on a dashboard?
Drop it; no year-to-date figure is needed elsewhere.
answered: 2026-10-16
by: you
