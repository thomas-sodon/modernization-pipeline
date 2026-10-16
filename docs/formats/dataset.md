# The dataset directory

A dataset directory is the one format in which table data moves through the pipeline. The
engagement's own data, the synthetic seeds, the reference engine's inputs, the goldens, the Stage 1
load and the Django importer all read and write it, so nothing downstream needs to know whether the
data came from an Access template, an exported folder, a database file or a workbook. The contract
summary is docs/contracts.md#dataset-directory; the machine-checked shape of `dataset.json` is
`toolkit/schemas/dataset.schema.json`.

## Where datasets live

| Dataset | Path | Written by |
|---|---|---|
| Engagement data | `<private>/datasets/engagement/` (data zone) | `mp-data dataset export <eng> --out <private>/datasets/engagement` |
| Synthetic seeds | `<target>/goldens/synthetic/seed-N/input/` | S2.synth |

The engagement dataset holds client data. Only mp-data writes or reads it, and it never leaves the
data zone. Seeds are synthetic and committed.

## Layout

```
<dir>/
  dataset.json
  tables/
    Customers.csv
    Order_Details-fe3ac0d4.csv
```

Each table's file name is its file-safe id (docs/contracts.md#conventions): any character outside
`[A-Za-z0-9._-]` becomes `_`, and when something was replaced, a `-` and the first 8 hex digits of
the SHA-256 of the name are added. The original name stays in `dataset.json`.

## dataset.json

Written like every other tool JSON file: sorted keys, two-space indentation, UTF-8, final newline.

| Key | Meaning |
|---|---|
| `format_version` | `1`. Readers refuse any other version (exit 2). |
| `name` | `engagement`, or `seed-N` for a synthetic seed. |
| `kind` | `engagement` or `synthetic`. |
| `seed` | N for a seed, `null` for the engagement. |
| `schema_sha256` | SHA-256 of the bytes of the model `schema.json` the dataset was built against. |
| `load_order` | Every table in the dataset, parents before children. |
| `tables` | One entry per table, in load order (below). |

