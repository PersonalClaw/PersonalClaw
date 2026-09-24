"""Declarative report specs → sanitized, self-contained HTML (KNOWLEDGE-SYNTHESIS §6.2).

A report SPEC is data: a title plus an ordered list of blocks (markdown prose, a table with
sort/filter/compute ops, an xychart). Rendering is a pure function of ``(spec, data)`` — which is
the whole point of the split. The spec is the versioned record; the HTML is a derived export a
periodic synthesizer regenerates from fresh data without a model call.

## Threat model — why this renders instead of sanitizing

Every string in a spec may be LLM output derived from untrusted web or inbox material, so the
rendered document is treated as hostile input all the way through. The document is served from the
artifacts area, same-origin with the dashboard, so a single surviving ``<script>`` would run with
the user's session.

The control is an **allowlist of zero tags**: no caller-supplied markup ever reaches the output as
markup. Text is passed through the repo's nh3 sanitizer (``web.extract.sanitize_html``, which drops
``<script>``/``<style>`` bodies and event handlers), then HTML-escaped, then placed into structure
this module generates itself. URLs from a spec are admitted only for ``http``/``https``/``mailto``,
so ``javascript:`` and ``data:`` links degrade to plain text.

nh3's own default allowlist is deliberately NOT used as the last line: it permits ``<img src>`` and
``<a>`` unchanged, which is safe-from-script but not **self-contained** — a report that fetches a
remote image beacons every time it is opened. Self-containment is a security property here, not a
convenience.

``assert_self_contained`` is the belt-and-braces invariant over the finished document: it fails
CLOSED on any script, event handler, external reference, or embedded frame. It should never fire —
if it does, a renderer changed and the export is refused rather than shipped.
"""

from __future__ import annotations

import html
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from personalclaw.web.extract import sanitize_html

#: Block kinds this renderer understands. A spec naming anything else is a spec error, reported to
#: the author — not silently dropped, which would render a confidently incomplete report.
BLOCK_KINDS = ("markdown", "table", "xychart")

#: URL schemes admitted from a spec. Relative links are also allowed (they stay inside the
#: dashboard's own origin); everything else renders as inert text.
ALLOWED_URL_SCHEMES = ("http://", "https://", "mailto:")

#: Caps. A report is a human-readable page, not a data dump: an unbounded spec would render a
#: multi-megabyte document that the artifact store then refuses at its own 1 MiB body cap, so the
#: truncation happens here where it can be reported.
MAX_BLOCKS = 50
MAX_TABLE_ROWS = 500
MAX_TABLE_COLUMNS = 24
MAX_SERIES = 8
MAX_POINTS = 200
MAX_TEXT_CHARS = 20_000

#: Series colours, fixed so a regenerated report is byte-identical when the data has not moved.
_SERIES_COLORS = (
    "#4f46e5",
    "#0891b2",
    "#ca8a04",
    "#dc2626",
    "#16a34a",
    "#9333ea",
    "#db2777",
    "#475569",
)

#: The stylesheet is inline and constant. No caller data reaches it, so there is no injection path
#: through CSS — and an external stylesheet would break self-containment.
_STYLE = """
:root { color-scheme: light dark; }
body { margin: 0; padding: 2rem 1.25rem; font: 15px/1.6 system-ui, sans-serif;
       color: #1f2937; background: #ffffff; }
main { max-width: 52rem; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 1.5rem; }
h2 { font-size: 1.25rem; margin: 2rem 0 .5rem; }
h3 { font-size: 1.05rem; margin: 1.5rem 0 .5rem; }
p, ul, ol { margin: .5rem 0; }
code { font-family: ui-monospace, monospace; background: #f3f4f6; padding: .1em .3em;
       border-radius: 3px; }
pre { background: #f3f4f6; padding: .75rem; border-radius: 6px; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: .75rem 0; font-size: 14px; }
th, td { border: 1px solid #e5e7eb; padding: .4rem .6rem; text-align: left; }
th { background: #f9fafb; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
figure { margin: 1rem 0; }
figcaption { font-size: 13px; color: #6b7280; margin-top: .35rem; }
.empty { color: #6b7280; font-style: italic; }
@media (prefers-color-scheme: dark) {
  body { color: #e5e7eb; background: #111827; }
  code, pre, th { background: #1f2937; }
  th, td { border-color: #374151; }
  figcaption, .empty { color: #9ca3af; }
}
"""

