# Canonical output

The form in which a unit's result is recorded as a golden and compared by the harness. The
contract is docs/contracts.md#canonical-output (`canonical_version 1`); the one implementation
is `harness/canonical.py` (standard library only, vendored into the target repo with the rest of
the harness). `toolkit/golden.py` (`golden record`) and the harness compare step both import it,
so the recorder and the comparer format and order rows the same way.

## Where it is used

| Output | Form | Digest |
|---|---|---|
| Seed goldens, `goldens/synthetic/seed-N/expected/<file-safe id>.csv` (committed) | CSV below | `sha256` |
| Engagement goldens, `<private>/goldens/engagement/<file-safe id>.csv` (data zone) | CSV below | `hmac-sha256` |
| Document units (`.json` instead of `.csv`) | canonical JSON below | as above |

The manifest (`goldens/manifest.json`, docs/contracts.md#goldens-manifest) records per unit the
column names and neutral types (`columns`), the ORDER BY columns (`order_by`) and, per dataset,
the row count and the digest of these bytes. It never holds values.

The file-safe id is the unit id with each character outside `[A-Za-z0-9._-]` replaced by `_`,
plus `-` and the first 8 hex digits of sha256(id) when anything was replaced
(`Order Details` becomes `Order_Details-fe3ac0d4`). It is the rule `reference sql` uses.

## CSV

- UTF-8 without BOM, RFC 4180: comma separator, CRLF after every record, a field is quoted with
  `"` only when it holds a comma, a `"`, a CR or an LF, or is empty; a `"` inside is doubled.
- The first record is the header: the unit's output column names, in output order (NFC).
- NULL is the unquoted `\N`. An empty string is `""`. A text whose whole value is backslashes
  followed by `N` gets one more leading backslash (`\N` as text is written `\\N`).
- A unit with no rows is its header line alone (status `empty` in the manifest).

## Values by neutral type

Each value is formatted by its column's neutral type (manifest `columns`), never by its Python
type. A value that does not fit the type (a fraction in an integer column, text in a number
column, NaN or infinity, a time zone) raises `CanonicalError`; the harness reports it as a test
error and never casts silently. The error text never contains the value.

| Type | Rule | Value in | Canonical text |
|---|---|---|---|
| `byte`, `integer`, `longinteger`, `autonumber` | plain integer, no leading zeros, `-0` is `0`; integral floats and decimals are accepted | `-0`, `7.0` | `0`, `7` |
| `currency` | exactly 4 decimal places, half-even rounding | `12.5`, `1.00015` | `12.5000`, `1.0002` |
| `decimal` | as currency | `1.00005`, `-0.00001` | `1.0000`, `0.0000` |
| `single` | half-even to 6 significant digits, plain notation, trailing zeros and `.` removed | the binary32 value of `0.1`; `1234567` | `0.1`; `1234570` |
| `double` | half-even to 12 significant digits, plain notation, trailing zeros and `.` removed | `0.1 + 0.2`; `1/3` | `0.3`; `0.333333333333` |
| `datetime` | `YYYY-MM-DDTHH:MM:SS`, no zone, rounded to the second; a date is midnight; a time-only Access value keeps its day 1899-12-30 | `2024-03-01 09:30:05.6`; time `14:05` | `2024-03-01T09:30:06`; `1899-12-30T14:05:00` |
| `yesno` | `1` or `0`; Access True (-1) is `1` | `-1`, `False` | `1`, `0` |
| `text`, `memo`, `hyperlink` | the text in Unicode NFC, otherwise unchanged (spaces kept) | `Cafe` + U+0301 | `Café` (U+00E9) |
| `guid` | lower case `8-4-4-4-12`, no braces | `{1234ABCD-...}` | `1234abcd-...` |
| any | NULL | `None` | `\N` |

`complex` and `oleobject` columns have no canonical form (the reference databases hold them as
all-NULL memo placeholders, so they arrive as `memo` with `\N`).

Floats are first read through their shortest round-trip decimal (Python `repr`), then rounded,
so a binary32 value read as a double (`0.10000000149011612`) still gives `0.1`.

## Row order

Ordering is normalised, not checked: a rule that depends on visible order gets a rule test or an
e2e test.

- **The unit has an ORDER BY on output columns** (manifest `order_by`, a list of
  `{column, desc}`): rows are sorted by those columns with a typed compare, then ties are broken
  by the full canonical row tuple.
  - NULL sorts first; `desc` reverses the column, NULL position included (NULL last).
  - Numbers (the integer types, currency, decimal, single, double, yesno) compare numerically.
  - Datetimes compare chronologically (their canonical text does).
  - Text compares by code point of its NFC form.
- **Otherwise** rows are sorted by the full canonical row tuple: the field strings, compared field
  by field in UTF-8 byte order (so `10` sorts before `9`).

`golden record` derives `order_by` from the unit's outermost ORDER BY: each term that names an
output column (an alias, the same expression as a select item, a `Table.Column` whose column is
output, or a position) in order, stopping at the first term that does not. A unit whose ORDER BY
names no output column, or that has none, gets `order_by` null.

Example: `columns` `ItemID longinteger, Label text, Price currency`, `order_by`
`[{"column": "Price", "desc": true}]`, rows `(9, "b", 1)`, `(10, "a", 1)`, `(2, "c", NULL)`,
`(3, "d", 0.5)`:

```
ItemID,Label,Price
10,a,1.0000
9,b,1.0000
3,d,0.5000
2,c,\N
```

The two `1.0000` rows tie and are ordered by the row tuple (`10` before `9` as text). Without
`order_by` the same rows give `10,a,...`, `2,c,...`, `3,d,...`, `9,b,...`.

## Documents

Document units (docs/contracts.md#documents) are one JSON object keyed by document key, each key
holding one object whose leaves are canonical strings (the value rules above: dates as datetime,
amounts as currency). The bytes are
`json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` plus a newline. The
manifest's `rows` for a document unit is its number of keys.

```json
{"15001":{"lines":[{"amount":"100.0000","date":"2024-02-20T00:00:00","service":"Design work"}],"net":"100.0000","total":"118.0000","vat":"18.0000"}}
```

A leaf that is not a string raises `CanonicalError`. Document goldens are always JSON, never PDF
or xlsx.

## Digests

`digest(data, key=None)` is the sha256 hex digest of the canonical bytes, or the HMAC-SHA256 hex
digest when a key is given.

- Seeds: `sha256`. Anyone can recompute it from the committed file.
- Engagement: `hmac-sha256` with the key
  `HMAC-SHA256(key=b"modpipe-golden-v1", message=<bytes of the engagement data file>)`
  (`key_from_file(path)`), where the file is `source.data_copy` when set, else `source.path`, and
  `<private>/datasets/engagement/dataset.json` when that is a folder. Anyone with the data file
  can re-verify; nobody else can enumerate small outputs. The key is computed in memory, passed to
  the harness in `EQUIVALENCE_DIGEST_KEY` and never printed or written.

## Functions

| Function | Returns |
|---|---|
| `format_value(ctype, value)` | the canonical text of one value |
| `canonical_rows(columns, rows, order_by=None)` | formatted row tuples in canonical order |
| `canonical_csv(columns, rows, order_by=None)` | the CSV bytes, header included |
| `canonical_document(doc)` | the canonical JSON bytes of a document unit |
| `digest(data, key=None)` | sha256 or HMAC-SHA256 hex |
| `key_from_file(path)` | the engagement digest key (bytes) |
| `file_safe(name)` | the file-safe id |
| `unescape(text)` | the value a canonical text field stands for (None for `\N`) |

`columns` is a list of `{name, type}` (as in the manifest) or `(name, type)` pairs; `rows` are
sequences in column order.
