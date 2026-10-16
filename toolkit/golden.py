"""Goldens: record every characterized unit's output in the canonical form, and report the
characterization rate (docs/contracts.md#goldens-manifest, #canonical-output, #pass-rate).

`record(eng, ...)` runs the catalog units whose rules.json object is kept (plus units whose
object rules.json does not list, recorded with area null) through `ReferenceAdapter` on each
dataset, canonicalises the results with `harness.canonical` and writes:

- `<target>/goldens/manifest.json`: per unit area, kind, columns, order_by,
  needs_access_confirmation, canonical_version and per dataset status, reason, rows and digest
  (hmac-sha256 for the engagement, sha256 for seeds). No values.
- `<target>/goldens/params.json`: parameter definitions and the seed values.
- `<target>/goldens/synthetic/seed-N/expected/<file-safe id>.csv|json`: seed outputs in full,
  next to a `.gitattributes` (`* -text`) so git stores them byte-exact.
- `<private>/goldens/engagement/<file-safe id>.csv|json` and `<private>/goldens/params.json`:
  engagement outputs and parameter values (data zone, only through mp-data).

`status(...)` reads only committed files: the manifest and rules.json.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from harness import canonical as canon
from toolkit import quiet

FORMAT_VERSION = 1
STATUSES = ("ok", "empty", "needs-params", "unsupported")
MAX_TRIES = 20
UNIT_OBJECT_TYPES = ("query", "calculated_column", "formula_block")
KEY_TYPES = canon.INT_TYPES
# Seed expected files are byte-exact canonical output (CSV records end in CRLF, and a text field
# may hold CR or LF). A repository rule such as `* text=auto eol=lf` makes git store them with
# LF, so a checkout no longer matches its manifest digest. Each expected directory carries this
# attribute file, which turns git's line-end conversion off for the files in it.
GITATTRIBUTES = (
    b"# Written by golden record: canonical outputs are byte-exact (CSV records end in CRLF),\n"
    b"# so git must store them unchanged. Do not edit.\n"
    b"* -text\n"
)
NUMBER_TYPES = canon.NUMERIC_TYPES


class GoldenError(Exception):
    """An input or environment problem (exit 2 by default). The message never holds values."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def in_data_plane() -> bool:
    return os.environ.get("MP_SANDBOX") == "mp-data"


def make_adapter(private):
    """The reference adapter (docs/contracts.md#harness); tests replace this factory."""
    from toolkit.commands.reference import ReferenceAdapter

    return ReferenceAdapter(private=private)


# ------------------------------------------------------------------ inputs


def read_json(path: Path, what: str) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GoldenError(f"{what} missing") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GoldenError(f"{what} is not valid JSON") from None
    if not isinstance(doc, dict):
        raise GoldenError(f"{what} is not an object")
    return doc


def load_catalog(private: Path, eng_path: str) -> dict:
    path = Path(private) / "out" / "reference" / "catalog.json"
    if not path.is_file():
        raise GoldenError(
            f"out/reference/catalog.json missing, run:\nuv run toolkit reference sql {eng_path} --all"
        )
    doc = read_json(path, "out/reference/catalog.json")
    if doc.get("format_version") != FORMAT_VERSION:
        raise GoldenError("out/reference/catalog.json: unknown format_version")
    return doc


def load_rules(path: Path) -> dict:
    doc = read_json(path, "analysis/rules.json")
    if doc.get("format_version") != FORMAT_VERSION:
        raise GoldenError("analysis/rules.json: unknown format_version")
    return doc


def object_index(rules: dict) -> dict[tuple[str, str], dict]:
    return {(o.get("type"), o.get("name")): o for o in rules.get("objects") or []}


def recorded_units(catalog: dict, rules: dict) -> list[tuple[dict, dict | None]]:
    """(catalog unit, rules object or None) of the units the manifest holds: object kept, or
    object missing from rules.json (recorded with area null, which golden status rejects)."""
    objects = object_index(rules)
    out = []
    for unit in catalog.get("units") or []:
        obj = unit.get("object") or {}
        found = objects.get((obj.get("type"), obj.get("name")))
        if found is None or found.get("decision") == "keep":
            out.append((unit, found))
    return sorted(out, key=lambda x: x[0]["id"])