#: Patterns that must not survive into a finished export. Checked over the whole document as an
#: invariant, never as the primary control — a renderer that relied on this list would be one
#: unlisted attribute away from a hole.
_FORBIDDEN = (
    (re.compile(r"<\s*script", re.IGNORECASE), "a script element"),
    (re.compile(r"<\s*(iframe|object|embed|frame|link|base)\b", re.IGNORECASE), "an embed"),
    # `<meta charset>` is this renderer's own inert declaration and must pass; `http-equiv` is not
    # inert — `refresh` is a navigation, and `Content-Security-Policy` would let a document relax
    # its own containment. So the rule is scoped to the attributes, not to the tag.
    (re.compile(r"<\s*meta\b[^>]*\b(http-equiv|content)\s*=", re.IGNORECASE), "a meta directive"),
    (re.compile(r"<\s*[a-z][^>]*\son[a-z]+\s*=", re.IGNORECASE), "an inline event handler"),
    (
        re.compile(r"(?:href|src|xlink:href|action|srcdoc|data)\s*=\s*[\"']?\s*javascript:", re.I),
        "a javascript: URL",
    ),
    (
        re.compile(r"(?:src|xlink:href|srcset)\s*=\s*[\"']?\s*(?:https?:|//)", re.I),
        "a remote reference",
    ),
    (re.compile(r"@import", re.IGNORECASE), "a CSS import"),
    (re.compile(r"<\s*foreignObject", re.IGNORECASE), "an SVG foreignObject"),
)


class SpecError(ValueError):
    """The spec is malformed. Reported to the author rather than rendered around."""


@dataclass
class RenderedReport:
    """A render result: the document plus what the renderer had to say about the spec."""

    html: str
    #: Non-fatal spec complaints (a truncated table, a chart with no numeric series). Surfaced in
    #: the action's output so a template author sees them instead of wondering why a block is thin.
    warnings: list[str] = field(default_factory=list)
    block_count: int = 0


def canonical_spec_text(spec: dict[str, Any]) -> str:
    """The spec's stored form: sorted-key JSON, so an unchanged spec is byte-identical.

    Byte-identity is what keeps the versioned record honest — a periodic synthesizer that
    re-submitted the same spec with dict ordering shuffled would otherwise mint a version per run
    and bury the versions a human authored.
    """
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def parse_spec(raw: Any) -> dict[str, Any]:
    """Accept a spec as an object or as JSON text; reject anything else loudly."""
    if isinstance(raw, dict):
        spec = raw
    elif isinstance(raw, str):
        try:
            spec = json.loads(raw)
        except ValueError as exc:
            raise SpecError(f"spec is not valid JSON: {exc}") from exc
        if not isinstance(spec, dict):
            raise SpecError("spec JSON must be an object with a 'blocks' list")
    else:
        raise SpecError("spec must be an object or a JSON string")

    blocks = spec.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise SpecError("spec needs a non-empty 'blocks' list")
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise SpecError(f"block {index} is not an object")
        kind = str(block.get("type", "") or "")
        if kind not in BLOCK_KINDS:
            raise SpecError(f"block {index} type {kind!r} must be one of: {', '.join(BLOCK_KINDS)}")
    return spec


