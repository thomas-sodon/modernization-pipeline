"""Write the object model (docs/contracts.md#object-model).

`finalize(model)` names each module's code file (`code/<file-safe module name>.txt`) and returns
the four documents and the code files; `validate(docs)` checks each document against its `$defs`
entry in toolkit/schemas/model.schema.json; `write(out_dir, docs, code_files)` writes them as
UTF-8 with LF, keys sorted (docs/contracts.md#conventions), and removes code files a previous run
left behind, so two runs over the same source give identical bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "model.schema.json"
FILES = ("schema", "objects", "procedures", "datamacros")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def file_safe(name: str) -> str:
    """File-safe id (docs/contracts.md#conventions)."""
    safe = _UNSAFE.sub("_", name)
    if safe != name:
        safe += "-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return safe


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def finalize(model: dict) -> tuple[dict, dict]:
    """(docs by file stem, code files by relative path)."""
    code_files: dict[str, str] = {}
    for module in model["procedures"]["modules"]:
        text = model["code"].get(module["name"])
        if text is None:
            module["code_file"] = None
            continue
        rel = f"code/{file_safe(module['name'])}.txt"
        if rel in code_files:
            raise ValueError("two modules share a code file name")
        module["code_file"] = rel
        code_files[rel] = text
    docs = {stem: model[stem] for stem in FILES}
    return docs, code_files


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(docs: dict) -> list[str]:
    """Problems as '<file>.json: <json path>: <validator>' (never instance values)."""
    schema = _schema()
    problems = []
    for stem in FILES:
        sub = {"$ref": f"#/$defs/{stem}", "$defs": schema["$defs"]}
        validator = jsonschema.Draft202012Validator(sub)
        for err in sorted(
            validator.iter_errors(docs[stem]), key=lambda e: list(e.absolute_path)
        ):
            path = "$" + "".join(
                f"[{p}]" if isinstance(p, int) else f".{p}" for p in err.absolute_path
            )
            problems.append(f"{stem}.json: {path}: {err.validator}")
    return problems


def write(out_dir: Path, docs: dict, code_files: dict) -> list[Path]:
    out_dir = Path(out_dir)
    (out_dir / "code").mkdir(parents=True, exist_ok=True)
    written = []
    for stem in FILES:
        path = out_dir / f"{stem}.json"
        path.write_text(dumps(docs[stem]), encoding="utf-8", newline="\n")
        written.append(path)
    keep = set()
    for rel, text in sorted(code_files.items()):
        path = out_dir / rel
        path.write_text(text, encoding="utf-8", newline="\n")
        keep.add(path.name)
        written.append(path)
    for stale in (out_dir / "code").iterdir():
        if stale.is_file() and stale.name not in keep:
            stale.unlink()
    return written
