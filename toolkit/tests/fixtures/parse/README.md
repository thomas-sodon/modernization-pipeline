# Parse fixtures (synthetic)

Hand-written for the parse tests. Nothing here comes from a client or Microsoft file: the objects
describe a made-up widget shop. Containers (.accdt, .xlsm) are assembled at test time from these
text parts; the tests re-encode them (UTF-16LE with BOM, cp1252, CRLF) as the real formats need.

- `access/`: SaveAsText forms, a report and queries, VBA modules, table XSDs, data macros,
  object properties and sample rows, used by test_parse_saveastext, test_parse_vba,
  test_parse_datamacros and test_parse_accdt.
- `macros/`: one standalone macro and one form with one embedded macro (test_parse_saveastext).
- `vcs/`: a msaccess-vcs-addin style export folder (test_parse_vcs).
- `xlsm/`: a PROJECT stream, VBA component texts and an exported .frm (test_parse_xlsm); the
  workbook itself is built with openpyxl at test time.