def seed_dirs(target: Path) -> list[tuple[str, Path]]:
    root = Path(target) / "goldens" / "synthetic"
    out = []
    if root.is_dir():
        for d in root.iterdir():
            if (
                re.fullmatch(r"seed-\d+", d.name)
                and (d / "input" / "dataset.json").is_file()
            ):
                out.append((d.name, d / "input"))
    return sorted(out, key=lambda x: int(x[0].split("-")[1]))


def engagement_data_file(eng: dict) -> Path:
    """The file whose bytes key the engagement digests (docs/contracts.md#goldens-manifest)."""
    src = eng["source"]
    path = Path(src.get("data_copy") or src["path"])
    if path.is_dir():
        path = (
            Path(eng["paths"]["private"]) / "datasets" / "engagement" / "dataset.json"
        )
    return path


def engagement_key(eng: dict, eng_path: str) -> bytes:
    path = engagement_data_file(eng)
    if not path.is_file():
        raise GoldenError(
            f"engagement data file missing, run:\nmp-data fetch {eng_path} --verify"
        )
    return canon.key_from_file(path)


def build_command(dataset: str, eng_path: str) -> str:
    if dataset == "engagement":
        return f"mp-data reference build {eng_path} --dataset engagement"
    return f"uv run toolkit reference build {eng_path} --dataset seeds"


# ------------------------------------------------------------------ SQL scanning


_SEG = r"(?:\[[^\]]+\]|[A-Za-z_]\w*)"
_OPS = r"(?:=|<>|<=|>=|<|>|\bLike\b)"
_BEFORE = re.compile(
    rf"(?:({_SEG})\s*\.\s*)?({_SEG})\s*\)*\s*{_OPS}\s*\(*\s*$", re.IGNORECASE
)
_AFTER = re.compile(
    rf"^\s*\)*\s*{_OPS}\s*\(*\s*(?:({_SEG})\s*\.\s*)?({_SEG})", re.IGNORECASE
)


