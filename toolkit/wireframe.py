"""SVG wireframes of Access forms, drawn from the object model (docs/formats/model.md).

S6.visuals draws a 'before' picture of every form behind a kept workflow from the layout facts
S0.parse records in `out/model/objects.json`: `forms[].sections[]` (name, kind, height) and
`forms[].controls[]` (name, type, section, parent, page, caption, left, top, width, height,
source_object, visible), plus `forms[].tab_pages[]`. Positions are twips; 1 px = 15 twips.

Only design facts reach the picture: section names, control types and names, label and button
captions, subform source form names and tab page captions. Row sources, control sources, default
values, expressions, events and code are never read. Output is byte-stable: attributes are
written in sorted order, numbers in one fixed format, and nothing depends on the clock, the
machine or dict order of the input.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

TWIPS_PER_PX = 15
CAPTION = "Reconstructed from the Access form definition; not a screenshot"
FONT = "sans-serif"
LAYOUT_MESSAGE = "model has no form layout: S0.parse must record it"

SECTION_LABEL_PX = 16  # band label strip above each section
TITLE_PX = 28
FOOTER_PX = 26
MARGIN_PX = 8
CHAR_PX = 6.2  # rough average glyph width at 11px, used to shorten long captions

SECTION_TITLES = {
    "form_header": "Form header",
    "page_header": "Page header",
    "detail": "Detail",
    "page_footer": "Page footer",
    "form_footer": "Form footer",
    "group_header": "Group header",
    "group_footer": "Group footer",
}
SECTION_ORDER = ["form_header", "page_header", "detail", "page_footer", "form_footer"]

# Size used when the model records 0 for a control's width or height (Access then takes it from
# the form's default block for that type, which parse does not always resolve).
DEFAULT_SIZE = {
    "CheckBox": (240, 240),
    "OptionButton": (240, 240),
    "ToggleButton": (1440, 360),
    "CommandButton": (1440, 360),
    "Label": (1440, 300),
    "TextBox": (1440, 300),
    "ComboBox": (1440, 300),
    "ListBox": (1440, 1440),
}
FALLBACK_SIZE = (1440, 300)
LAYOUT_FIELDS = ("left", "top", "width", "height")

# Types drawn without an outline of their own or not at all.
SKIPPED_TYPES = {"EmptyCell", "Page", "PageBreak"}


class InputError(Exception):
    """Missing or unreadable model or rules file (exit 2)."""


class MissingForms(Exception):
    """Forms named in the rules file that the model does not hold (exit 1)."""

    def __init__(self, names: list[str]):
        super().__init__("forms missing from the model: " + ", ".join(names))
        self.names = names


class NoLayout(Exception):
    """The model lacks the layout fields (exit 2; S0.parse must record them)."""

    def __init__(self, missing: list[str]):
        super().__init__(f"{LAYOUT_MESSAGE} (missing: {', '.join(missing)})")
        self.missing = missing


# ---------------------------------------------------------------------------------------------
# inputs


def _read_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise InputError(f"{what} missing: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"{what} unreadable: {path.name}: {type(exc).__name__}") from None
    if not isinstance(doc, dict):
        raise InputError(f"{what} is not a JSON object: {path.name}")
    return doc


def load_model_forms(model_dir) -> dict[str, dict]:
    """Forms of `<model_dir>/objects.json` by name."""
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise InputError(f"model directory missing: {model_dir}")
    doc = _read_json(model_dir / "objects.json", "model objects.json")
    forms = doc.get("forms")
    if not isinstance(forms, list):
        raise InputError("model objects.json has no forms list")
    return {f["name"]: f for f in forms if isinstance(f, dict) and isinstance(f.get("name"), str)}


def load_workflows(rules_file) -> list[dict]:
    """rules.json workflows[] as {id, title, forms} in file order."""
    doc = _read_json(Path(rules_file), "rules file")
    flows = doc.get("workflows")
    if not isinstance(flows, list):
        raise InputError("rules file has no workflows list")
    out = []
    for wf in flows:
        if not isinstance(wf, dict):
            continue
        forms = [f for f in (wf.get("forms") or []) if isinstance(f, str)]
        out.append({"id": str(wf.get("id", "")), "title": str(wf.get("title", "")), "forms": forms})
    return out


def wanted_forms(workflows: list[dict]) -> list[str]:
    return sorted({f for wf in workflows for f in wf["forms"]})


def check_layout(forms: list[dict]) -> None:
    """Raise NoLayout naming the layout fields the model does not record."""
    missing: set[str] = set()
    for form in forms:
        if not isinstance(form.get("sections"), list) or not form["sections"]:
            missing.add("forms[].sections")
        else:
            for sec in form["sections"]:
                for key in ("name", "kind", "height"):
                    if key not in sec:
                        missing.add(f"forms[].sections[].{key}")
        controls = form.get("controls")
        if not isinstance(controls, list):
            missing.add("forms[].controls")
            continue
        for ctl in controls:
            for key in ("type", "name", "section"):
                if not isinstance(ctl.get(key), str):
                    missing.add(f"forms[].controls[].{key}")
            for key in LAYOUT_FIELDS:
                value = ctl.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    missing.add(f"forms[].controls[].{key}")
    if missing:
        raise NoLayout(sorted(missing))


# ---------------------------------------------------------------------------------------------
# helpers


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "form"


def slugs(names: list[str]) -> dict[str, str]:
    """File stems per form; names that collide get the first 8 hex digits of their SHA-256."""
    base = {n: slug(n) for n in names}
    seen: dict[str, int] = {}
    for s in base.values():
        seen[s] = seen.get(s, 0) + 1
    return {
        n: (s if seen[s] == 1 else f"{s}-{hashlib.sha256(n.encode('utf-8')).hexdigest()[:8]}")
        for n, s in base.items()
    }


def num(value: float) -> str:
    """Fixed number format: at most two decimals, no trailing zeros, no '-0'."""
    text = f"{round(float(value), 2):.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def px(twips) -> float:
    return float(twips or 0) / TWIPS_PER_PX


def display_caption(text) -> str:
    """Access caption as shown: '&' marks the access key, '&&' is a literal ampersand."""
    if not isinstance(text, str):
        return ""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


def fit(text: str, width_px: float) -> str:
    limit = max(1, int(width_px / CHAR_PX))
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _el(parent, tag: str, attrs: dict | None = None, text: str | None = None):
    attrs = attrs or {}
    node = ET.SubElement(parent, tag, {k: str(attrs[k]) for k in sorted(attrs)})
    if text is not None:
        node.text = one_line(text)
    return node


LINE_BREAK = re.compile(r"\r\n|\r|\n")
LINE_PX = 13.0  # line height for multi-line captions at 11px


def caption_lines(text: str) -> list[str]:
    """A caption split at its line breaks (CR LF, CR or LF): Access shows each on its own line."""
    return LINE_BREAK.split(text) if text else [""]


def one_line(text: str) -> str:
    """Text with every line break or other control character turned into a space.

    A raw CR or LF inside <text> is ignored by SVG renderers and rewritten by git when the target
    repo normalizes line endings, which would make --check fail on a fresh checkout.
    """
    text = LINE_BREAK.sub(" ", text)
    return "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in text)


def _text(parent, x, y, text, size=11, fill="#222", anchor="start", weight=None, italic=False):
    attrs = {
        "x": num(x),
        "y": num(y),
        "font-family": FONT,
        "font-size": num(size),
        "fill": fill,
    }
    if anchor != "start":
        attrs["text-anchor"] = anchor
    if weight:
        attrs["font-weight"] = weight
    if italic:
        attrs["font-style"] = "italic"
    return _el(parent, "text", attrs, text)


def _caption_text(parent, x, mid, caption: str, width_px: float, anchor="start"):
    """A caption: one <text>, with one <tspan> per line when the caption has line breaks."""
    lines = caption_lines(caption)
    if len(lines) == 1:
        return _text(parent, x, mid, fit(lines[0], width_px), anchor=anchor)
    first = mid - (len(lines) - 1) * LINE_PX / 2
    node = _text(parent, x, first, None, anchor=anchor)
    for i, line in enumerate(lines):
        _el(node, "tspan", {"x": num(x), "y": num(first + i * LINE_PX)}, fit(line, width_px))
    return node


def _rect(parent, x, y, w, h, fill="none", stroke="#555", **extra):
    attrs = {
        "x": num(x),
        "y": num(y),
        "width": num(max(w, 0)),
        "height": num(max(h, 0)),
        "fill": fill,
        "stroke": stroke,
    }
    attrs.update({k.replace("_", "-"): v for k, v in extra.items()})
    return _el(parent, "rect", attrs)


def _line(parent, x1, y1, x2, y2, stroke="#555", **extra):
    attrs = {"x1": num(x1), "y1": num(y1), "x2": num(x2), "y2": num(y2), "stroke": stroke}
    attrs.update({k.replace("_", "-"): v for k, v in extra.items()})
    return _el(parent, "line", attrs)


def _size(ctl: dict) -> tuple[float, float]:
    w = ctl.get("width") or 0
    h = ctl.get("height") or 0
    dw, dh = DEFAULT_SIZE.get(ctl.get("type"), FALLBACK_SIZE)
    if ctl.get("type") == "Edge":
        return px(w), px(h)
    return px(w if w > 0 else dw), px(h if h > 0 else dh)


# ---------------------------------------------------------------------------------------------
# drawing


def _ordered_sections(form: dict) -> list[dict]:
    secs = [s for s in form.get("sections") or [] if isinstance(s, dict)]
    rank = {k: i for i, k in enumerate(SECTION_ORDER)}
    ordered = sorted(enumerate(secs), key=lambda p: (rank.get(p[1].get("kind"), 2), p[0]))
    return [s for _, s in ordered]


def _hidden_pages(form: dict) -> dict[str, set[str]]:
    """Per tab control, the pages other than the first: their controls are not drawn."""
    pages: dict[str, list[dict]] = {}
    for tp in form.get("tab_pages") or []:
        if isinstance(tp, dict) and tp.get("tab_control"):
            pages.setdefault(tp["tab_control"], []).append(tp)
    out = {}
    for tab, tps in pages.items():
        tps = sorted(tps, key=lambda t: (t.get("index") if isinstance(t.get("index"), int) else 0))
        out[tab] = {t.get("name") for t in tps[1:]}
    return out


def _draw_control(g, ctl: dict, x: float, y: float, w: float, h: float, tab_pages: list[dict],
                  hidden_count: int) -> None:
    kind = ctl.get("type") or ""
    name = ctl.get("name") or ""
    caption = display_caption(ctl.get("caption"))
    mid = y + h / 2 + 4
    if kind == "Label":
        _caption_text(g, x + 2, mid, caption, w)
    elif kind in ("TextBox", "Attachment"):
        _rect(g, x, y, w, h, fill="#fff", stroke="#888")
        label = name if kind == "TextBox" else f"attachment {name}"
        _text(g, x + 3, mid, fit(label, w - 4), fill="#999")
    elif kind in ("ComboBox", "ListBox"):
        _rect(g, x, y, w, h, fill="#fff", stroke="#888")
        arrow = min(14.0, max(w / 4, 6.0))
        ax = x + w - arrow
        _rect(g, ax, y, arrow, min(h, 20.0), fill="#e8e8e8", stroke="#888")
        ah = min(h, 20.0)
        _el(g, "path", {
            "d": f"M{num(ax + arrow * 0.25)} {num(y + ah * 0.4)} "
                 f"L{num(ax + arrow * 0.75)} {num(y + ah * 0.4)} "
                 f"L{num(ax + arrow / 2)} {num(y + ah * 0.65)} Z",
            "fill": "#555",
        })
        _text(g, x + 3, y + min(h, 20.0) / 2 + 4, fit(name, w - arrow - 4), fill="#999")
    elif kind in ("CommandButton", "ToggleButton"):
        _rect(g, x, y, w, h, fill="#eef1f6", stroke="#667", rx="4", ry="4")
        _caption_text(g, x + w / 2, mid, caption or name, w - 4, anchor="middle")
    elif kind == "CheckBox":
        side = min(w, h, 12.0)
        _rect(g, x, y + (h - side) / 2, side, side, fill="#fff", stroke="#555")
    elif kind == "OptionButton":
        r = min(w, h, 12.0) / 2
        _el(g, "circle", {"cx": num(x + r), "cy": num(y + h / 2), "r": num(r), "fill": "#fff",
                          "stroke": "#555"})
    elif kind == "OptionGroup":
        _rect(g, x, y, w, h, stroke="#777", rx="2", ry="2")
        _text(g, x + 4, y + 11, fit(f"option group {name}", w - 8), size=9, fill="#777")
    elif kind == "Subform":
        _rect(g, x, y, w, h, fill="#fafafa", stroke="#777", stroke_dasharray="6 4")
        src = ctl.get("source_object")
        label = f"subform: {src}" if isinstance(src, str) and src else f"subform {name}"
        _text(g, x + w / 2, y + h / 2 + 4, fit(label, w - 8), fill="#666", anchor="middle")
    elif kind == "Tab":
        _draw_tab(g, x, y, w, h, tab_pages, hidden_count)
    elif kind in ("Image", "ObjectFrame", "BoundObjectFrame", "UnboundObjectFrame", "Chart"):
        _rect(g, x, y, w, h, fill="#f4f4f4", stroke="#888")
        _line(g, x, y, x + w, y + h, stroke="#bbb")
        _line(g, x + w, y, x, y + h, stroke="#bbb")
        _text(g, x + w / 2, y + h / 2 + 4, "image" if kind == "Image" else kind.lower(),
              size=10, fill="#777", anchor="middle")
    elif kind == "Rectangle":
        _rect(g, x, y, w, h, stroke="#bbb")
    elif kind == "Edge":
        _line(g, x, y, x + w, y + h, stroke="#999")
    else:
        _rect(g, x, y, w, h, stroke="#aaa", stroke_dasharray="2 2")
        _text(g, x + 2, mid, fit(f"{kind} {name}".strip(), w - 4), size=9, fill="#999")


def _draw_tab(g, x, y, w, h, pages: list[dict], hidden_count: int) -> None:
    strip = 20.0
    _rect(g, x, y + strip, w, max(h - strip, 0), fill="#fcfcfc", stroke="#777")
    tx = x
    for i, tp in enumerate(pages):
        cap = display_caption(tp.get("caption")) or str(tp.get("name") or "")
        tw = min(max(len(cap) * CHAR_PX + 14, 40.0), max(x + w - tx, 0))
        if tw <= 0:
            break
        _rect(g, tx, y, tw, strip, fill="#fff" if i == 0 else "#ececec", stroke="#777")
        _text(g, tx + tw / 2, y + 14, fit(cap, tw - 6), size=10, anchor="middle",
              weight="bold" if i == 0 else None)
        tx += tw
    if hidden_count:
        _text(g, x + w - 4, y + h - 5, f"{hidden_count} more controls on other pages",
              size=9, fill="#999", anchor="end")


def render_form(form: dict) -> tuple[str, int]:
    """SVG text for one form and the number of controls drawn."""
    name = form.get("name") or ""
    sections = _ordered_sections(form)
    controls = [c for c in form.get("controls") or [] if isinstance(c, dict)]
    pages_by_tab: dict[str, list[dict]] = {}
    for tp in form.get("tab_pages") or []:
        if isinstance(tp, dict):
            pages_by_tab.setdefault(tp.get("tab_control"), []).append(tp)
    for tps in pages_by_tab.values():
        tps.sort(key=lambda t: (t.get("index") if isinstance(t.get("index"), int) else 0))
    hidden = _hidden_pages(form)
    hidden_page_names = set().union(*hidden.values()) if hidden else set()

    drawn = [c for c in controls if c.get("type") not in SKIPPED_TYPES
             and c.get("page") not in hidden_page_names]
    hidden_per_tab = {
        tab: sum(1 for c in controls if c.get("page") in names) for tab, names in hidden.items()
    }

    # canvas width: the form width or the right-most control, whichever is larger
    right = px(form.get("width") or 0)
    for c in drawn:
        w, _h = _size(c)
        right = max(right, px(c.get("left") or 0) + w)
    content_w = max(right, 240.0)

    # vertical layout of the section bands
    tops: dict[str, float] = {}
    heights: dict[str, float] = {}
    y = TITLE_PX + MARGIN_PX
    for sec in sections:
        sh = px(sec.get("height") or 0)
        for c in drawn:
            if c.get("section") == sec.get("name"):
                _w, ch = _size(c)
                sh = max(sh, px(c.get("top") or 0) + ch)
        tops[sec.get("name")] = y + SECTION_LABEL_PX
        heights[sec.get("name")] = sh
        y += SECTION_LABEL_PX + sh
    total_w = content_w + 2 * MARGIN_PX
    total_h = y + MARGIN_PX + FOOTER_PX

    svg = ET.Element("svg", {
        "height": num(total_h),
        "role": "img",
        "viewBox": f"0 0 {num(total_w)} {num(total_h)}",
        "width": num(total_w),
        "xmlns": "http://www.w3.org/2000/svg",
    })
    _el(svg, "title", None, f"{name}: {CAPTION}")
    _rect(svg, 0, 0, total_w, total_h, fill="#ffffff", stroke="none")
    # The form's own caption is not drawn: Access saves whatever it showed last (often a record
    # value such as a name), so only the form name heads the picture.
    _text(svg, MARGIN_PX, 19, name, size=14, weight="bold")

    for sec in sections:
        sname = sec.get("name")
        top = tops[sname]
        band = _el(svg, "g", {"class": "section", "data-kind": str(sec.get("kind") or ""),
                              "data-name": str(sname or "")})
        _rect(band, MARGIN_PX, top - SECTION_LABEL_PX, content_w, SECTION_LABEL_PX,
              fill="#dfe3ea", stroke="#9aa")
        title = SECTION_TITLES.get(sec.get("kind"), str(sec.get("kind") or "section"))
        _text(band, MARGIN_PX + 4, top - 4, f"{title} ({sname})", size=10, fill="#334")
        fill = "#f7f7f7" if sec.get("kind") == "detail" else "#eef0f3"
        _rect(band, MARGIN_PX, top, content_w, heights[sname], fill=fill, stroke="#9aa")

    count = 0
    for c in drawn:
        sname = c.get("section")
        if sname not in tops:
            continue
        w, h = _size(c)
        x = MARGIN_PX + px(c.get("left") or 0)
        cy = tops[sname] + px(c.get("top") or 0)
        attrs = {"class": "ctl", "data-name": str(c.get("name") or ""),
                 "data-type": str(c.get("type") or "")}
        if c.get("visible") is False:
            attrs["opacity"] = "0.45"
        g = _el(svg, "g", attrs)
        _draw_control(g, c, x, cy, w, h, pages_by_tab.get(c.get("name"), []),
                      hidden_per_tab.get(c.get("name"), 0))
        count += 1

    _text(svg, MARGIN_PX, total_h - 9, CAPTION, size=11, fill="#555", italic=True)
    ET.indent(svg, space=" ")
    for node in svg.iter("text"):
        for span in node:  # keep multi-line text free of indentation whitespace
            node.text = None
            span.tail = None
    body = ET.tostring(svg, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n", count


def render_index(workflows: list[dict], names: dict[str, str]) -> str:
    lines = [
        "# Before: the Access forms behind the kept workflows",
        "",
        (
            "Wireframes drawn by `toolkit wireframe` from the form definitions in the object "
            "model (sections, control types, positions, label captions and control names). "
            "They show no data, row sources, control sources, default values, expressions or "
            "code."
        ),
        "",
    ]
    for wf in workflows:
        lines.append(f"## {wf['id']}: {wf['title']}")
        lines.append("")
        if not wf["forms"]:
            lines.append("No forms.")
            lines.append("")
        for form in wf["forms"]:
            lines.append(f"### {form}")
            lines.append("")
            lines.append(f"![{form}]({names[form]}.svg)")
            lines.append("")
            lines.append(f"*{CAPTION}.*")
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_all(model_dir, rules_file) -> tuple[dict[str, bytes], dict]:
    """All outputs as {file name: bytes} plus counts. Raises InputError, MissingForms, NoLayout."""
    forms = load_model_forms(model_dir)
    workflows = load_workflows(rules_file)
    wanted = wanted_forms(workflows)
    missing = [n for n in wanted if n not in forms]
    if missing:
        raise MissingForms(missing)
    check_layout([forms[n] for n in wanted])
    names = slugs(wanted)
    files: dict[str, bytes] = {}
    controls = 0
    for n in wanted:
        text, count = render_form(forms[n])
        controls += count
        files[f"{names[n]}.svg"] = text.encode("utf-8")
    files["index.md"] = render_index(workflows, names).encode("utf-8")
    counts = {"workflows": len(workflows), "forms": len(wanted), "controls": controls}
    return files, counts


def compare(files: dict[str, bytes], out_dir) -> list[str]:
    """Problems between the rendered files and out_dir: missing, extra .svg, changed."""
    out = Path(out_dir)
    problems = []
    for name in sorted(files):
        p = out / name
        if not p.is_file():
            problems.append(f"missing: {name}")
        elif p.read_bytes() != files[name]:
            problems.append(f"changed: {name}")
    if out.is_dir():
        for p in sorted(out.glob("*.svg")):
            if p.name not in files:
                problems.append(f"extra: {p.name}")
    return problems


def write(files: dict[str, bytes], out_dir) -> tuple[int, int]:
    """Write the files and delete .svg files this run did not produce: (written, deleted)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for name in sorted(files):
        p = out / name
        if not p.is_file() or p.read_bytes() != files[name]:
            p.write_bytes(files[name])
            written += 1
    deleted = 0
    for p in sorted(out.glob("*.svg")):
        if p.name not in files:
            p.unlink()
            deleted += 1
    return written, deleted
