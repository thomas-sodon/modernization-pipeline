# The object model (`out/model/`)

The object model is the neutral, scrubbed description of a legacy application's design. `toolkit
parse` writes it; every later stage reads it instead of the source file. The contract summary is
docs/contracts.md#object-model, the counting rules are docs/contracts.md#inventory, and the
machine-checked shape is `toolkit/schemas/model.schema.json` (one `$defs` entry per file).

It lives in the design zone at `<private>/out/model/`:

| File | Holds |
|---|---|
| `schema.json` | tables (Access tables or Excel ListObjects), their columns, keys, indexes and relationships |
| `objects.json` | everything else in the design: queries, forms, reports, macros, linked tables, workbook sheets and layout, plus facts and parse diagnostics |
| `procedures.json` | VBA modules and the procedures found in them |
| `datamacros.json` | Access table data macros |
| `code/<module>.txt` | each module's VBA after scrubbing |

The model never holds data. Rows, cell values, cached formula results and comment texts are not
design; they stay in the source (read only through a reader's `iter_rows` and `iter_texts`) or,
for workbooks, in the private `workbook/` folder of the data zone.

## Conventions

- JSON is written with sorted keys, two-space indentation, UTF-8 without a BOM and a final LF
  newline. Lists of named things at the top of a file are sorted by name; lists whose order
  carries meaning (columns, controls, sections, macro actions, formula cells) keep source order.
  Two runs over the same source and the same scrub map produce byte-identical files.
- Every file has `"format_version": 1`. `schema.json` and `objects.json` also carry
  `source_kind` (`accdt`, `vcs-export`, `accdb`, `mdb` or `xlsm`).
- A value the source does not provide is `null`; a list with nothing in it is `[]`.
- Sizes and positions of Access forms and reports are integers in twips (1/1440 inch).
  Userform positions from an exported `.frm` are numbers in points, as the export writes them.
- Code files use the file-safe id of docs/contracts.md#conventions: characters outside
  `[A-Za-z0-9._-]` become `_`, and when anything changed the first 8 hex digits of the name's
  SHA-256 are appended after a `-`.

## Neutral column types

One vocabulary for every source kind, so later stages map types once:

| Type | Meaning |
|---|---|
| `text` | short text with an optional `size` (characters) |
| `memo` | long text |
| `byte` | 0..255 integer |
| `integer` | 16-bit integer |
| `longinteger` | 32-bit integer (also Access "Large Number" when present) |
| `autonumber` | a 32-bit integer the database assigns |
| `single`, `double` | binary floating point |
| `currency` | fixed-point money, four decimals |
| `decimal` | exact decimal with `precision` and `scale` |
| `datetime` | date and time |
| `yesno` | boolean |
| `hyperlink` | a link stored as text |
| `oleobject` | binary object |
| `complex` | attachments and multi-valued fields |
| `guid` | replication id / unique identifier |

For Access the type comes from the table design's `jetType` (an `autoUnique` long integer is
`autonumber`; a `hyperlink` flag wins over the stored text type). For workbooks it is inferred
from the column's cached values: all dates give `datetime`, all whole numbers `longinteger`, other
numbers `decimal`, booleans `yesno`, anything else (and an empty column) `text`; such columns carry
`type_inferred: true`.

## schema.json

`tables[]`, one per table:

| Field | Meaning |
|---|---|
| `name` | table name (XML name escapes such as `_x0020_` decoded) |
| `role` | `data`, or `documentation` for a table that only documents the application (no business rows) |
| `columns[]` | in design order: `name`, `type`, `size`, `precision`, `scale`, `required`; Access adds `source_type` (the original type word), `sql_type` and `properties` (kept design properties such as Caption, Description, DefaultValue, ValidationRule, Format, InputMask and lookup settings); calculated columns add `computed: true` and `formula` |
| `primary_key` | column names, empty when none |
| `indexes[]` | `name`, `columns`, `unique`, and `primary` on the primary key index |
| `properties` | kept table properties (Description, ValidationRule, ...) |
| `sheet`, `ref` | workbooks: the sheet and A1 range of the ListObject |
| `key_inferred` | workbooks: the key was inferred, S1 confirms it |

`relationships[]`: `name`, `from_table` and `from_columns` (the many side), `to_table` and
`to_columns` (the one side), `enforce`, `cascade_update`, `cascade_delete`, plus `one_to_one` and
`join` (`inner`, `left`, `right`) for Access and `inferred: true` for workbook relationships.

## objects.json

### Queries (`queries[]`)

| Field | Meaning |
|---|---|
| `storage` | `design` when Access keeps the query as a design grid, `sql` when it keeps only SQL text (union, pass-through and data-definition queries, or SQL-only exports) |
| `query_type` | `select`, `union`, `crosstab`, `action` or `pass-through`, from the SQL when present, else from the stored operation |
| `design` | for grid queries: `operation`, `option`, `input_tables[]` (`name`, `alias`), `output_columns[]` (`expression`, `alias`), `joins[]` (`left`, `right`, `expression`, `flag`), `where`, `having`, `group_by[]`, `order_by[]`; `{}` for SQL-only queries |
| `sql` | the SQL text when the source stores it, else `null` |
| `params` | declared parameters |
| `functions` | names called in the query's expressions (SQL keywords and object names excluded) |
| `form_refs` | `Form!Control` references found in expressions |

### Forms and reports (`forms[]`, `reports[]`)

`name`, `record_source`, `caption`, `has_code_behind`, `events`, `width`, and for forms
`default_view`, for reports `groups[]` (`control_source`, `group_header`, `group_footer`,
`descending`). The layout facts S6 draws wireframes from:

- `sections[]`: `name`, `kind` (`form_header`, `page_header`, `detail`, `page_footer`,
  `form_footer`, and for reports `group_header`, `group_footer`; a report header and footer use
  the form kinds) and `height`;
- `controls[]` in design order: `name`, `type` (the Access control type, for example `TextBox`,
  `Label`, `Subform`, `Tab`), `section`, `parent` (the control a label or option belongs to, or
  the tab page), `caption`, `left`, `top`, `width`, `height` (a size missing from the control
  comes from the form's default block for its type), `control_source`, `row_source`,
  `source_object` (subforms, without the `Form.`/`Report.` prefix), `page` (the tab page the
  control sits on), `visible` and `events`;
- `tab_pages[]` per tab control in index order: `tab_control`, `name`, `caption`, `index`.

`events` lists the events with a handler: the event name alone (`Click`, `AfterUpdate`, `Load`)
for an event procedure, `<event>:macro` for an embedded macro, `<event>:expression` for an
`=Function()` handler and `<event>:named-macro` for a macro object.

### Macros (`macros[]`)

Standalone macro objects and macros embedded in form, report, section and control events share
one shape: `name`, `kind` (`standalone` or `embedded`), `owner` (`null`, or
`form:<form>.<event>` / `report:<report>.<event>`, where a control's event is
`<control>.<event>`), and `actions[]`. An embedded macro is named like its owner without the
`form:`/`report:` prefix. Actions are flattened in order: `{action, args, depth}`; `args` maps
argument names to object names or (scrubbed) literals; block statements (`If`, `ElseIf`, `Else`,
`LookUpRecord`, `ForEachRecord`, `EditRecord`, `CreateRecord`, `Group`, `Sub` ...) appear as
entries of their own, and the statements inside them follow with `depth` + 1. Parse reads the
macro's AXL (XML) form; when an embedded macro has none it falls back to the positional
arguments (`"1"`, `"2"` ...) and warns.

### Linked tables and other Access facts

- `linked_tables[]`: `name`, `backend_kind` (`access`, `odbc`, `sharepoint`, `excel`, `text`, or
  `unresolved`), `backend` (a number per distinct back end), `backend_file` (the file name only,
  for file back ends) and `source_table`. The connect string itself is not kept.
- `code_behind`: `form:<name>` and `report:<name>` for objects with a CodeBehindForm or
  CodeBehindReport section.
- `object_properties[]`: per object with a properties part, its property names only.
- `vba_references`: the project's references (GUID and version for Access, names for workbooks).
- `database_properties`: the names of the database properties.

### Facts (`facts`)

Non-data facts the inventory and the health check count: `compiled_only`, `vba_present`,
`parts` (design parts per folder and extension), `encodings` (parts per object kind and
detected encoding), `sample_data_tables`, `data_backends`, `linked_tables` (counts per back-end
kind); for workbooks `sample_outputs` (file count per declared sample folder, never file names),
`formula_cells_by_sheet` and `table_columns`; with a data copy, `data_copy` (per-table column
counts of the comparison).

### Diagnostics (`diagnostics`)

`errors[]` and `warnings[]` as `{code, object, line?, count?, detail?}` (codes, object names and
positions; never source text), `unknown_parts` (folder to count) and `package_parts` (the
package folders outside the design objects, with counts). `parse --strict` fails on any error or
unknown part; warnings never fail.

### Workbook additions (xlsm)

| Key | Meaning |
|---|---|
| `sheets[]` | `name`, `index`, `state`, `role` (`input`, `template`, `calculation`, `dashboard`, `documentation`; inferred from the sheet name, charts, documentation tables and the formula/constant balance, so `role_inferred: true`), `formula_cells` |
| `layout` | per sheet: `default_column_width`, `default_row_height`, non-default `column_widths` and `row_heights`, `merged` ranges, `labels[]` (`cell`, `text`), `constants[]` (`cell`, `value: null`) and `list_objects[]` (`name`, `ref`, `headers`) |
| `formula_blocks[]` | contiguous formula ranges outside ListObjects, named `<Sheet>!<A1 range>`; each cell with `address`, `formula` as written (structured references kept) and `has_cached_value` |
| `calculated_columns[]` | `<Table>[<Column>]` with `table`, `column` and `formula` |
| `defined_names[]` | every defined name in the workbook part, `_xlnm.*` included: `name`, `scope_sheet`, `hidden`, `refers_to`, `broken` (refers to `#REF!`; a warning, not an error) |
| `data_validations[]` | `<Sheet>!<range>` with `type`, `operator`, `formula1`, `formula2`, `allow_blank` |
| `charts[]` | `<Sheet>!<chart name>` with `type` (for example `barChart`), `title`, `anchor` (`from`, `to` cells) and `series[]` (the `tx`, `cat` and `val` references) |
| `buttons[]` | form-control buttons: `sheet`, `control`, `caption`, `macro`, `anchor` |
| `macro_assignments[]` | drawing shapes with an assigned macro: `shape`, `kind`, `macro`, `caption`, `anchor` |
| `activex[]` | ActiveX controls: `control`, `clsid`, `part`, `anchor` |
| `comments` | comment counts per sheet (the texts go to the private folder) |
| `userforms[]` | `name`, `caption` and `controls[]` (`name`, `type`, `caption`, and `left`, `top`, `width`, `height` when an exported `.frm` gives them) |