A table entry has `name`, `file` (`tables/<file-safe name>.csv`), `rows` (data rows, header not
counted), `sha256` (of the CSV file's bytes), `columns` (`name` and neutral `type`, in schema order)
and `omitted_columns` (`name`, `type`, `reason`).

### Load order

Tables are ordered so that the table a relationship points to comes before the table that points
to it. When several tables are free at the same time, the one with the smallest name goes first,
so the order never depends on the source. If the relationships form a cycle, the remaining table
with the smallest name is placed next and the export reports it ("cycle broken at ..."). A table
that refers to itself (an employee's supervisor) is no ordering constraint; the export counts such
tables as `self_references`, and loaders insert those rows in an order that satisfies the
reference or defer the constraint.

### Omitted columns

Some columns cannot be expressed as text and are left out of the CSV, but listed so a reader knows
they exist:

| reason | Which columns |
|---|---|
| `complex` | Access multi-value and attachment columns (neutral type `complex`). |
| `oleobject` | OLE object columns. |
| `computed` | Calculated columns of an Excel table (the model marks them `computed: true`); the app recomputes them. |

## CSV files

- UTF-8 without a byte order mark; RFC 4180 with a comma separator and CR LF after every record,
  the last one included.
- The first record is the header: the column names exactly as in the schema, in schema order.
- A field is quoted only when it has to be: when it holds a comma, a double quote, a CR or an LF
  (double quotes inside are doubled), and when it is the empty string, which is always written as
  `""`. That keeps a one-column row holding an empty string from turning into a blank line.
- NULL is the unquoted two characters `\N`. The empty string and NULL therefore never look alike.
- A text whose whole value is a run of backslashes followed by `N` (`\N`, `\\N`, ...) is written
  with one more leading backslash, and readers remove one. Any other backslash is literal.

### Values by neutral type

`harness.dataset.format_value(type, value)` is the single implementation of these rules; the
writer, the validator and stage1's per-table digests all call it.

| Type | Text form |
|---|---|
| `text`, `memo`, `hyperlink` | As stored (a hyperlink keeps Access's `display#address#...` form). |
| `byte`, `integer`, `longinteger`, `autonumber` | A plain integer: no sign for positives, no leading zeros, no fraction. Out-of-range values are errors. |
| `currency`, `decimal` | A plain decimal with `.`: no exponent, no thousands separator, trailing fractional zeros dropped, zero written `0` (so `12.5000` becomes `12.5`). |
| `single` | The shortest decimal that reads back to the same IEEE binary32 value, written the way Python writes floats (`0.1`, `0.33333334`, `-0.0`). |
| `double` | Python's `repr` of the value (`0.30000000000000004`, `1e-07`). |
| `datetime` | `YYYY-MM-DDTHH:MM:SS`: no zone, no fraction (rounded to the nearest second); a date alone gets `T00:00:00`. |
| `yesno` | `1` or `0`. |
| `guid` | Lower case `8-4-4-4-12`, no braces. |
| `complex`, `oleobject` | Never written; see omitted columns. |

NaN and infinity are errors for every numeric type. A value that does not fit its type stops the
export; the message names the table, column and row number, never the value.

Readers (`read_dataset`, `iter_table`) return typed rows as dictionaries keyed by column name:
`str` for text types and guid, `int`, `decimal.Decimal` for currency and decimal, `float` for
single and double, `datetime.datetime`, `bool` for yesno, and `None` for NULL.

## Exporting the engagement data

`toolkit dataset export <eng> --out DIR [--check]` runs only through mp-data. It reads the model
`schema.json` for tables, columns and types, and the source through the toolkit readers:

- **Access template (`.accdt`)**: the sampleData part of each table.
- **vcs-export folder**: the table data files under `tables/`. These are text, so an empty field
  of a non-text column is NULL.
- **Workbook (`.xlsm`)**: the cached values of each Excel table. Tables whose role is
  `documentation` are left out, and calculated columns are omitted as `computed`.
- **`source.data_copy`**: a copy of the application that holds its data. A copy that is itself a
  dataset directory is read as one; an Access file (`.accdb`, `.mdb`), like an `.accdb` or `.mdb`
  source, goes through the Jackcess bridge that S2.reference provides. Until it exists, the export
  exits 2 with `data_copy needs the Jackcess bridge from S2.reference`.

For text sources (accdt, vcs-export), an element that is missing from a row is NULL and an
element that is present but empty is the empty string. Fields that match no column of the schema
are counted (`unknown_columns`), and a row in which no field matches a column at all stops the
export with exit 1, because the reader evidently returned something other than rows.

Special cases:

- **No data in the source**: an accdt or vcs-export source not saved with data and without a
  `data_copy` gives a `dataset.json` with no tables, exit 0, status `ok` and the message `no data
  in the source`. A health check can still pass on a design-only file; S2 and S3 refuse such an
  engagement.
- **Back ends**: tables linked to the one data back end are read from the received file
  (`source.data_copy` or a `source.backends` entry) through `toolkit.dataset.open_backend`.
  Tables linked to more than one back end make the export exit 2 with `more than one data back
  end: custom quote`. When the back end was not received, its tables are left out of `tables`
  and `load_order`, the rest is written, and the export exits 0 with status `no-data` and a
  message giving the number of skipped tables.

The export builds the new dataset in a temporary directory next to DIR, validates it, and only
then renames it over DIR, so a failed export leaves the previous dataset untouched. With `--check`
it builds into a temporary directory, compares it with DIR file by file and exits 1 on any
difference (a missing DIR counts), writing nothing.

The summary line holds only totals (tables, rows, omitted columns, skipped tables, broken cycles,
self references, unknown columns and, under `--check`, the number of differing files) and fixed
messages: no values, no data-zone paths, and no table or column names. mp-data withholds any
summary that contains one of the engagement's data strings anywhere in it, and a long line of
design names could contain one by chance. The per-table detail (name, row count, SHA-256, column
names, and the files that differ under `--check`) goes to stderr, which mp-data keeps in the
call's log inside the data zone; `dataset.json` itself carries the same counts and hashes.

## Validating a dataset

`toolkit dataset validate DIR --schema schema.json` is for seeds and fixtures (the engagement
dataset is checked by `export --check` inside mp-data). It exits 2 when `dataset.json` or the
schema is missing, and 1 when any of these fails:

- `dataset.json` against `toolkit/schemas/dataset.schema.json`;
- every schema table is present, except tables whose role is `documentation` (they must be
  absent) - this is how seeds of a workbook source look;
- every column is listed with the schema's type and in schema order, except omitted ones; an
  omitted column must be `complex` or `oleobject` with the matching reason, or `computed` in the
  schema; omitting an ordinary column is an error;
- each CSV: file name, SHA-256, no BOM, UTF-8, header, field count, every field in the canonical
  text form of its type, the quoting and CR LF rules (the file must equal its own re-encoding),
  and the row count;
- no foreign-key orphans in enforced relationships between tables of the dataset (a key with a
  NULL part is not checked);
- `load_order` lists exactly the dataset's tables in the order described above.

A `schema_sha256` that differs from the `--schema` file is reported but is not a failure.

## Python API

`harness/dataset.py` uses only the standard library and imports nothing from the toolkit, so
S2.harness can vendor it into the delivered app for `import_dataset` and `load_dataset`:

- `format_value(type, value)` and `parse_value(type, text)`;
- `write_dataset(dir, meta, tables)`: `meta` has `name`, `kind`, `seed`, `schema_sha256` and
  optionally `load_order`; each table has `name`, `columns`, `omitted_columns` and `rows` (dicts
  by column name, or sequences in column order). Returns the `dataset.json` it wrote;
- `read_dataset(dir)`: `(dataset.json, {table: iterator of typed rows})`, in load order;
- `validate(dir, schema, allow_absent=())`: `{"problems": [...], "counts": {...},
  "schema_sha256_matches": bool}`;
- `load_order(schema, names)`, `file_safe(name)`.

`toolkit.dataset` re-exports these and adds the source adapters, `export(eng, dir)` and
`open_backend(path, schema)`, a reader with `tables()` and `iter_rows(table)` for an Access back
end at any path (stage1 `--dataset fresh-copy` uses it).