def render_report(spec: dict[str, Any], data: dict[str, Any] | None = None) -> RenderedReport:
    """Render a validated spec against ``data`` into one self-contained HTML document.

    ``data`` maps dataset name → rows (a table) or a series bundle (a chart). Keeping it OUT of the
    spec is what makes the periodic regeneration free: the versioned record describes the shape,
    each run supplies the fresh numbers.
    """
    datasets = data if isinstance(data, dict) else {}
    warnings: list[str] = []

    blocks = spec["blocks"]
    if len(blocks) > MAX_BLOCKS:
        warnings.append(f"spec has {len(blocks)} blocks — rendered the first {MAX_BLOCKS}")
        blocks = blocks[:MAX_BLOCKS]

    # After sanitization, because a title made ENTIRELY of markup sanitizes to "" and an untitled
    # report reads as a rendering failure rather than as a blocked payload.
    title = _text(spec.get("title", "")).strip() or "Report"
    parts: list[str] = []
    for index, block in enumerate(blocks):
        kind = str(block["type"])
        try:
            if kind == "markdown":
                parts.append(_render_markdown(block, warnings))
            elif kind == "table":
                parts.append(_render_table(block, datasets, warnings, index))
            else:
                parts.append(_render_chart(block, datasets, warnings, index))
        except SpecError as exc:
            raise SpecError(f"block {index} ({kind}): {exc}") from exc

    document = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n<main>\n"
        f"<h1>{html.escape(title)}</h1>\n" + "\n".join(parts) + "\n</main>\n</body>\n</html>\n"
    )
    assert_self_contained(document)
    return RenderedReport(html=document, warnings=warnings, block_count=len(blocks))


def assert_self_contained(document: str) -> None:
    """Fail CLOSED if a finished document carries script, a handler, or a remote reference.

    An export that beacons or executes is worse than a missing export: it is a rendered page the
    user has been told is a report. So this raises rather than stripping — a strip would hide the
    renderer bug that produced it.
    """
    for pattern, label in _FORBIDDEN:
        match = pattern.search(document)
        if match:
            raise SpecError(
                f"refusing to export: the rendered document contains {label} "
                f"({match.group(0)[:60]!r}) — this is a renderer bug, not a spec error"
            )


# ── text admission ──


def _text(raw: Any, *, limit: int = MAX_TEXT_CHARS) -> str:
    """Reduce any spec value to inert plain text.

    Two passes on purpose. nh3 first (the repo's shared sanitizer) because it DELETES
    ``<script>``/``<style>`` bodies rather than exposing them; escaping alone would render
    ``alert(1)`` as visible prose in the middle of a report. Then tag-stripping and escaping, so
    what lands in the document is text and nothing else.
    """
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, (int, float)):
        return _number_text(raw)
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    if "<" in text or "&" in text:
        text = sanitize_html(text)
        # nh3 keeps its own allowed tags (``<p>``, ``<a>``…). They are markup we did not author,
        # so they go too: this renderer's contract is that caller text is never markup.
        text = re.sub(r"<[^>]*>", "", text)
        text = html.unescape(text)
    return text[:limit]


def _number_text(value: float) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.4g}"


def _safe_url(raw: Any) -> str:
    """A URL a link may point at, or "" when it must degrade to plain text."""
    url = _text(raw, limit=2000).strip()
    if not url:
        return ""
    lowered = url.lower()
    if lowered.startswith(ALLOWED_URL_SCHEMES):
        return url
    if url.startswith("/") and not url.startswith("//"):
        return url
    return ""


# ── markdown ──

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_FENCE = re.compile(r"^\s*```")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _render_markdown(block: dict[str, Any], warnings: list[str]) -> str:
    source = block.get("text", block.get("content", ""))
    text = _text(source)
    if not text.strip():
        warnings.append("a markdown block rendered empty")
        return '<p class="empty">(no content)</p>'

    out: list[str] = []
    bullets: list[str] = []
    numbers: list[str] = []
    paragraph: list[str] = []
    code: list[str] | None = None

    def flush() -> None:
        nonlocal bullets, numbers, paragraph
        if bullets:
            out.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets = []
        if numbers:
            out.append("<ol>" + "".join(f"<li>{n}</li>" for n in numbers) + "</ol>")
            numbers = []
        if paragraph:
            out.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph = []

    for line in text.splitlines():
        if _FENCE.match(line):
            if code is None:
                flush()
                code = []
            else:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = min(6, max(2, len(heading.group(1)) + 1))  # h1 is the report title
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue
        bullet = _BULLET.match(line)
        if bullet:
            if numbers or paragraph:
                flush()
            bullets.append(_inline(bullet.group(1)))
            continue
        ordered = _ORDERED.match(line)
        if ordered:
            if bullets or paragraph:
                flush()
            numbers.append(_inline(ordered.group(1)))
            continue
        if not line.strip():
            flush()
            continue
        if bullets or numbers:
            flush()
        paragraph.append(_inline(line.strip()))

    if code is not None:
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush()
    return "\n".join(out)