Label cells follow the contract: constant text cells outside ListObjects, on input, template or
dashboard sheets, at most 80 characters, that no formula reference and no VBA `Range("...")` or
`Cells(r, c)` literal reads or writes. Every other constant cell is listed by position with
`value: null`; S2 turns the constant cells that formulas read into run-time parameters.

The workbook mapping in short: a ListObject is a table, its calculated column a computed column
and a `calculated_column` object, a list validation on a table column becomes that column's
`choices_from`, a unique non-blank column is an inferred key, and a column whose values all
appear in another table's key is an inferred foreign key.

## procedures.json

`modules[]`: `name`, `kind` (`standard`, `class`, `form`, `report`, `document`, `userform`),
`nonblank_lines`, `code_file` (`code/<file-safe name>.txt`) and `procedures[]`. Code-behind
modules are named as Access names them, `Form_<form>` and `Report_<report>`. Workbook components
are typed from the VBA project's PROJECT stream (`Module=` standard, `Class=` class, `Document=`
document, `BaseClass=` userform) and also list `handlers` (procedure names with an underscore,
such as `btnPrice_Click`).

`nonblank_lines` counts lines that are not blank after trimming and do not start with
`Attribute ` (comments count). For Access the module text is the module part or the
CodeBehindForm/CodeBehindReport section; for workbooks it is the component as olevba extracts it.

