"""Self-contained HTML export of the memory entity graph (MEMORY-GRAPH-AND-VAULT §7.2).

One file you can mail, archive, or open in five years: the graph drawn as inline SVG, the
legend, and the underlying JSON embedded verbatim so the picture and its data never separate.

**No script, by design — and that IS a deviation from the plan's sketch.** §7.2 describes an
interactive `graph.html` with the JSON embedded for a client-side renderer. This repo already
answered that question for exports in ``knowledge/reports.py``: an exported document that can
execute "is worse than a missing export — it is a rendered page the user has been told is a
report". So the layout is computed HERE and the export ships as static SVG, and the same
:func:`assert_self_contained` invariant that guards knowledge reports guards this one. Reusing
that guard rather than writing a second forbidden-pattern list is deliberate: two lists drift,
and the one that drifts is the one nobody is reading when it matters.

The JSON travels inside an HTML comment rather than a ``<script type="application/json">``
block for exactly that reason — the guard refuses any ``<script`` at all, and a data island
that can only exist by weakening the guard is not worth the convenience.
"""

from __future__ import annotations

import html
import json
import math
from typing import Any

#: Layout box. Matches `MemoryGraph.tsx`'s 1000x1000 viewBox so the export reads as the same
#: picture as the on-screen canvas rather than as a second, differently-shaped graph.
_W = 1000
_H = 1000

#: Ring radii + capacities, mirroring the radial layout in `MemoryGraph.tsx`. Kept as data so
#: the two implementations can be compared line-for-line instead of by reading trigonometry.
_RINGS: tuple[tuple[int, int], ...] = ((1, 0), (8, 130), (16, 250), (35, 370), (10_000, 470))

#: Neutral fill for an entity Louvain has not placed in a community.
_UNCLUSTERED = "hsl(0 0% 62%)"

_MAX_NODES = 600
_MAX_EDGES = 3000


def _positions(count: int) -> list[tuple[float, float]]:
    """Radial ring positions for *count* nodes, centre-first."""
    cx, cy = _W / 2, _H / 2
    out: list[tuple[float, float]] = []
    start = 0
    for capacity, radius in _RINGS:
        if start >= count:
            break
        here = min(capacity, count - start)
        for i in range(here):
            angle = (i / max(1, here)) * math.tau - math.pi / 2
            out.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
        start += here
    return out


def _fill(community: Any) -> str:
    """The node colour for a community id — the SAME hue the on-screen canvas uses.

    `MemoryGraph.tsx` derives its hue from the node's group STRING with
    ``h = (h * 31 + charCode) % 360``, and the Studio's group string for an entity is
    ``neighbourhood <n>``. Re-deriving that here rather than picking a private palette is the
    whole point: the export is presented as the picture the user just looked at, so an export
    that recoloured every neighbourhood would quietly be a different diagram of the same data.
    """
    if community is None:
        return _UNCLUSTERED
    try:
        group = f"neighbourhood {int(community)}"
    except (TypeError, ValueError):
        return _UNCLUSTERED
    hue = 0
    for char in group:
        hue = (hue * 31 + ord(char)) % 360
    return f"hsl({hue} 55% 60%)"


_STYLE = (
    "body{margin:0;background:#101418;color:#e3e6ea;"
    "font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}"
    "main{max-width:1100px;margin:0 auto;padding:24px}"
    "h1{font-size:1.25rem;margin:0 0 4px}"
    "p.meta{color:#9aa4ae;margin:0 0 16px;font-size:.8125rem}"
    ".legend{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 0;font-size:.8125rem}"
    ".legend span{display:inline-flex;align-items:center;gap:6px;color:#9aa4ae}"
    ".swatch{width:10px;height:10px;border-radius:999px;display:inline-block}"
    "figure{margin:0;border:1px solid #2a3138;border-radius:12px;background:#161b20}"
    "svg{display:block;width:100%;height:auto}"
    "details{margin-top:16px;border:1px solid #2a3138;border-radius:12px;padding:10px 12px}"
    "summary{cursor:pointer;color:#9aa4ae;font-size:.8125rem}"
    "pre{overflow-x:auto;font-size:.75rem;color:#c8cfd6;white-space:pre-wrap}"
    "table{border-collapse:collapse;font-size:.8125rem;margin-top:8px}"
    "td,th{text-align:left;padding:2px 12px 2px 0;color:#c8cfd6}"
)


