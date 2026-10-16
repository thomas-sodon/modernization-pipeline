# Viability plan: AI-assisted modernization of small-business legacy software (US, Django)

## Context
The user is an experienced developer working on this full-time, betting long-term, and selling in the **US**. They know **Django** best and are **starting with no network**. From five researched ideas they chose this one:

> Fixed-price migration of small and mid-size businesses' MS Access databases, Excel VBA workbooks and VB6 apps to modern web apps. It starts as a service and becomes a tool later.

The user's open questions were:
- What exactly are we migrating into?
- Where do customers come from?
- Why would a stranger be trusted with a business-critical system?

This plan answers all three and sets go/no-go gates so a no-go costs about 4–6 months, not years.

## 1. Thesis
Thousands of US small businesses run core operations on Access, VBA or VB6 apps that nobody understands anymore. Their options today:
- **Manual consultancies:** about $15–50k and 3–4 months.
- **Enterprise tools:** GAPVelocity outputs .NET only, priced per line of code.
- **Microsoft's Dataverse path:** moves the data; the logic has to be rewritten.

**The offer:** recover the business rules first, then deliver a standard Django web app that is *proven* to behave the same. Proof comes from characterization tests: tests built from the client's real data that record what the old app currently does. Fixed price, about half what consultancies charge, delivered in weeks, and the client owns all the code.

## 2. Evidence (research, Oct 2026)

**Demand**
- Third-party trackers count 30k–109k companies using Access; the counts are inconsistent.
- VBA and VB6 use is under-counted because it is internal.

**Triggers**
- Office LTSC 2021, including Access, reached end of support on 13 Oct 2026.
- Office blocks macros in files from the internet by default.
- VB6's development environment is unsupported on Windows 11.
- Access developers are retiring.

**Gap**
- GitHub Copilot app modernization doesn't cover VB6, VBA or Access.
- Devin/Cognition and Google target mainframes and COBOL.
- AWS Transform custom targets enterprise pipelines.
- GAPVelocity is .NET-only and aimed at enterprises.
- No AI startup focused on Access or VBA was found.

**Price anchors**
- Agency quotes are $15–50k for an Access-to-web migration (mostly one source, unverified).
- Freelancers charge $40–120/h.

**Weak spots**
- Market-size data is vague.
- The price anchors are thin.
- Hidden business logic makes fixed price risky.
- Clients could do it themselves with AI coding agents.
- **The user starts with no network, so the sales ramp is the biggest risk.**

## 3. What we migrate into (the standard target)
**Stage 1: move the data off the Access file** ($2–5k, 1–2 weeks)
- **What it is:** move tables to **Postgres**, or to **SQL Server/Azure SQL** for clients already on Microsoft 365. The Access screens keep working through ODBC links, so users see no change.
- **Tools:** Microsoft's free SSMA for Access handles SQL Server; for Postgres, use psqlODBC plus scripted schema and data migration.
- **What it fixes right away:** the 2GB limit, corruption, slowness with several users, and no backups.
- **Why sell it:** a cheap, low-risk first purchase that builds trust.

**Stage 2: replace the screens with a web app** ($8–30k)
- **Framework:** Django 5.x LTS with HTMX for interactive forms. Use Django admin for internal maintenance screens and custom views for the main workflows.
- **Database:** Postgres by default. Use **mssql-django** (Microsoft-maintained) when the client's database is SQL Server.
- **Reports:** Access reports become HTML/PDF via WeasyPrint plus Excel exports via openpyxl, because users still want "the Excel".
- **Background jobs:** cron or Celery-lite (django-q2) for macros and scheduled VBA.
- **Excel VBA workbooks:** a small Django app on the same stack, or a scheduled Python job if no screen is needed.
- **Hosting:**
  - Clients on Microsoft: the client's own Azure App Service.
  - Otherwise: a managed host such as Render, Fly or DigitalOcean, run by you on the care plan.
  - Code is always in the client's own GitHub org.
- **Default build:** the same project template for every client: auth with Microsoft 365 single sign-on via django-allauth or MSAL, audit log, backups, CI and docs. Reuse is how margins grow.
- **Offered on request only** (not the default): Power Apps, Retool-style builders, .NET.

## 4. Delivery pipeline (becomes the product)
1. **Extractor:** dumps an `.accdb`/`.mdb` file's schema, queries, forms, reports, macros and VBA to text, using mdbtools or Jackcess, plus the VBA source exported from Access itself. For `.xlsm` workbooks, use oletools/olevba.
2. **Rule recovery:** an LLM pass turns VBA, form events and queries into a readable business-rules spec that the client signs off.
3. **Characterization tests:** run queries and reports against a snapshot of the data and save the outputs as golden tests that the Django app must pass under pytest.
4. **Generation:** an agent writes models, migrations, views, forms and reports into the standard template. A human reviews every change.
5. **Equivalence report and handover:** test pass rate, known differences, documentation and a runbook. This is what the client is paying for.