def _unbracket(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s[1:-1] if s.startswith("[") and s.endswith("]") else s


def _norm(expr: str) -> str:
    return re.sub(r"\s+", "", expr.replace("[", "").replace("]", "")).lower()


def _top_level(masked: str) -> list[int]:
    """Paren depth at each position of a masked SQL text."""
    depth, out = 0, []
    for ch in masked:
        if ch == "(":
            depth += 1
        out.append(depth)
        if ch == ")":
            depth = max(0, depth - 1)
    return out


def _split_top(
    text: str, masked: str, depth: list[int], start: int, end: int
) -> list[tuple[int, int]]:
    parts, last = [], start
    for i in range(start, end):
        if masked[i] == "," and depth[i] == 0:
            parts.append((last, i))
            last = i + 1
    parts.append((last, end))
    return parts


def _select_items(
    sql: str, masked: str, depth: list[int]
) -> list[tuple[str, str | None]]:
    """(expression, output name or None for * expansions) of the first SELECT's list."""
    m = None
    for cand in re.finditer(r"\bSELECT\b", masked, re.IGNORECASE):
        if depth[cand.start()] == 0:
            m = cand
            break
    if m is None:
        return []
    start = m.end()
    head = re.match(
        r"\s*(?:(?:DISTINCTROW|DISTINCT|ALL)\s+)?(?:TOP\s+\d+(?:\s+PERCENT)?\s+)?",
        masked[start:],
        re.IGNORECASE,
    )
    start += head.end() if head else 0
    end = len(sql)
    for stop in re.finditer(
        r"\b(?:FROM|UNION|WHERE|ORDER\s+BY)\b", masked[start:], re.IGNORECASE
    ):
        if depth[start + stop.start()] == 0:
            end = start + stop.start()
            break
    items = []
    for a, b in _split_top(sql, masked, depth, start, end):
        text, mtext = sql[a:b], masked[a:b]
        alias = re.search(r"\s+AS\s+(\[\0*\]|[A-Za-z_]\w*)\s*$", mtext, re.IGNORECASE)
        if alias:
            name = text[alias.start(1) : alias.end(1)]
            items.append((text[: alias.start()].strip(), _unbracket(name)))
            continue
        expr = text.strip()
        if expr.endswith("*"):
            items.append((expr, None))
            continue
        ref = re.fullmatch(rf"(?:{_SEG}\s*\.\s*)?({_SEG})", expr)
        items.append((expr, _unbracket(ref.group(1)) if ref else None))
    return items


def order_by(sql: str | None, columns: list[str] | None) -> list[dict] | None:
    """[{column, desc}] for the unit's ORDER BY on output columns: the leading terms of the
    outermost ORDER BY that name an output column (an alias, a select expression, a column
    reference or a position); null when none does."""
    if not sql or not columns:
        return None
    from toolkit.reference import catalog as cat

    text = cat.strip_parameters(sql).strip().rstrip(";").rstrip()
    masked = cat.mask(text)
    depth = _top_level(masked)
    found = None
    for m in re.finditer(r"\bORDER\s+BY\b", masked, re.IGNORECASE):
        if depth[m.start()] == 0:
            found = m
    if found is None:
        return None
    items = _select_items(text, masked, depth)
    by_lower = {}
    for name in columns:
        by_lower.setdefault(name.lower(), name)
    out = []
    for a, b in _split_top(text, masked, depth, found.end(), len(text)):
        term = text[a:b].strip()
        desc = False
        d = re.search(r"\s+(ASC|DESC)\s*$", term, re.IGNORECASE)
        if d:
            desc = d.group(1).upper() == "DESC"
            term = term[: d.start()].strip()
        name = None
        if re.fullmatch(r"\d+", term):
            i = int(term) - 1
            name = columns[i] if 0 <= i < len(columns) else None
        if name is None:
            for expr, out_name in items:
                if out_name and _norm(expr) == _norm(term):
                    name = out_name
                    break
        if name is None:
            ref = re.fullmatch(rf"(?:{_SEG}\s*\.\s*)?({_SEG})", term)
            if ref:
                name = _unbracket(ref.group(1))
        resolved = by_lower.get((name or "").lower())
        if resolved is None:
            break
        out.append({"column": resolved, "desc": desc})
    return out or None


def compared_column(sql: str, start: int, end: int) -> tuple[str | None, str] | None:
    """(table or None, column) that a reference at sql[start:end] is compared with."""
    m = _BEFORE.search(sql[:start])
    if m:
        return _unbracket(m.group(1)), _unbracket(m.group(2))
    m = _AFTER.match(sql[end:])
    if m:
        return _unbracket(m.group(1)), _unbracket(m.group(2))
    return None


def param_sites(texts: list[str], param: dict) -> list[tuple[str | None, str]]:
    """The columns a parameter is compared with in the unit's SQL (and its dependencies')."""
    from toolkit.reference import catalog as cat

    name = param["name"].lower()
    out = []
    for sql in texts:
        spans = []
        if param.get("source") == "form_control":
            spans = [(s, e) for n, _c, s, e in cat.form_refs(sql) if n.lower() == name]
        else:
            masked = cat.mask(sql, brackets=False)
            for m in re.finditer(
                r"\[" + re.escape(param["name"]) + r"\]", sql, re.IGNORECASE
            ):
                if masked[m.start()] != "\0":
                    spans.append(m.span())
        for s, e in spans:
            site = compared_column(sql, s, e)
            if site and site not in out:
                out.append(site)
    return out


# ------------------------------------------------------------------ parameter values


class DatasetValues:
    """Distinct column values of one dataset directory, read through toolkit.dataset."""

    def __init__(self, directory: Path | None):
        self.reader = None
        self._cache: dict[tuple[str, str], list] = {}
        if directory is not None:
            from toolkit.dataset import DatasetDirReader

            self.reader = DatasetDirReader(Path(directory))

    def _columns(self, table: str) -> list[str]:
        return [c["name"] for c in self.reader.by_name[table].get("columns") or []]

    def tables_for(self, table: str | None, column: str) -> list[tuple[str, str]]:
        if self.reader is None:
            return []
        lc = column.lower()
        tables = self.reader.tables()
        if table:
            for t in tables:
                if t.lower() == table.lower():
                    for c in self._columns(t):
                        if c.lower() == lc:
                            return [(t, c)]
        having = []
        for t in tables:
            for c in self._columns(t):
                if c.lower() == lc:
                    having.append((t, c))
        first = [(t, c) for t, c in having if self._columns(t)[0].lower() == lc]
        return first or having

    def values(self, table: str, column: str) -> list:
        key = (table, column)
        if key not in self._cache:
            seen = set()
            for row in self.reader.iter_rows(table):
                v = row.get(column)
                if v is not None:
                    seen.add(v)
            self._cache[key] = list(seen)
        return self._cache[key]

    def date_values(self, table_hint: str, column_hint: str) -> list:
        """Datetime values of the columns whose name holds `column_hint` in the tables whose
        name holds `table_hint` (xlsm dashboard date inputs)."""
        if self.reader is None:
            return []
        import datetime as dt

        out = []
        for t in self.reader.tables():
            if table_hint not in t.lower():
                continue
            for c in self.reader.by_name[t].get("columns") or []:
                if c.get("type") == "datetime" and column_hint in c["name"].lower():
                    out += [
                        v
                        for v in self.values(t, c["name"])
                        if isinstance(v, dt.datetime)
                    ]
        return out


def _sortable(ptype: str, values: list) -> list:
    def key(v):
        if ptype in NUMBER_TYPES:
            try:
                return (0, float(v))
            except (TypeError, ValueError):
                return (1, str(v))
        return (0, str(v)) if not hasattr(v, "isoformat") else (0, v.isoformat())

    return sorted(values, key=key)


def _canon_values(ptype: str, values: list) -> list[str]:
    out = []
    for v in values:
        try:
            text = canon.format_value(ptype, v)
        except canon.CanonicalError:
            continue
        if text not in out:
            out.append(text)
    return out


def candidates(
    unit: dict,
    param: dict,
    texts: list[str],
    data: DatasetValues,
    dataset: str,
    adapter,
):
    """Candidate canonical values for one parameter, smallest first; None when the tool finds
    no source for them."""
    ptype = param.get("type") or "text"
    if param.get("source") == "input_cell":
        if dataset == "engagement":
            getter = getattr(adapter, "input_value", None)
            if getter is None:
                return None
            try:
                value = getter(param["name"])
            except Exception:  # noqa: BLE001 - adapter errors make the unit needs-params
                return None
            return _canon_values(ptype, [value]) or None
        if ptype == "datetime":
            dates = data.date_values("invoice", "date")
            return _canon_values(ptype, [max(dates)]) if dates else None
        return None  # other seed inputs take a value an answers.md rule gives
    values = []
    for table, column in param_sites(texts, param):
        for t, c in data.tables_for(table, column):
            values += data.values(t, c)
    if not values:
        return None
    if ptype == "datetime":
        return _canon_values(ptype, [max(values)])
    return _canon_values(ptype, _sortable(ptype, values))[:MAX_TRIES] or None


# ------------------------------------------------------------------ recording


@dataclass
class Outcome:
    status: str
    reason: str | None
    rows: int | None = None
    data: bytes | None = None
    values: dict | None = None
    keys: list | None = None


@dataclass
class Recording:
    manifest: dict
    public_params: dict
    private_params: dict | None
    files: dict[Path, bytes]
    owned_dirs: list[Path]
    outcomes: dict[str, dict[str, Outcome]]
    failures: list[str] = field(default_factory=list)


class UnitFailed(Exception):
    """A unit failed at run time (str: the exception class, never values)."""


def _columns_of(adapter, unit: dict) -> list[dict] | None:
    if unit.get("kind") == "document":
        return None
    try:
        described = adapter.describe(unit["id"])
    except NotImplementedError:
        return None
    except Exception as exc:  # noqa: BLE001
        raise UnitFailed(f"describe: {type(exc).__name__}") from None
    return [{"name": str(n), "type": str(t)} for n, t in described]


def _rows_outcome(rows, columns, order, values=None) -> Outcome:
    data = canon.canonical_csv(columns, rows, order)
    n = len(rows)
    return Outcome("ok" if n else "empty", None if n else "empty", n, data, values)


def run_unit(
    adapter, unit: dict, columns, order, texts, data: DatasetValues, dataset: str
) -> Outcome:
    uid = unit["id"]
    status, reason = unit.get("status"), unit.get("reason")
    if status == "unsupported":
        return Outcome("unsupported", reason or "other")
    if status not in ("ok", "needs-params"):
        return Outcome("unsupported", reason or "other")
    if unit.get("kind") == "document":
        return _document(adapter, unit)
    if columns is None:
        return Outcome("unsupported", "other")
    if status == "ok":
        try:
            rows = adapter.run_saved_query(uid, {})
        except NotImplementedError:
            return Outcome("unsupported", reason or "other")
        except Exception as exc:  # noqa: BLE001
            raise UnitFailed(type(exc).__name__) from None
        return _rows_outcome(rows, columns, order)
    # needs-params: pick values from the dataset
    params = unit.get("params") or []
    lists = []
    for p in params:
        c = candidates(unit, p, texts, data, dataset, adapter)
        if c is None:
            return Outcome("needs-params", reason or "other")
        lists.append(c)
    only_dates = all((p.get("type") == "datetime") for p in params)
    for combo in itertools.islice(itertools.product(*lists), MAX_TRIES):
        values = {p["name"]: v for p, v in zip(params, combo)}
        try:
            rows = adapter.run_saved_query(uid, values)
        except NotImplementedError:
            return Outcome("unsupported", reason or "other")
        except Exception as exc:  # noqa: BLE001 - a value that does not work is no failure
            rows = None
            quiet.progress(f"{uid}: a candidate value failed ({type(exc).__name__})")
        if rows is None:
            continue
        if rows or only_dates:
            return _rows_outcome(rows, columns, order, values)
    return Outcome("needs-params", "other")


def _document(adapter, unit: dict) -> Outcome:
    uid = unit["id"]
    lister = getattr(adapter, "document_keys", None)
    if lister is None:
        return Outcome("unsupported", "other")
    try:
        keys = [str(k) for k in lister(uid)]
        doc = {k: adapter.render_document(uid, k, {}) for k in keys}
    except NotImplementedError:
        return Outcome("unsupported", "other")
    except Exception as exc:  # noqa: BLE001
        raise UnitFailed(type(exc).__name__) from None
    data = canon.canonical_document(doc)
    n = len(doc)
    return Outcome(
        "ok" if n else "empty", None if n else "empty", n, data, None, sorted(keys)
    )


def _sql_texts(private: Path, catalog: dict, unit: dict) -> list[str]:
    if not str(catalog.get("engine") or "").startswith("ucanaccess-"):
        return []
    from toolkit.reference import catalog as cat

    by_id = {u["id"]: u for u in catalog.get("units") or []}
    out = []
    for uid in [unit["id"]] + list(unit.get("depends_on") or []):
        u = by_id.get(uid)
        if u is None:
            continue
        try:
            out.append(cat.unit_sql(private, u))
        except (OSError, KeyError, cat.CatalogError):
            continue
    return out


def ext_of(unit: dict) -> str:
    return ".json" if unit.get("kind") == "document" else ".csv"


def dataset_entry(
    outcome: Outcome, dataset: str, unit: dict, key: bytes | None
) -> dict:
    entry = {
        "status": outcome.status,
        "reason": outcome.reason,
        "rows": None,
        "digest": None,
    }
    if outcome.status in ("ok", "empty"):
        entry["rows"] = outcome.rows
        if dataset == "engagement":
            entry["digest"] = {
                "alg": "hmac-sha256",
                "value": canon.digest(outcome.data, key),
            }
        else:
            entry["digest"] = {"alg": "sha256", "value": canon.digest(outcome.data)}
            entry["expected"] = (
                f"synthetic/{dataset}/expected/{canon.file_safe(unit['id'])}{ext_of(unit)}"
            )
    return entry


def _existing(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return (
        doc
        if isinstance(doc, dict) and doc.get("format_version") == FORMAT_VERSION
        else {}
    )


def _kept_dataset(name: str, recorded: list[str], all_seeds: bool) -> bool:
    """True when an existing dataset entry survives this run (it was not recorded now)."""
    return name not in recorded and not (all_seeds and name.startswith("seed-"))


def record_datasets(
    eng: dict, eng_path: str, datasets: list[tuple[str, Path]], all_seeds: bool
) -> Recording:
    private = Path(eng["paths"]["private"])
    target = Path(eng["paths"]["target"])
    catalog = load_catalog(private, eng_path)
    rules = load_rules(target / "analysis" / "rules.json")
    units = recorded_units(catalog, rules)
    names = [n for n, _d in datasets]
    key = engagement_key(eng, eng_path) if "engagement" in names else None
    adapter = make_adapter(private)
    columns: dict[str, list | None] = {}
    described: set[str] = set()
    orders: dict[str, list | None] = {}
    texts = {u["id"]: _sql_texts(private, catalog, u) for u, _o in units}
    outcomes: dict[str, dict[str, Outcome]] = {u["id"]: {} for u, _o in units}
    failures: list[str] = []
    try:
        for name, directory in datasets:
            try:
                adapter.load_dataset(directory)
            except FileNotFoundError:
                raise GoldenError(
                    f"reference database for {name} missing, run:\n{build_command(name, eng_path)}"
                ) from None
            data = DatasetValues(directory)
            for unit, _obj in units:
                uid = unit["id"]
                try:
                    if uid not in described and unit.get("status") != "unsupported":
                        columns[uid] = _columns_of(adapter, unit)
                        orders[uid] = order_by(
                            texts[uid][0] if texts[uid] else None,
                            [c["name"] for c in columns[uid] or []],
                        )
                        described.add(uid)
                    outcomes[uid][name] = run_unit(
                        adapter,
                        unit,
                        columns.get(uid),
                        orders.get(uid),
                        texts[uid],
                        data,
                        name,
                    )
                except UnitFailed as exc:
                    failures.append(f"{uid} on {name}: {exc}")
                except canon.CanonicalError as exc:
                    failures.append(f"{uid} on {name}: {exc}")
    finally:
        closer = getattr(adapter, "close", None) or getattr(
            getattr(adapter, "_impl", None), "close", None
        )
        if closer is not None:
            closer()

    # manifest
    manifest_path = target / "goldens" / "manifest.json"
    old = _existing(manifest_path).get("units") or {}
    manifest = {"format_version": FORMAT_VERSION, "engagement": eng["id"], "units": {}}
    files: dict[Path, bytes] = {}
    for unit, obj in units:
        uid = unit["id"]
        prev = old.get(uid) or {}
        entry_datasets = {
            n: e
            for n, e in (prev.get("datasets") or {}).items()
            if _kept_dataset(n, names, all_seeds)
        }
        for name, _d in datasets:
            o = outcomes[uid].get(name)
            if o is None:
                continue
            entry_datasets[name] = dataset_entry(o, name, unit, key)
            if o.status in ("ok", "empty"):
                fname = canon.file_safe(uid) + ext_of(unit)
                if name == "engagement":
                    files[private / "goldens" / "engagement" / fname] = o.data
                else:
                    files[
                        target / "goldens" / "synthetic" / name / "expected" / fname
                    ] = o.data
        cols = columns.get(uid) if uid in described else prev.get("columns")
        manifest["units"][uid] = {
            "area": obj.get("area") if obj else None,
            "kind": unit.get("kind"),
            "canonical_version": canon.CANONICAL_VERSION,
            "columns": cols,
            "order_by": orders.get(uid) if uid in described else prev.get("order_by"),
            "needs_access_confirmation": bool(unit.get("needs_access_confirmation")),
            "datasets": entry_datasets,
        }

    # parameters
    params_path = target / "goldens" / "params.json"
    old_params = _existing(params_path).get("units") or {}
    public = {"format_version": FORMAT_VERSION, "units": {}}
    private_params = None
    if "engagement" in names:
        private_params = {"format_version": FORMAT_VERSION, "units": {}}
    for unit, _obj in units:
        uid = unit["id"]
        if not unit.get("params") and unit.get("kind") != "document":
            continue
        prev = old_params.get(uid) or {}
        values = {
            n: v
            for n, v in (prev.get("values") or {}).items()
            if n != "engagement" and _kept_dataset(n, names, all_seeds)
        }
        keys = {
            n: v
            for n, v in (prev.get("keys") or {}).items()
            if n != "engagement" and _kept_dataset(n, names, all_seeds)
        }
        for name, _d in datasets:
            o = outcomes[uid].get(name)
            if name == "engagement" or o is None:
                continue
            if o.values:
                values[name] = o.values
            if o.keys is not None:
                keys[name] = o.keys
        public["units"][uid] = {
            "params": list(unit.get("params") or []),
            "values": values,
            "keys": keys,
        }
        if private_params is not None:
            o = outcomes[uid].get("engagement")
            private_params["units"][uid] = {
                "values": {"engagement": o.values}
                if o is not None and o.values
                else {},
                "keys": {"engagement": o.keys}
                if o is not None and o.keys is not None
                else {},
            }

    owned = [
        target / "goldens" / "synthetic" / n / "expected"
        for n in names
        if n != "engagement"
    ]
    if "engagement" in names:
        owned.append(private / "goldens" / "engagement")
    return Recording(manifest, public, private_params, files, owned, outcomes, failures)


# ------------------------------------------------------------------ writing and checking


def _write(path: Path, data: bytes) -> bool:
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def _golden_files(directory: Path) -> set[Path]:
    if not directory.is_dir():
        return set()
    return {
        p for p in directory.iterdir() if p.is_file() and p.suffix in (".csv", ".json")
    }


def write_public(rec: Recording, target: Path) -> list[Path]:
    """Manifest, params and seed expected files; stale expected files are removed."""
    written = []
    goldens = target / "goldens"
    for path, data in (
        (goldens / "manifest.json", dumps(rec.manifest).encode("utf-8")),
        (goldens / "params.json", dumps(rec.public_params).encode("utf-8")),
    ):
        if _write(path, data):
            written.append(path)
    for d in rec.owned_dirs:
        if not d.is_relative_to(goldens):
            continue
        d.mkdir(parents=True, exist_ok=True)
        if _write(d / ".gitattributes", GITATTRIBUTES):
            written.append(d / ".gitattributes")
        wanted = {p for p in rec.files if p.parent == d}
        for stale in _golden_files(d) - wanted:
            stale.unlink()
            written.append(stale)
        for p in sorted(wanted):
            if _write(p, rec.files[p]):
                written.append(p)
    return written


def write_private(rec: Recording, private: Path) -> int:
    n = 0
    d = private / "goldens" / "engagement"
    d.mkdir(parents=True, exist_ok=True)
    wanted = {p for p in rec.files if p.parent == d}
    for stale in _golden_files(d) - wanted:
        stale.unlink()
        n += 1
    for p in sorted(wanted):
        n += int(_write(p, rec.files[p]))
    if rec.private_params is not None:
        n += int(
            _write(
                private / "goldens" / "params.json",
                dumps(rec.private_params).encode("utf-8"),
            )
        )
    return n


def _dir_diff(directory: Path, wanted: dict[Path, bytes], label: str) -> list[str]:
    out = []
    have = _golden_files(directory)
    for p in sorted(set(wanted) - have):
        out.append(f"{label}: {p.name} missing")
    for p in sorted(have - set(wanted)):
        out.append(f"{label}: {p.name} not produced")
    for p in sorted(set(wanted) & have):
        if p.read_bytes() != wanted[p]:
            out.append(f"{label}: {p.name} differs")
    return out


def check_public(rec: Recording, target: Path) -> list[str]:
    diffs = []
    goldens = target / "goldens"
    for name, obj in (
        ("manifest.json", rec.manifest),
        ("params.json", rec.public_params),
    ):
        path = goldens / name
        if not path.is_file():
            diffs.append(f"{name} missing")
        elif path.read_bytes() != dumps(obj).encode("utf-8"):
            diffs.append(f"{name} differs")
    for d in rec.owned_dirs:
        if d.is_relative_to(goldens):
            wanted = {p: b for p, b in rec.files.items() if p.parent == d}
            diffs += _dir_diff(d, wanted, d.parent.name)
            attrs = d / ".gitattributes"
            if not attrs.is_file():
                diffs.append(f"{d.parent.name}: .gitattributes missing")
            elif attrs.read_bytes() != GITATTRIBUTES:
                diffs.append(f"{d.parent.name}: .gitattributes differs")
    return diffs


def check_private(rec: Recording, private: Path) -> list[str]:
    """Differences with the private goldens, when they are present."""
    root = private / "goldens"
    if not root.is_dir() or not any(root.iterdir()):
        return []
    d = root / "engagement"
    wanted = {p: b for p, b in rec.files.items() if p.parent == d}
    diffs = _dir_diff(d, wanted, "engagement")
    if rec.private_params is not None:
        path = root / "params.json"
        if not path.is_file():
            diffs.append("private params.json missing")
        elif path.read_bytes() != dumps(rec.private_params).encode("utf-8"):
            diffs.append("private params.json differs")
    return diffs


def compare_engagement(rec: Recording, manifest_path: Path) -> list[tuple[str, str]]:
    """(unit id, what differs) between this engagement recording and the committed manifest:
    status, row count or digest, and units present on one side only."""
    committed = _existing(manifest_path).get("units")
    if committed is None:
        raise GoldenError(
            "goldens/manifest.json missing, run:\nmp-data golden record <eng> --dataset all"
        )
    out = []
    for uid in sorted(set(committed) | set(rec.manifest["units"])):
        new = ((rec.manifest["units"].get(uid) or {}).get("datasets") or {}).get(
            "engagement"
        )
        old = ((committed.get(uid) or {}).get("datasets") or {}).get("engagement")
        if new is None or old is None:
            out.append((uid, "unit missing on one side"))
            continue
        for k in ("status", "rows", "digest"):
            if new.get(k) != old.get(k):
                out.append((uid, k))
                break
    return out


# ------------------------------------------------------------------ status


def status_report(goldens: Path, rules_path: Path, dataset: str) -> dict:
    """Counts, rates and problems of the characterization rate (docs/contracts.md#pass-rate)."""
    manifest = read_json(Path(goldens) / "manifest.json", "goldens/manifest.json")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise GoldenError("goldens/manifest.json: unknown format_version")
    rules = load_rules(rules_path)
    areas = {a.get("id") for a in rules.get("areas") or []}
    objects = rules.get("objects") or []
    units = manifest.get("units") or {}
    problems = []
    by_status = {s: 0 for s in STATUSES}
    by_area: dict[str, dict[str, int]] = {}
    not_ok = []
    for uid in sorted(units):
        u = units[uid] or {}
        area = u.get("area")
        if area not in areas:
            problems.append(
                f"{uid}: area {area if area else 'null'} is not a rules.json area"
            )
        entry = (u.get("datasets") or {}).get(dataset)
        st = entry.get("status") if entry else "missing"
        reason = entry.get("reason") if entry else "missing"
        by_status[st] = by_status.get(st, 0) + 1
        by_area.setdefault(str(area), {}).setdefault(st, 0)
        by_area[str(area)][st] += 1
        if st != "ok":
            not_ok.append(
                {"id": uid, "status": st, "reason": reason, "dataset": dataset}
            )
    for o in objects:
        if o.get("type") not in UNIT_OBJECT_TYPES:
            continue
        kept, has_unit = o.get("decision") == "keep", o.get("name") in units
        if kept and not has_unit:
            problems.append(f"kept {o.get('type')} {o.get('name')} has no unit")
        elif not kept and has_unit:
            problems.append(
                f"{o.get('name')}: its object is not kept; re-record the goldens"
            )
    q = len(units)
    rate = by_status.get("ok", 0) / q if q else 0.0
    empty = by_status.get("empty", 0) / q if q else 0.0
    if q == 0:
        problems.append("the manifest holds no units")
    return {
        "q": q,
        "by_status": by_status,
        "by_area": by_area,
        "not_ok": not_ok,
        "rate": round(rate, 4),
        "empty_share": round(empty, 4),
        "problems": problems,
    }