def _inline(text: str) -> str:
    """Inline spans over ALREADY-inert text: escape first, then add our own markup.

    The order is the control. Formatting first would let a ``*<script>*`` reach the escaper as
    markup this function had already decided to keep.
    """
    escaped = html.escape(text)

    def link(match: re.Match[str]) -> str:
        url = _safe_url(html.unescape(match.group(2)))
        label = match.group(1)
        if not url:
            # A dropped scheme leaves the label AND the rejected target visible: a report that
            # silently swallowed a link would hide the fact that something tried one.
            return f"{label} (link removed)"
        return f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{label}</a>'

    escaped = _LINK.sub(link, escaped)
    escaped = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    escaped = _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", escaped)
    return escaped


# ── tables ──


def _render_table(
    block: dict[str, Any], datasets: dict[str, Any], warnings: list[str], index: int
) -> str:
    rows = _rows_for(block, datasets, warnings, index)
    columns = _columns_for(block, rows)
    caption = _text(block.get("title", ""))
    title_html = f"<h2>{html.escape(caption)}</h2>\n" if caption else ""
    if not columns:
        if not rows:
            # An absent dataset is already a WARNING (a periodic fetch that found nothing must not
            # lose the report's other blocks). Raising here would turn that warning into a failed
            # node, which is the same loss by another route.
            return f'{title_html}<p class="empty">(no rows)</p>'
        raise SpecError("no columns — give 'columns' or rows with at least one key")

    rows = [_row_dict(r, columns) for r in rows]
    rows = _apply_filters(rows, block.get("filter"))
    rows = _apply_compute(rows, block.get("compute"), columns)
    rows = _apply_sort(rows, block.get("sort"))
    limit = _int(block.get("limit"), MAX_TABLE_ROWS)
    if len(rows) > min(limit, MAX_TABLE_ROWS):
        kept = min(limit, MAX_TABLE_ROWS)
        warnings.append(f"table block {index}: {len(rows)} rows — rendered the first {kept}")
        rows = rows[:kept]

    numeric = {c["key"] for c in columns if all(_num(r.get(c["key"])) is not None for r in rows)}
    head = "".join(
        f'<th{_num_class(c["key"], numeric)}>{html.escape(_text(c["label"]))}</th>' for c in columns
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td{_num_class(c["key"], numeric)}>'
            f"{html.escape(_text(row.get(c['key'], '')))}</td>"
            for c in columns
        )
        + "</tr>"
        for row in rows
    )
    if not rows:
        return f'{title_html}<p class="empty">(no rows)</p>'
    return f"{title_html}<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _num_class(key: str, numeric: set[str]) -> str:
    """The right-alignment class for a wholly-numeric column. A constant, never caller data."""
    return ' class="num"' if key in numeric else ""


def _rows_for(
    block: dict[str, Any], datasets: dict[str, Any], warnings: list[str], index: int
) -> list[Any]:
    name = str(block.get("dataset", "") or "")
    if name:
        if name not in datasets:
            # NOT an error: a periodic run whose fetch found nothing must still render the report,
            # with the gap visible, rather than failing the node and losing the other blocks.
            warnings.append(f"table block {index}: dataset {name!r} was not supplied")
            return []
        raw = datasets[name]
    else:
        raw = block.get("rows", [])
    if isinstance(raw, dict):
        raw = raw.get("rows", [])
    if not isinstance(raw, list):
        raise SpecError("rows must be a list of objects or a list of lists")
    return raw[: MAX_TABLE_ROWS * 2]