**Data rule:** only code and schema go to model APIs. Client data stays on the client's systems or in an encrypted local copy, and is anonymised for any AI step. Confirm Anthropic's commercial and zero-retention terms first.

## 5. Customers: a cold-start plan for the US with no network
Buyers buy at a moment of pain, not because the product is "modernization". The typical moments:
- The person who built the app is leaving.
- The file corrupts.
- Staff need remote access.
- An auditor or cyber insurer flags unsupported software.
- The company moves to Microsoft 365.

Channels, in order of when to start:
1. **Upwork (from week 2):** use it to build a track record, not as the main revenue source.
   - **Volume:** probably 10–40 open Access jobs at any time, plus some VBA ones (live counts not confirmed). Some postings accept only US freelancers, e.g. one at $35/hr.
   - **Job size:** most are small fixes or enhancements ($100–2k). Larger migrations are rare on marketplaces.
   - **Cost:** a 0–15% fee per contract (about 10% is typical), and about $0.30–2.40 in Connects (Upwork's bidding credits) per proposal.
   - **Tactics:**
     - Win 2–3 small jobs for reviews.
     - Offer a fixed-price "assessment + migration plan" for $300–800; some clients already ask for this.
     - Link the public Northwind demo in every proposal.
   - **Skip:** Freelancer.com and Fiverr (sellers at $15–20/hr), and Toptal (its 2–5 week screening is too slow).
   - **Access community:** Help4Access (claims 250+ US consultants) and the 599cd Developer Network are possible sources of subcontracted overflow work.
2. **Job postings as buying signals (from week 3):** about 600 "Microsoft Access Developer" results on CareerBuilder and 287 remote Access jobs on Jobgether (signals only; many are generic roles). Companies advertising "MS Access developer", "VBA analyst" or "Access database administrator" on Indeed or LinkedIn have the problem now. Email the hiring manager or owner: "Instead of hiring someone to maintain it, replace it for a fixed price." Aim for 20 personalised emails a week.
3. **MSPs, the repeatable channel (from week 4):**
   - **Who:** 150 small MSPs in 3–5 US metros plus remote-friendly ones.
   - **Where to find them:** r/msp, CompTIA ISAO and community groups, and ASCII Group.
   - **Offer:** a 10–15% referral fee, or a white-label service. Give them the free health-check scanner to run on their clients' servers.
   - **Target:** 5 active MSP partners by month 6.
4. **Retiring Access developers (from month 2):** find them in Access user groups, on LinkedIn and on Clutch. Offer to take over their clients or pay them a referral fee.
5. **Content plus a free scanner (ongoing):**
   - A "Free Access app health check" page: upload or run locally, get a report on size, risk, complexity and an estimated migration price.
   - Articles on common searches: "Access too slow multiple users", "Access 2GB limit", "convert Access database to web app cost".
6. **Accountants and fractional CFOs (month 3+):** they see the business-critical Excel workbooks.
7. **Community presence (ongoing):**
   - Answer questions on r/MSAccess (~14k members) and AccessForums.
   - Present at Access user group meetings, which are monthly and online.
   - Offer guest posts to Access blogs such as nolongerset and devhut.

## 6. Trust: built into the offer
- **Small first step:** a free health-check report, then a $1.5–3k paid assessment that is credited toward the migration. They see that you understand their app before committing.
- **The old app keeps running in parallel** until they sign off. There is no big-bang switch.
- **Payment tied to proof:**
  - Milestones: 30% at start, 40% when the app runs on their test data, 30% when the equivalence tests pass and they accept it.
  - Any mismatch with the agreed spec is fixed at no cost.
- **No dependence on you:** standard Django, code in their own GitHub, documentation and a runbook. Any Django developer can take over. Say this explicitly; it answers "what if you disappear".
- **Visible proof:**
  - Public before-and-after migrations of Microsoft's Northwind and 1–2 community sample databases, each with a sample equivalence report.
  - A short video walkthrough.
  - Case studies and Upwork reviews.
- **Borrowed trust:** a recommendation from the client's MSP or retiring Access developer is worth more than any website.
- **Look like a real business:**
  - An LLC.
  - $1M E&O plus cyber insurance.
  - A master services agreement (MSA), statement of work (SOW) and data processing agreement (DPA).
  - A clear one-page data policy, a real website with your name and face, and a LinkedIn presence.
- **Pitch the outcome, not the AI:** "Your system, modernized, proven identical, for about half the usual price, and you own it." AI is how you deliver that, not what you sell.

## 7. Offer and pricing (USD)
| Tier | Scope | Price | Target effort |
|---|---|---|---|
| Health check | Automated scan and a 1-page report | Free | <1 h |
| Assessment (credited toward the migration) | Inventory, keep/drop list, rules spec, fixed quote | $1.5k–3k | 8–15 h |
| Stage 1: data off the Access file | Move tables to Postgres or SQL Server, re-link the Access screens, backups | $2k–5k | 12–30 h |
| Stage 2: small web app | 1 app, up to ~20 forms and reports | $8k–15k | 50–90 h |
| Stage 2: standard web app | Several modules, integrations | $18k–35k | 110–200 h |
| Care plan | Hosting, fixes, small changes | $200–600/mo | 1–4 h/mo |

**Target:** an effective rate of at least $130/h by project 5. Expect $60–90/h on the first Upwork projects.

## 8. Validation timeline and gates (adjusted for no network)
| When | Work | Gate |
|---|---|---|
| Weeks 1–4 | Set up the LLC, insurance and contracts. Build the standard Django template, extractor and test harness. Migrate Northwind end to end. Build the website, scanner MVP and case-study page. Start Upwork bids in week 2. | Northwind migrated with ≥90% golden tests passing in ≤40 h; ≥1 Upwork job won |
| Weeks 4–12 | Outreach: Upwork, 20 job-signal emails a week, 150 MSPs contacted, community posts | ≥30 conversations; ≥3 paid assessments or Stage 1 projects; ≥2 MSP partners |
| Months 3–6 | Deliver the work and convert it into Stage 2 projects | ≥2 Stage 2 projects signed; hours within 30% of estimates; ≥1 MSP referral that closed |
| Month 6 | Review: effective rate, sales cycle, best channel | ≥$100/h and a pipeline of ≥$40k → go all in and start productizing |

**Kill or pivot if, by month 6:**
- Fewer than 3 paid engagements came from 50+ conversations.
- Clients refuse fixed prices above $5k.
- Delivery takes more than 2x the estimate even with tooling.

**Pivot options:**
- **Sell to consultants:** sell the extractor and test harness to Access consultants.
- **Narrow the scope:** Stage 1 database moves only, sold through MSPs.
- **Narrow the input:** Excel VBA workbooks only.

## 9. Financial model
**Runway**
- Plan for 6–9 months of personal expenses. With no network, meaningful revenue likely starts in months 3–5.

**Year 1 (conservative)**

| Revenue line | Estimate |
|---|---|
| ~6 Upwork and small jobs | ~$10k |
| 8 assessments or Stage 1 projects | ~$20k |
| 4 Stage 2 projects averaging $14k | ~$56k |
| Care plans | ~$5k |
| **Total** | **≈ $90k** |

**Year 2**
- With MSP channels and tooling: about 15 Stage 2 projects plus a recurring care base ≈ $250–300k.

**Costs**

| Cost | Estimate |
|---|---|
| E&O and cyber insurance | $1.5–3k/yr |
| LLC, legal templates | $1.5–3k one-time |
| AI/API | $100–400 per project |
| Hosting and tools | ~$200/mo |
| Upwork fees | 10–15% of Upwork revenue |

**When to productize**
- After ~10 projects, if the pipeline handles most of the delivery work:
  - Sell scanner reports for $99–499.
  - License the toolkit to other consultancies and MSPs.

## 10. Key risks and mitigations
| Risk | Mitigation |
|---|---|
| No network → slow sales | Upwork and job-signal outreach first; MSP channel; set a runway; month-6 gate |
| Hidden logic breaks the fixed price | Paid assessment; signed rules spec; change orders for anything new |
| Liability | Liability capped at the fee; acceptance means passing the agreed tests; E&O insurance |
| Clients go DIY with AI agents | Sell the proof, handover and accountability, not the code conversion; target owners who aren't technical |
| Microsoft or AWS move down-market | Stay service-led and relationship-led through MSPs |
| Data privacy | Only code and schema go to AI; data stays with the client; DPA |

## 11. Public demo: proof of the delivery pipeline
**Demo inputs (chosen from research)**
- **Access: Microsoft Northwind 2.0 Developer Edition** (Apr 2023, v2.2).
  - **Size:** 28 tables, 65 queries, 40 forms, 15 reports, 18 VBA modules and about 5,800 lines of VBA, plus 75 data-macro files.
  - **Workflows:** order → invoice → shipping, inventory, purchase orders, login.
  - **Why it works as a demo:** buyers recognise it, and it is realistic enough to show off the pipeline.
  - **Download:** `https://binaries.templates.cdn.office.net/support/templates/en-us/tf22238896_win32.accdt` (12.9 MB).
  - **Key finding:** an `.accdt` file is a zip of plain text (table schemas as XSD, data as XML, forms, reports, queries and modules as Access SaveAsText files, data macros as `.axl`). That means the **whole app can be parsed on Linux** with no Access install.
  - **License:** Microsoft publishes none. **Never commit or redistribute the `.accdt`, its data or verbatim VBA.** A script downloads it locally, and only the Django output and analysis are published. Get a quick legal check before marketing it.
- **Excel: `MaxineXiong/InvoiceGenerator`** (MIT).
  - Contents: an `.xlsm` workbook with userforms, modules, PDF/Excel invoices, Outlook drafts and a dashboard.
  - Its VBA is already exported in the repo. Extract it with oletools/olevba.
- **Fallbacks with clean licenses:**
  - `cod-e-ash/SalesInvoice` (Apache-2.0): has both an `.accdb` and an `.xlsm`.
  - Classic Northwind schema and data from `microsoft/sql-server-samples` (MIT).

**Extraction from real client `.accdb` files (beyond the demo)**
- **Linux:** mdbtools or Jackcess cover tables, data and query SQL; `jetdb` may also read VBA (unverified).
- **Windows + Access:** forms, reports and VBA need msaccess-vcs-addin. Budget for a Windows VM build box.

**Demo deliverables (all in this repo)**
1. `extractor/`: Python CLI. Unzips `.accdt`, parses tables, queries, forms, reports and modules into a JSON inventory, and produces a complexity report. The same code powers the free health-check scanner later.
2. `analysis/`: generated business-rules spec (Markdown) for Northwind's order, inventory and PO workflows.
3. `tests/golden/`: characterization tests (recordings of what the old app does).
   - Northwind's saved queries are run against its sample data in SQLite or Postgres. The SQL is translated from Access dialect, and the outputs are recorded as golden fixtures.
   - VBA rules (e.g. order status transitions, inventory checks) are encoded as expected-behavior tests taken from the spec.
4. `northwind_django/`: the standard Django 5.x LTS project template.
   - Stack: Postgres, HTMX, Django admin, WeasyPrint PDF reports, openpyxl exports, login.
   - It implements Orders, Inventory, Purchase Orders and the key reports.
   - pytest runs the golden tests against it.
5. `docs/equivalence-report.md`: pass rate, known differences, before/after screenshots.
6. `docs/software-modernization-viability.md`: this plan.
7. Phase 2: the same flow for the InvoiceGenerator workbook.

## 12. Verify before committing
- Get real E&O and cyber insurance quotes.
- Check Anthropic API retention and zero-retention terms.
- Check how mdbtools/Jackcess handle forms and reports. The VBA source and form/report definitions may need export via Access's automation interface on a Windows VM: plan for a Windows build box.
- Validate prices in the first 5 discovery calls.

## Next steps: building the demo (not started)
1. **Commit 1:** `docs/software-modernization-viability.md` (this plan, polished, with source links) plus `README.md`.
2. **Commit 2:** `extractor/`, a Python 3.12 package.
   - `fetch_northwind.py`: downloads to `data/`, which is in `.gitignore`.
   - `accdt.py`: unzips and parses the XSD, XML and SaveAsText formats.
   - `inventory.py`: produces the JSON inventory and complexity report.
   - Includes unit tests.
3. **Commit 3:** `analysis/northwind-rules.md`, the business-rules spec, generated from the extracted VBA and queries and then reviewed.
4. **Commit 4:** `northwind_django/` plus the golden characterization tests.
5. **Commit 5:** the equivalence report.
6. Push after each milestone with `git push -u origin claude/sweet-pasteur-dw6ykm`, retrying on network errors. No PR unless asked.

## Verification
- `python -m extractor inventory data/northwind.accdt` prints counts that match what the research measured: 28 tables, 65 queries, 40 forms, 15 reports and about 5,800 VBA lines.
- `pytest` passes the extractor unit tests and the golden tests against the Django app.
- `python manage.py runserver` runs. Screenshots of the order → invoice → ship flow come from Playwright, using the preinstalled Chromium.
- `git ls-files` contains no Microsoft `.accdt`, data XML or verbatim VBA.