def render_graph_html(graph: dict, *, generated_at: str = "") -> str:
    """One self-contained HTML document for *graph* (the `entity_graph` shape).

    Truncates rather than refusing on an enormous graph: an export that silently omits the
    tail would misrepresent the store, so the omission is stated in the document itself.
    """
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    notes: list[str] = []
    if len(nodes) > _MAX_NODES:
        notes.append(f"showing the {_MAX_NODES} most-linked of {len(nodes)} entities")
        nodes = sorted(nodes, key=lambda n: -int(n.get("inbound_count") or 0))[:_MAX_NODES]
    shown_ids = {str(n.get("id")) for n in nodes}
    edges = [e for e in edges if str(e.get("from")) in shown_ids and str(e.get("to")) in shown_ids]
    if len(edges) > _MAX_EDGES:
        notes.append(f"showing the {_MAX_EDGES} strongest of {len(edges)} links")
        edges = sorted(edges, key=lambda e: -float(e.get("records") or 0))[:_MAX_EDGES]

    coords = dict(zip((str(n.get("id")) for n in nodes), _positions(len(nodes))))
    degree: dict[str, int] = {}
    for edge in edges:
        for end in ("from", "to"):
            key = str(edge.get(end))
            degree[key] = degree.get(key, 0) + 1

    lines: list[str] = []
    for edge in edges:
        a = coords.get(str(edge.get("from")))
        b = coords.get(str(edge.get("to")))
        if not a or not b:
            continue
        width = min(3.0, 0.6 + float(edge.get("records") or 1) * 0.35)
        lines.append(
            f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" '
            f'stroke="#3a444e" stroke-width="{width:.2f}" stroke-opacity="0.6" />'
        )
    marks: list[str] = []
    for node in nodes:
        nid = str(node.get("id"))
        point = coords.get(nid)
        if not point:
            continue
        radius = min(15.0, 5.0 + degree.get(nid, 0) * 1.5)
        label = html.escape(str(node.get("name") or nid))
        kind = html.escape(str(node.get("entity_type") or ""))
        marks.append(
            f'<g transform="translate({point[0]:.1f},{point[1]:.1f})">'
            f'<circle r="{radius:.1f}" fill="{_fill(node.get("community"))}" '
            f'fill-opacity="0.9" stroke="#0d1114" stroke-width="1">'
            f"<title>{label} ({kind})</title></circle>"
            f'<text y="{-radius - 4:.1f}" text-anchor="middle" font-size="10" fill="#e3e6ea">'
            f"{label}</text></g>"
        )

    communities = sorted({n.get("community") for n in nodes if n.get("community") is not None})
    legend = "".join(
        f'<span><i class="swatch" style="background:{_fill(c)}"></i>'
        f"neighbourhood {html.escape(str(c))}</span>"
        for c in communities
    )
    if any(n.get("community") is None for n in nodes):
        legend += (
            f'<span><i class="swatch" style="background:{_UNCLUSTERED}"></i>unclustered</span>'
        )

    meta_bits = [f"{len(nodes)} entities", f"{len(edges)} links"]
    if generated_at:
        meta_bits.append(f"exported {html.escape(generated_at)}")
    meta_bits.extend(html.escape(n) for n in notes)

    # Crop the viewBox to what was actually drawn. The ring layout spreads across the full
    # 1000x1000 box only once the outer rings fill, so a small graph placed near the centre
    # would otherwise export as four dots in a mostly-empty square — the on-screen canvas gets
    # away with that because it has zoom, and this file has none. Padding leaves room for the
    # labels, which are drawn ABOVE each node and would clip against a tight box.
    view = f"0 0 {_W} {_H}"
    if coords:
        pad = 90
        xs = [p[0] for p in coords.values()]
        ys = [p[1] for p in coords.values()]
        span = max(max(xs) - min(xs), max(ys) - min(ys)) + pad * 2
        span = max(span, 240)  # a single node must not zoom to absurdity
        # Square, and centred on the drawing: an aspect-distorted box would render circles
        # as ellipses, which reads as a rendering bug rather than as a tight crop.
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        view = f"{cx - span / 2:.1f} {cy - span / 2:.1f} {span:.1f} {span:.1f}"

    # The data island. Inside a comment, with `--` neutralized so an entity name containing it
    # cannot close the comment early and spill the rest of the JSON into the document body. The
    # replacement is a JSON escape, not a lookalike character: outside a string JSON never has
    # two adjacent hyphens, so every `--` is inside one, and `--` parses back to `--`. The
    # island therefore stays machine-readable, which is the only reason to ship it at all.
    payload = json.dumps({"nodes": nodes, "edges": edges}, indent=1, sort_keys=True)
    island = payload.replace("--", "-\\u002d")

    document = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<title>PersonalClaw memory graph</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n<main>\n"
        "<h1>Memory entity graph</h1>\n"
        f'<p class="meta">{" · ".join(meta_bits)}</p>\n'
        "<figure>\n"
        f'<svg viewBox="{view}" xmlns="http://www.w3.org/2000/svg" role="img" '
        'aria-label="Memory entity graph: entities as circles coloured by neighbourhood, '
        'links as lines">\n'
        f'<g>{"".join(lines)}</g>\n<g>{"".join(marks)}</g>\n</svg>\n</figure>\n'
        f'<div class="legend">{legend}</div>\n'
        "<details><summary>Underlying data (JSON)</summary>\n"
        f"<pre>{html.escape(payload)}</pre>\n</details>\n"
        f"<!-- personalclaw-memory-graph\n{island}\n-->\n"
        "</main>\n</body>\n</html>\n"
    )
    # The invariant, borrowed rather than re-derived — see the module docstring.
    from personalclaw.knowledge.reports import assert_self_contained

    assert_self_contained(document)
    return document
