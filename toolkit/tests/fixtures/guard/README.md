# Guard fixtures (synthetic)

Hand-written for `toolkit/tests/test_guard.py`. Nothing here comes from a client or Microsoft
file: the "greenhouse ledger" app is invented.

- `vcs/` is a tiny msaccess-vcs-addin style export. Files carry an extra `.txt` suffix
  (`modGreenhouse.bas.txt`) so the forbidden-file hooks never see a `.bas` name in this repo; the
  tests copy the tree into a temporary engagement's `private/sources/` and drop the suffix.
- `paraphrase.txt` restates what `modGreenhouse` does in other words; it must pass the copy check.
- `handwritten-form.txt` is a SaveAsText form written from scratch with the same layout
  vocabulary as the export (Begin Form, sections, controls) but no copied code or expressions; it
  must pass too.
- `vcs/tables/tblTrays.txt` holds data values that test strings.txt: one equals a form name
  (`frmTrayBench`) and one sits inside it (`TrayBench`); neither may enter strings.txt, because
  mp-data withholds any summary line that contains a corpus string.