def _columns_for(block: dict[str, Any], rows: list[Any]) -> list[dict[str, Any]]:
    declared = block.get("columns")
    out: list[dict[str, Any]] = []
    if isinstance(declared, list) and declared:
        for entry in declared[:MAX_TABLE_COLUMNS]:
            if isinstance(entry, dict):
                key = str(entry.get("key", entry.get("label", "")) or "")
                label = str(entry.get("label", key) or key)
            else:
                key = label = str(entry)
            if key:
                out.append({"key": key, "label": label})
        return out
    seen: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            for key in row:
                if str(key) not in seen:
                    seen.append(str(key))
    return [{"key": k, "label": k} for k in seen[:MAX_TABLE_COLUMNS]]


def _row_dict(row: Any, columns: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(row, dict):
        return {str(k): v for k, v in row.items()}
    if isinstance(row, (list, tuple)):
        return {c["key"]: (row[i] if i < len(row) else "") for i, c in enumerate(columns)}
    return {columns[0]["key"]: row}


_FILTER_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and b is not None and a > b,
    "gte": lambda a, b: a is not None and b is not None and a >= b,
    "lt": lambda a, b: a is not None and b is not None and a < b,
    "lte": lambda a, b: a is not None and b is not None and a <= b,
}


def _apply_filters(rows: list[dict[str, Any]], raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return rows
    clauses = raw if isinstance(raw, list) else [raw]
    for clause in clauses:
        if not isinstance(clause, dict):
            raise SpecError("each filter clause must be an object")
        column = str(clause.get("column", "") or "")
        op = str(clause.get("op", "eq") or "eq")
        target = clause.get("value")
        if not column:
            raise SpecError("a filter clause needs 'column'")
        if op == "contains":
            needle = _text(target).lower()
            rows = [r for r in rows if needle in _text(r.get(column)).lower()]
            continue
        if op not in _FILTER_OPS:
            raise SpecError(f"filter op {op!r} must be one of: contains, {', '.join(_FILTER_OPS)}")
        test = _FILTER_OPS[op]
        kept: list[dict[str, Any]] = []
        for row in rows:
            left, right = _comparable(row.get(column), target)
            if test(left, right):
                kept.append(row)
        rows = kept
    return rows


def _comparable(left: Any, right: Any) -> tuple[Any, Any]:
    """Compare as numbers when BOTH sides are numeric, else as text.

    Mixed compares are the quiet failure here: ``"9" > "10"`` is true as text, so a spec that
    filtered `count > 10` would keep the 9-row and the author would read a wrong report as a right
    one.
    """
    ln, rn = _num(left), _num(right)
    if ln is not None and rn is not None:
        return ln, rn
    return _text(left), _text(right)


_COMPUTE_OPS = ("sum", "diff", "product", "ratio", "percent", "min", "max", "mean")


def _apply_compute(
    rows: list[dict[str, Any]], raw: Any, columns: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if raw is None:
        return rows
    specs = raw if isinstance(raw, list) else [raw]
    for entry in specs:
        if not isinstance(entry, dict):
            raise SpecError("each compute entry must be an object")
        target = str(entry.get("column", "") or "")
        op = str(entry.get("op", "") or "")
        of = entry.get("of")
        if not target:
            raise SpecError("a compute entry needs 'column' (the derived column's name)")
        if op not in _COMPUTE_OPS:
            raise SpecError(f"compute op {op!r} must be one of: {', '.join(_COMPUTE_OPS)}")
        sources = [str(s) for s in of] if isinstance(of, list) else ([str(of)] if of else [])
        if not sources:
            raise SpecError(f"compute {target!r} needs 'of' (the source columns)")
        for row in rows:
            row[target] = _compute(op, [_num(row.get(s)) for s in sources])
        if not any(c["key"] == target for c in columns):
            columns.append({"key": target, "label": entry.get("label", target)})
    return rows


def _compute(op: str, values: list[float | None]) -> Any:
    present = [v for v in values if v is not None]
    if not present or len(present) != len(values):
        # A derived cell over a missing input renders blank rather than 0: a zero would be read as
        # a measurement.
        return ""
    if op == "sum":
        return sum(present)
    if op == "diff":
        out = present[0]
        for v in present[1:]:
            out -= v
        return out
    if op == "product":
        out = present[0]
        for v in present[1:]:
            out *= v
        return out
    if op in ("ratio", "percent"):
        if len(present) < 2 or present[1] == 0:
            return ""
        value = present[0] / present[1]
        return round(value * 100, 2) if op == "percent" else round(value, 4)
    if op == "min":
        return min(present)
    if op == "max":
        return max(present)
    return round(sum(present) / len(present), 4)


def _apply_sort(rows: list[dict[str, Any]], raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return rows
    clause = raw if isinstance(raw, dict) else {"column": str(raw)}
    column = str(clause.get("column", "") or "")
    if not column:
        raise SpecError("a sort clause needs 'column'")
    descending = bool(clause.get("desc", clause.get("descending", False)))
    numeric = all(_num(r.get(column)) is not None for r in rows) and bool(rows)

    def key(row: dict[str, Any]) -> Any:
        if numeric:
            value = _num(row.get(column))
            return value if value is not None else 0.0
        return _text(row.get(column)).lower()

    return sorted(rows, key=key, reverse=descending)


# ── charts ──


def _render_chart(
    block: dict[str, Any], datasets: dict[str, Any], warnings: list[str], index: int
) -> str:
    labels, series = _chart_data(block, datasets, warnings, index)
    caption = _text(block.get("title", ""))
    title_html = f"<h2>{html.escape(caption)}</h2>\n" if caption else ""
    if not labels or not series:
        return f'{title_html}<p class="empty">(no chart data)</p>'
    style = str(block.get("style", "bar") or "bar")
    if style not in ("bar", "line"):
        raise SpecError(f"chart style {style!r} must be 'bar' or 'line'")
    svg = _svg(labels, series, style)
    legend = " · ".join(
        f'<span style="color:{_SERIES_COLORS[i % len(_SERIES_COLORS)]}">'
        f"{html.escape(s['name'])}</span>"
        for i, s in enumerate(series)
    )
    return f"{title_html}<figure>{svg}<figcaption>{legend}</figcaption></figure>"


def _chart_data(
    block: dict[str, Any], datasets: dict[str, Any], warnings: list[str], index: int
) -> tuple[list[str], list[dict[str, Any]]]:
    name = str(block.get("dataset", "") or "")
    source: Any = block
    if name:
        if name not in datasets:
            warnings.append(f"chart block {index}: dataset {name!r} was not supplied")
            return [], []
        source = datasets[name]
        if not isinstance(source, dict):
            # A dataset given as rows: read x/y out of the declared columns.
            return _chart_from_rows(block, source, warnings, index)

    raw_x = source.get("x", block.get("x"))
    if isinstance(raw_x, list):
        labels = [_text(x, limit=40) for x in raw_x[:MAX_POINTS]]
    else:
        return _chart_from_rows(block, source.get("rows", []), warnings, index)

    raw_series = source.get("series", block.get("series"))
    if not isinstance(raw_series, list) or not raw_series:
        raise SpecError("a chart needs 'series' (name + values) or a row dataset with 'y_columns'")
    series: list[dict[str, Any]] = []
    for entry in raw_series[:MAX_SERIES]:
        if not isinstance(entry, dict):
            raise SpecError("each series must be an object with 'name' and 'values'")
        values = entry.get("values")
        if not isinstance(values, list):
            raise SpecError(f"series {entry.get('name', '?')!r} needs a 'values' list")
        nums = [_num(v) for v in values[: len(labels)]]
        if all(v is None for v in nums):
            warnings.append(
                f"chart block {index}: series {entry.get('name', '?')!r} had no numbers"
            )
            continue
        series.append({"name": _text(entry.get("name", "series"), limit=40), "values": nums})
    return labels, series


def _chart_from_rows(
    block: dict[str, Any], rows: Any, warnings: list[str], index: int
) -> tuple[list[str], list[dict[str, Any]]]:
    if not isinstance(rows, list) or not rows:
        warnings.append(f"chart block {index}: no rows to plot")
        return [], []
    x_column = str(block.get("x_column", "") or "")
    y_columns = block.get("y_columns")
    if not x_column or not isinstance(y_columns, list) or not y_columns:
        raise SpecError("a row-backed chart needs 'x_column' and 'y_columns'")
    dicts = [r for r in rows[:MAX_POINTS] if isinstance(r, dict)]
    labels = [_text(r.get(x_column, ""), limit=40) for r in dicts]
    series = [
        {"name": _text(col, limit=40), "values": [_num(r.get(str(col))) for r in dicts]}
        for col in y_columns[:MAX_SERIES]
    ]
    return labels, [s for s in series if any(v is not None for v in s["values"])]


def _svg(labels: list[str], series: list[dict[str, Any]], style: str) -> str:
    """An inline SVG chart — the self-contained answer to Mermaid's xychart.

    Mermaid renders in the BROWSER from a script, which a self-contained export cannot carry. So
    the same declarative shape compiles to finished geometry here: no script, no fonts, no fetches,
    and identical bytes for identical data.
    """
    width, height = 720, 320
    pad_left, pad_right, pad_top, pad_bottom = 56, 16, 16, 48
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    values = [v for s in series for v in s["values"] if v is not None]
    low = min(0.0, min(values))
    high = max(values)
    if math.isclose(low, high):
        high = low + 1.0
    span = high - low

    def y_of(value: float) -> float:
        return pad_top + plot_h - ((value - low) / span) * plot_h

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="chart">'
    ]
    for step in range(5):
        value = low + span * step / 4
        y = round(y_of(value), 2)
        out.append(
            f'<line x1="{pad_left}" y1="{y}" x2="{width - pad_right}" y2="{y}" '
            f'stroke="#d1d5db" stroke-width="1"/>'
            f'<text x="{pad_left - 8}" y="{y + 4}" font-size="11" fill="#6b7280" '
            f'text-anchor="end">{html.escape(_number_text(value))}</text>'
        )

    slot = plot_w / max(1, len(labels))
    if style == "bar":
        bar_w = max(1.0, (slot * 0.7) / max(1, len(series)))
        for s_index, s in enumerate(series):
            color = _SERIES_COLORS[s_index % len(_SERIES_COLORS)]
            for point, value in enumerate(s["values"]):
                if value is None:
                    continue
                x = pad_left + slot * point + slot * 0.15 + bar_w * s_index
                top = y_of(value)
                out.append(
                    f'<rect x="{round(x, 2)}" y="{round(min(top, y_of(low)), 2)}" '
                    f'width="{round(bar_w, 2)}" '
                    f'height="{round(abs(y_of(low) - top), 2)}" fill="{color}"/>'
                )
    else:
        for s_index, s in enumerate(series):
            color = _SERIES_COLORS[s_index % len(_SERIES_COLORS)]
            points = [
                f"{round(pad_left + slot * i + slot / 2, 2)},{round(y_of(v), 2)}"
                for i, v in enumerate(s["values"])
                if v is not None
            ]
            if points:
                out.append(
                    f'<polyline fill="none" stroke="{color}" stroke-width="2" '
                    f'points="{" ".join(points)}"/>'
                )

    for point, label in enumerate(labels):
        x = round(pad_left + slot * point + slot / 2, 2)
        out.append(
            f'<text x="{x}" y="{height - pad_bottom + 18}" font-size="11" fill="#6b7280" '
            f'text-anchor="middle">{html.escape(label[:14])}</text>'
        )
    out.append(
        f'<line x1="{pad_left}" y1="{round(y_of(low), 2)}" x2="{width - pad_right}" '
        f'y2="{round(y_of(low), 2)}" stroke="#9ca3af" stroke-width="1"/>'
    )
    out.append("</svg>")
    return "".join(out)


def _num(raw: Any) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _int(raw: Any, fallback: int) -> int:
    value = _num(raw)
    return int(value) if value is not None else fallback
