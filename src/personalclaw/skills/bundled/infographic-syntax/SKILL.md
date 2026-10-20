---
name: infographic-syntax
description: Author infographics with the AntV declarative DSL — pick a template, fill a small indented data tree, and save as an artifact (kind=infographic) that renders to crisp SVG and streams as you write it. Load when the user wants an infographic, a visual summary, a comparison/steps/hierarchy/stats graphic, or "make this look like a poster/diagram".
triggers: infographic, visual summary, poster, data storytelling, steps graphic, comparison graphic, swot, mind map, hierarchy diagram, stat cards, process diagram, timeline graphic, make it visual, turn this into a graphic
---

# Infographic Syntax

PersonalClaw renders infographics from a compact, declarative DSL (powered by the
AntV Infographic engine). You write a few indented lines — a **template name**
plus a small **data tree** — and the system renders professional SVG. The syntax
is highly fault-tolerant, so it renders *as you stream it*.

Save an infographic as an artifact with `kind=infographic`; the body is the DSL
source (NOT HTML, NOT a widget). The Artifacts/Files viewer renders it live and
lets the user edit the DSL with a side-by-side preview.

## When to use this vs a widget

- **Infographic** — structured information design: steps, comparisons, hierarchies,
  SWOT, stat cards, mind maps, process flows. Pick this when the *shape of the
  information* is the point and you want a polished, print-quality result fast.
- **Widget** (see the `visual-output` skill) — bespoke interactive HTML, custom
  charts, dashboards, tables, or anything needing live behavior/JS.
- **Mermaid** (a ```mermaid fenced block) — flowcharts/sequence/ER graphs.

## Format

The body is plain text: the first line is the template, then a `data` block with
an indented tree. Indentation (2 spaces) defines nesting; `- ` starts a list item.

```
infographic list-row-simple-horizontal-arrow
data
  title Onboarding in three steps
  lists
    - label Sign up
      desc Create your account
    - label Connect
      desc Link your data sources
    - label Ship
      desc Launch your first workflow
```

- **First token** after `infographic` is the template id (below). Omit it (or use
  `default`) and the engine picks a sensible layout for the data shape.
- `data` holds the content. Common keys: `title`, `desc`, `lists` (the items),
  `label`/`desc` per item, and template-specific keys (e.g. comparison sides).
- Keep labels short — infographics are glanceable, not paragraphs.

## Picking a template

Choose by the *relationship* you're showing (≈200 templates ship; these are the
workhorses):

| Intent | Template family |
|---|---|
| Ordered steps / process | `list-row-simple-horizontal-arrow`, `list-column-simple-vertical-arrow`, `horizontal-icon-arrow` |
| Stat / KPI cards | `list-grid-badge-card`, `list-grid-compact-card`, `indexed-card`, `circular-progress` |
| Two-way comparison | `compare-binary-horizontal-*-vs`, `compare-swot` |
| Hierarchy / mind map | `hierarchy-mindmap`, `hierarchy-structure`, `compare-hierarchy-left-right` |
| Simple charts | `chart-bar`, `chart-column`, `chart-line`, `chart-pie` |
| Generic / let it choose | `default` |

If unsure, start with `default` or a `list-*` template and iterate — the user can
nudge the template in the editor and re-render instantly.

## Streaming

Because the syntax tolerates partial input, emit the artifact body top-to-bottom
and it paints progressively. Lead with the `infographic <template>` line and the
`title`, then stream the list items — the user sees the structure form in real time.

## Workflow

1. Decide infographic is the right format (structured info-design, not interactive).
2. Pick a template by relationship (table above).
3. `artifact_save(kind="infographic", name="…", content=<DSL>)` — the body is the
   DSL source. (Check `artifact_list` first if iterating on an existing one, per
   the `artifacts` skill — then `artifact_update`.)
4. The viewer renders it; the user can edit the DSL with live preview, or comment.

## Pitfalls

- Don't wrap the DSL in a `<widget>` tag or HTML — `kind=infographic` is its own
  type. Wrapping it makes it render as raw text.
- Don't write prose paragraphs as labels — keep each item to a few words.
- Indentation is significant (2 spaces per level). Mixed tabs/spaces break nesting.
- One infographic per artifact.