Each procedure: `name`, `kind` (`Sub`, `Function`, `Property Get`, `Property Let`,
`Property Set`), `scope` (`Public`, `Private`, `Friend`; `Public` when unstated), `static`,
`start` and `end` (1-based lines in the code file), `lines` (non-blank lines) and `refs`:
`tables`, `queries`, `forms` and `reports` the procedure names (in identifiers, bracketed names or
string literals, not in comments), `docmd` (the `DoCmd` methods it calls) and `dao` (whether it
uses DAO or the domain functions). `Declare` statements are not procedures; a declaration
continued with ` _` is one statement.

## datamacros.json

`datamacros[]`: `table`, `event` (`BeforeChange`, `AfterInsert`, ...; `Named` for a named data
macro, with its `name` and `params`) and `actions[]` in the macro action shape above. `files[]`
lists each data-macro part with its table and the number of macros in it. Object-properties
parts are metadata, never data macros, and never counted here.

## Scrubbing

Parse scrubs every string of the four files (keys included) and every code file before writing:
connect-string credentials (`PWD=`, `Password=`, `UID=`, `User ID=`), the user and password of
credential URLs, private-key blocks, access-key ids and quoted values assigned to names that
contain api_key, secret, token or password become `<SECRET-n>`; a URL whose query carries a
secret becomes `<URL-n>`; email addresses `<EMAIL-n>`; phone numbers `<PHONE-n>`. The same
original always gets the same token within an engagement: the map is the private
`scrub-map.json` (data zone, never read by agents). `toolkit scrub --check <dir>` exits 1 when
anything a scrub would replace remains and names the file and JSON path or line, never the value.

## Sources

| Kind | Read from |
|---|---|
| `accdt` | the template's OPC parts: object parts under `template/database/objects/` (SaveAsText in UTF-16LE with a BOM; VBA in cp1252; table XSDs), their metadata parts (object name and type), data-macro and properties `.axl` parts, `relationships.xml`, `vbaReferences.xml` and `databaseProperties.xml`; sample rows (`sampleData/`) only through `iter_rows` |
| `vcs-export` | an msaccess-vcs-addin export folder: `forms/`, `reports/`, `modules/`, `queries/`, `macros/`, `tbldefs/`, `tdmacros/`, `relations/` (folder names as the add-in documents them); `tables/` data only through `iter_rows` |
| `accdb`, `mdb` | the Jackcess bridge of S2.reference (`toolkit.reference.bridge`); until it exists parse exits 2 |
| `xlsm` | openpyxl for sheets, tables, validations and formulas, the package XML for names, drawings, controls and comments, oletools and olefile for the VBA project |

SaveAsText is read line by line: nested `Begin`/`End` blocks, `Name =value` properties whose
quoted values may continue on following quoted lines, both backslash escapes (including
three-digit octal) and doubled quotes inside strings, property blocks of hex lines, typed
`db<Type> "Name"` properties, and the trailing CodeBehindForm/CodeBehindReport section. CRLF and
LF line ends are both accepted everywhere.
