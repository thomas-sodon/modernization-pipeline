_Generated from analysis/rules.json by `toolkit rules-render`. Do not edit by hand._

# Keep and drop list

Engagement `booklend-demo`, spec version 1.0.

## Keep

| type | name | area | target component |
|---|---|---|---|
| query | qryAvailableBooks | catalog | catalog.saved_queries |
| table | Book | catalog | legacy.models |
| data_macro | Loan.BeforeChange | lending | lending.models |
| form | frmLoan | lending | lending.views |
| procedure | modLoans.CheckOutBook | lending | lending.services |
| query | qryMemberFines | lending | lending.saved_queries |
| query | qryOverdueLoans | lending | lending.saved_queries |
| report | rptOverdueNotice | lending | lending.reports |
| table | Fine | lending | legacy.models |
| table | Loan | lending | legacy.models |
| table | Member | lending | legacy.models |

## Drop

| type | name | area | reason |
|---|---|---|---|
| query | qryLoanCopy | lending | duplicate of qryOverdueLoans |

## Out of scope

| type | name | area | reason |
|---|---|---|---|
| macro | mcrNightlyCleanup | housekeeping | nightly compaction job; the database server replaces it |
| query | qryYearlyStats | housekeeping | yearly statistics, not requested |
