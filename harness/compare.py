"""Compare step of the equivalence harness (docs/contracts.md#harness).

- `unit_bytes(unit, adapter, uid, params, keys)`: the canonical bytes of one unit's result
  (canonical.py, with the manifest's column types and order_by).
- `digest_matches(data, entry, key)`: the engagement check, the HMAC digest of the canonical
  bytes against the manifest.
- `mismatch(obtained, expected, kind)`: per-column mismatch counts and a datacompy report with
  every column hidden (`hide_sensitive_columns`, `report(sample_count=0)`), so the report holds
  counts and column names only.
- `text_diff(obtained, expected)`: a short unified diff, for synthetic seeds only.
"""

from __future__ import annotations

import csv
import difflib
import io
import json
from dataclasses import dataclass, field

from . import canonical as canon

ONLY_OBTAINED = "(rows only in obtained)"
ONLY_GOLDEN = "(rows only in golden)"


def extension(unit: dict) -> str:
    return ".json" if unit.get("kind") == "document" else ".csv"


def golden_name(unit_id: str, unit: dict) -> str:
    return canon.file_safe(unit_id) + extension(unit)


def unit_bytes(unit: dict, adapter, unit_id: str, params: dict, keys) -> bytes:
    """Run one unit through the adapter and return its canonical bytes. NotImplementedError from
    the adapter propagates to the caller."""
    if unit.get("kind") == "document":
        doc = {
            str(k): adapter.render_document(unit_id, str(k), dict(params))
            for k in keys or []
        }
        return canon.canonical_document(doc)
    columns = unit.get("columns")
    if not columns:
        raise canon.CanonicalError("the manifest gives this unit no columns")
    rows = adapter.run_saved_query(unit_id, dict(params))
    return canon.canonical_csv(columns, list(rows), unit.get("order_by"))


def digest_matches(data: bytes, entry: dict, key: bytes | None) -> bool:
    want = (entry.get("digest") or {}).get("value")
    alg = (entry.get("digest") or {}).get("alg")
    if not want:
        return False
    if alg == "hmac-sha256":
        if key is None:
            return False
        return canon.digest(data, key) == want
    return canon.digest(data) == want


# ------------------------------------------------------------------ tables


def _unique(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        k = seen.get(n, 0) + 1
        seen[n] = k
        out.append(n if k == 1 else f"{n}_{k}")
    return out


def table_of(data: bytes, kind: str | None) -> tuple[list[str], list[list[str]]]:
    """(header, rows of field strings) of canonical bytes: CSV, or a document flattened to one
    row per key (column `key`, then the top-level fields; nested values as canonical JSON)."""
    text = data.decode("utf-8")
    if kind == "document":
        doc = json.loads(text) if text.strip() else {}
        fields = sorted({f for v in doc.values() for f in v})
        rows = []
        for k in sorted(doc):
            row = [k]
            for f in fields:
                v = doc[k].get(f)
                if v is None:
                    row.append(canon.NULL)
                elif isinstance(v, str):
                    row.append(v)
                else:
                    row.append(
                        json.dumps(
                            v, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                        )
                    )
            rows.append(row)
        return _unique(["key", *fields]), rows
    records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    if not records:
        return [], []
    return _unique(records[0]), [list(r) for r in records[1:]]


# ------------------------------------------------------------------ mismatch


@dataclass
class Mismatch:
    rows: int
    columns: dict[str, int] = field(default_factory=dict)
    report: str = ""

    def message(self) -> str:
        return f"{self.rows} rows differ in columns [{', '.join(self.columns)}]"


def _positional(h1, r1, h2, r2) -> Mismatch:
    counts: dict[str, int] = {}
    rows = 0
    for a, b in zip(r1, r2):
        differ = False
        for i, name in enumerate(h1):
            va = a[i] if i < len(a) else None
            vb = b[i] if i < len(b) else None
            if va != vb:
                counts[name] = counts.get(name, 0) + 1
                differ = True
        rows += int(differ)
    if len(r1) > len(r2):
        counts[ONLY_OBTAINED] = len(r1) - len(r2)
    elif len(r2) > len(r1):
        counts[ONLY_GOLDEN] = len(r2) - len(r1)
    rows += abs(len(r1) - len(r2))
    return Mismatch(rows, counts)


def mismatch(obtained: bytes, expected: bytes, kind: str | None = None) -> Mismatch:
    """Mismatch counts per column between obtained and expected canonical bytes, with a datacompy
    report (sample_count=0, every column hidden). Joins on the first column."""
    h1, r1 = table_of(obtained, kind)
    h2, r2 = table_of(expected, kind)
    if h1 != h2 or not h1:
        names = sorted(set(h1) ^ set(h2)) or h1 or h2
        return Mismatch(
            max(len(r1), len(r2)),
            {n: max(len(r1), len(r2)) for n in names},
            "column names differ between obtained and golden\n",
        )
    try:
        import datacompy
        import pandas as pd

        compare_cls = getattr(datacompy, "Compare", None) or datacompy.PandasCompare
        df1 = pd.DataFrame(r1, columns=h1, dtype=object)
        df2 = pd.DataFrame(r2, columns=h2, dtype=object)
        cmp = compare_cls(
            df1,
            df2,
            join_columns=[h1[0]],
            df1_name="obtained",
            df2_name="golden",
            cast_column_names_lower=False,
        )
        counts = {
            str(s["column"]): int(s["unequal_cnt"])
            for s in cmp.column_stats
            if int(s["unequal_cnt"])
        }
        intersect = len(cmp.all_mismatch()) if counts else 0
        only1, only2 = len(cmp.df1_unq_rows), len(cmp.df2_unq_rows)
        if only1:
            counts[ONLY_OBTAINED] = only1
        if only2:
            counts[ONLY_GOLDEN] = only2
        cmp.hide_sensitive_columns(list(h1))
        report = cmp.report(sample_count=0)
        return Mismatch(intersect + only1 + only2, counts, report)
    except Exception as exc:  # noqa: BLE001 - fall back to a positional count
        m = _positional(h1, r1, h2, r2)
        m.report = f"datacompy could not compare ({type(exc).__name__}); positional counts only\n"
        return m


def text_diff(obtained: bytes, expected: bytes, limit: int = 40) -> str:
    """A unified diff of at most `limit` lines (synthetic seeds only: it shows values)."""
    a = expected.decode("utf-8", "replace").splitlines()
    b = obtained.decode("utf-8", "replace").splitlines()
    lines = list(difflib.unified_diff(a, b, "expected", "obtained", lineterm="", n=1))
    if len(lines) > limit:
        lines = lines[:limit] + [f"... {len(lines) - limit} more diff lines"]
    return "\n".join(lines)
