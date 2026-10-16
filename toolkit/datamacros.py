"""Access macros in AXL (the XML form of Access macros).

- `dataMacros/*.axl` parts hold table data macros: `parse_data_macros(xml, table)` gives
  `[{table, event, name, actions}]` for datamacros.json.
- `properties/*.axl` parts are object metadata, never data macros:
  `parse_properties(xml, name)` gives `{name, root, properties}` (property names only).
- `axl_actions(element)` flattens the statements of a data macro or a UI macro (standalone or
  embedded) into an ordered list of `{action, args, depth}`: an `Action` element gives its `Name`
  and arguments by name; block statements (If, ElseIf, Else, LookUpRecord, ForEachRecord,
  EditRecord, CreateRecord, Group, Sub ...) become entries named after the block, and the
  statements inside them follow with depth + 1. Comments are counted, never copied.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


def local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _text(el) -> str:
    return "".join(el.itertext()).strip()


def _children(el, name):
    return [c for c in el if local(c.tag) == name]


def _block_args(el) -> dict:
    args = {k: v for k, v in sorted(el.attrib.items()) if "}" not in k}
    for child in el:
        tag = local(child.tag)
        if tag in ("Statements", "Comment"):
            continue
        if tag == "Data":
            for k, v in sorted(child.attrib.items()):
                args[k] = v
            for sub in child:
                args[local(sub.tag)] = _text(sub)
        elif tag == "Condition":
            args["Condition"] = _text(child)
        elif tag == "Argument":
            args[child.get("Name") or "Argument"] = _text(child)
        elif len(child) == 0:
            args[tag] = _text(child)
    return args


def axl_actions(el, depth: int = 0) -> list[dict]:
    """Flatten the statements under `el` (any AXL element) in document order."""
    out: list[dict] = []
    for child in el:
        tag = local(child.tag)
        if tag in ("Comment", "Parameters", "Condition"):
            continue
        if tag in ("Statements", "DataMacro", "UserInterfaceMacro", "Macros"):
            out.extend(axl_actions(child, depth))
            continue
        if tag == "Action":
            args = {}
            for arg in _children(child, "Argument"):
                args[arg.get("Name") or str(len(args) + 1)] = _text(arg)
            out.append(
                {"action": child.get("Name") or "", "args": args, "depth": depth}
            )
            continue
        if tag == "ConditionalBlock":
            for branch in child:
                btag = local(branch.tag)
                args = {}
                cond = _children(branch, "Condition")
                if cond:
                    args["Condition"] = _text(cond[0])
                out.append({"action": btag, "args": args, "depth": depth})
                out.extend(axl_actions(branch, depth + 1))
            continue
        out.append({"action": tag, "args": _block_args(child), "depth": depth})
        for sub in child:
            if local(sub.tag) == "Statements":
                out.extend(axl_actions(sub, depth + 1))
    return out


def count_comments(el) -> int:
    return sum(1 for e in el.iter() if local(e.tag) == "Comment")


def parse_data_macros(xml_bytes: bytes, table: str) -> list[dict]:
    """Data macros of one dataMacros/<table>.axl part."""
    root = ET.fromstring(xml_bytes)
    macros = []
    nodes = (
        [root]
        if local(root.tag) == "DataMacro"
        else [e for e in root.iter() if local(e.tag) == "DataMacro"]
    )
    for dm in nodes:
        event = dm.get("Event")
        name = dm.get("Name")
        params = []
        for p in dm.iter():
            if local(p.tag) == "Parameter" and p.get("Name"):
                params.append(p.get("Name"))
        macros.append(
            {
                "table": table,
                "event": event or "Named",
                "name": name,
                "params": params,
                "actions": axl_actions(dm),
            }
        )
    return macros


def parse_properties(xml_bytes: bytes, name: str) -> dict:
    """Object metadata from a properties/*.axl part: the root tag and the property names."""
    root = ET.fromstring(xml_bytes)
    names = set()
    for e in root.iter():
        n = e.get("Name")
        if n:
            names.add(n)
    tags = sorted({local(e.tag) for e in root.iter()})
    return {
        "name": name,
        "root": local(root.tag),
        "properties": sorted(names),
        "tags": tags,
    }
