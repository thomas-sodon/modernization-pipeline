"""Access table designs (XSD with the officedata annotations) into schema.json tables.

`parse_xsd(xml_bytes)` returns `(tables, warnings)`; each table is
`{name, role, columns, primary_key, indexes, properties}` with the neutral column types of
docs/contracts.md#object-model. `neutral_type(jet_type, ...)` maps an `od:jetType` value.
Field and table properties are kept by name for the design facts S1 reads (captions,
descriptions, defaults, validation rules, lookups); parse scrubs them like every model string.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

XSD = "http://www.w3.org/2001/XMLSchema"
OD = "urn:schemas-microsoft-com:officedata"

NEUTRAL_TYPES = (
    "text",
    "memo",
    "byte",
    "integer",
    "longinteger",
    "autonumber",
    "single",
    "double",
    "currency",
    "decimal",
    "datetime",
    "yesno",
    "hyperlink",
    "oleobject",
    "complex",
    "guid",
)

_JET = {
    "text": "text",
    "memo": "memo",
    "byte": "byte",
    "integer": "integer",
    "longinteger": "longinteger",
    "autonumber": "autonumber",
    "single": "single",
    "double": "double",
    "currency": "currency",
    "decimal": "decimal",
    "numeric": "decimal",
    "datetime": "datetime",
    "date": "datetime",
    "datetimeextended": "datetime",
    "yesno": "yesno",
    "boolean": "yesno",
    "hyperlink": "hyperlink",
    "oleobject": "oleobject",
    "binary": "oleobject",
    "longbinary": "oleobject",
    "attachment": "complex",
    "complex": "complex",
    "replicationid": "guid",
    "guid": "guid",
    "bigint": "longinteger",
    "largenumber": "longinteger",
}

# od:fieldProperty / od:tableProperty names kept in the model (design facts)
FIELD_PROPS = (
    "Caption",
    "Description",
    "DefaultValue",
    "ValidationRule",
    "ValidationText",
    "Format",
    "InputMask",
    "DisplayControl",
    "RowSourceType",
    "RowSource",
    "BoundColumn",
    "ColumnCount",
    "LimitToList",
    "AllowMultipleValues",
    "Expression",
    "ResultType",
    "IMEMode",
    "AllowZeroLength",
    "TextFormat",
    "AppendOnly",
)
TABLE_PROPS = ("Description", "ValidationRule", "ValidationText", "Filter", "OrderBy")


_XML_NAME_ESC = re.compile(r"_x([0-9A-Fa-f]{4})_")


def xml_name(name: str | None) -> str | None:
    """Decode XML name escapes such as _x0020_ (space) in element names."""
    if name is None:
        return None
    return _XML_NAME_ESC.sub(lambda m: chr(int(m.group(1), 16)), name)


class SchemaError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code + (f" ({detail})" if detail else ""))


def neutral_type(jet: str | None) -> str | None:
    if not jet:
        return None
    return _JET.get(jet.strip().lower().replace(" ", "").replace("_", ""))


def _q(ns, tag):
    return f"{{{ns}}}{tag}"


def _props(appinfo_parent, tag: str, keep) -> dict:
    out = {}
    for ann in appinfo_parent.findall(_q(XSD, "annotation")):
        for appinfo in ann.findall(_q(XSD, "appinfo")):
            for p in appinfo.findall(_q(OD, tag)):
                name = p.get("name")
                if name in keep:
                    out[name] = p.get("value")
    return out


def _split_key(key: str, columns: list[str]) -> list[str]:
    """Split an od:index index-key into column names; names may contain spaces."""
    key = key.strip()
    out = []
    by_len = sorted(columns, key=len, reverse=True)
    while key:
        for col in by_len:
            if key == col or key.startswith(col + " "):
                out.append(col)
                key = key[len(col) :].strip()
                break
        else:
            word, _, key = key.partition(" ")
            out.append(word.strip("[]"))
            key = key.strip()
    return out


def _column(el, warnings, table) -> dict:
    name = xml_name(el.get("name"))
    jet = el.get(_q(OD, "jetType"))
    ntype = neutral_type(jet)
    complex_type = el.find(_q(XSD, "complexType")) is not None
    if el.get(_q(OD, "hyperlink")) == "yes":
        ntype = "hyperlink"
    if ntype is None:
        if complex_type:
            ntype = "complex"
        else:
            raise SchemaError(
                "unknown-jet-type", f"{table}.{name}: jetType {jet!s:.30}"
            )
    if ntype == "longinteger" and el.get(_q(OD, "autoUnique")) == "yes":
        ntype = "autonumber"
    size = precision = scale = None
    simple = el.find(_q(XSD, "simpleType"))
    if simple is not None:
        r = simple.find(_q(XSD, "restriction"))
        if r is not None:
            ml = r.find(_q(XSD, "maxLength"))
            td = r.find(_q(XSD, "totalDigits"))
            fd = r.find(_q(XSD, "fractionDigits"))
            if ml is not None and ntype in ("text", "hyperlink"):
                size = int(ml.get("value"))
            if td is not None:
                precision = int(td.get("value"))
            if fd is not None:
                scale = int(fd.get("value"))
    required = (
        el.get(_q(OD, "nonNullable")) == "yes"
        or el.get("minOccurs") == "1"
        or ntype == "autonumber"
    )
    col = {
        "name": name,
        "type": ntype,
        "size": size,
        "precision": precision,
        "scale": scale,
        "required": required,
        "source_type": jet,
    }
    sql_type = el.get(_q(OD, "sqlSType"))
    if sql_type:
        col["sql_type"] = sql_type
    props = _props(el, "fieldProperty", FIELD_PROPS)
    if props:
        col["properties"] = props
    if props.get("Expression"):
        col["computed"] = True
        col["formula"] = props["Expression"]
    return col


def parse_xsd(xml_bytes: bytes) -> tuple[list[dict], list[dict]]:
    root = ET.fromstring(xml_bytes)
    if root.tag != _q(XSD, "schema"):
        raise SchemaError("not-an-xsd", root.tag.rsplit("}", 1)[-1][:40])
    warnings: list[dict] = []
    tables = []
    for el in root.findall(_q(XSD, "element")):
        name = xml_name(el.get("name"))
        if name == "dataroot" or not name:
            continue
        seq = el.find(f"{_q(XSD, 'complexType')}/{_q(XSD, 'sequence')}")
        columns = []
        if seq is not None:
            for c in seq.findall(_q(XSD, "element")):
                columns.append(_column(c, warnings, name))
        col_names = [c["name"] for c in columns]
        indexes = []
        pk: list[str] = []
        for ann in el.findall(_q(XSD, "annotation")):
            for appinfo in ann.findall(_q(XSD, "appinfo")):
                for idx in appinfo.findall(_q(OD, "index")):
                    cols = _split_key(idx.get("index-key") or "", col_names)
                    entry = {
                        "name": idx.get("index-name"),
                        "columns": cols,
                        "unique": idx.get("unique") == "yes",
                    }
                    if idx.get("primary") == "yes":
                        pk = cols
                        entry["primary"] = True
                    indexes.append(entry)
        table = {
            "name": name,
            "role": "data",
            "columns": columns,
            "primary_key": pk,
            "indexes": sorted(indexes, key=lambda i: i["name"] or ""),
        }
        tprops = _props(el, "tableProperty", TABLE_PROPS)
        if tprops:
            table["properties"] = tprops
        tables.append(table)
    return tables, warnings
