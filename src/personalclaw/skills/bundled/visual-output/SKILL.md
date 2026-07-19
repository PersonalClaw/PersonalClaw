---
name: visual-output
description: Emit rich visual output in chat via <widget> tags — styled HTML cards, charts, tables, interactive tools, and drawn SVG/canvas diagrams, illustrations, and animations — theme-aware, centered, and responsive instead of clashing or cramped.
triggers: widget, widget iframe, chart, table, visual, dashboard, render widget, illustration, diagram, draw, svg, animation, animate, graph, schematic, visualize, visual explanation, figure
---

# Visual Output

PersonalClaw renders rich HTML inline in chat via `<widget>` tags. The HTML runs
in a sandboxed iframe that inherits the dashboard's active theme through CSS
variables. Get the styling contract right and a widget looks native on every
theme — light, dark, or custom. Get it wrong (hardcoded colors, fixed pixel
sizes) and it clashes or renders tiny in a corner.

This skill covers both halves of visual output:
- **The widget container** — format, theme variables, CSP, interactivity, and the
  card design system (layout, spacing, type, surface, motion).
- **Drawn visuals** — SVG/canvas diagrams, schematics, charts, and animations,
  and the craft of making them centered, proportioned, labelled, and legible.

## Format

```html
<widget title="Sales by Region">
  <div class="p-4 rounded-lg" style="background: var(--card); color: var(--card-fg);">
    …content…
  </div>
</widget>
```

- One widget per payload, self-contained.
- Tailwind CSS is available inside the iframe.
- For large HTML (long dashboards, big tables), save it to a file or an
  Artifact (see the `artifacts` skill) and reference it — don't inline hundreds
  of lines into the chat.

## Design system — make it look intentional

The difference between a widget that looks native and one that looks slapped
together is **consistent layout, spacing, type, and alignment**. Follow these.

### Layout & alignment

- **Cap the width and center it.** Wrap content in `max-w-md`/`max-w-xl`/`max-w-2xl`
  (pick by density) with `mx-auto`. A widget that sprawls full-width in a wide
  chat reads as broken. Dashboards: `max-w-3xl`; a single stat/card: `max-w-sm`.
- **One alignment spine.** Left-align text content; center only standalone figures
  (charts, single big numbers, illustrations). Don't mix center + left in one card.
- **Use a grid for multi-item layouts** — `grid grid-cols-2 gap-3` (or `gap-4`),
  not floats or ad-hoc margins. Equal gaps everywhere; let the grid do alignment.
- **Group related, separate unrelated** with whitespace + hairline dividers
  (`border-t border-[var(--border)]`), not boxes-within-boxes.

### Spacing rhythm (4px scale)

Stick to a consistent scale so spacing feels deliberate: `gap-2`(8) `gap-3`(12)
`gap-4`(16); padding `p-4`/`p-5` for cards, `p-3` for compact rows. Pick ONE card
padding and one gap size per widget and reuse them. Avoid arbitrary `mt-[7px]`.

### Typography hierarchy

- **Title** of the widget content: `text-base font-semibold` (or `text-lg` for a
  hero), `var(--text-strong)`.
- **Body**: `text-sm`, `var(--text)`. **Captions/labels**: `text-xs`,
  `var(--muted)`. **Big stat numbers**: `text-2xl`/`text-3xl font-semibold tabular-nums`.
- Max ~2 type sizes per card beyond the title. Use weight + color for hierarchy,
  not many sizes. `tabular-nums` for any aligned numbers/tables.

### Surface & depth

- Cards: `rounded-lg` (or `rounded-xl` for hero), `bg-[var(--card)]`,
  `border border-[var(--border)]`. One elevation level — don't nest cards 3 deep.
- Subtle, not heavy: prefer a hairline border + tint over big shadows.

### Motion

- Use CSS `transition`/`@keyframes` (no JS needed for most). Easing
  `cubic-bezier(0.2,0,0,1)` (or `ease-in-out`); **150–300ms** for UI transitions,
  2–6s for ambient loops. Animate `opacity`/`transform` only (cheap, smooth).
- Reveal-on-load is fine once; avoid perpetual motion unless it's the point
  (a live pulse/orbit). Always honor reduced-motion:
  ```css
  @media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
  ```

## Theme variables — use these, never hardcoded colors

The iframe injects the active theme's palette as CSS custom properties. Style
**everything** with them (directly via `style="…: var(--x)"` or Tailwind
arbitrary values like `bg-[var(--card)]`) so the widget tracks the theme:

| Variable | Use for |
|---|---|
| `var(--bg)` | page / outermost background |
| `var(--text)` | default body text |
| `var(--card)` / `var(--card-fg)` | panel surface / text on it |
| `var(--border)` | hairlines, dividers, input borders |
| `var(--accent)` / `var(--accent-hover)` | primary actions, links, highlights |
| `var(--muted)` / `var(--muted-strong)` | secondary / tertiary text |
| `var(--text-strong)` | emphasized headings |
| `var(--bg-elevated)` / `var(--bg-hover)` | raised surfaces / hover states |
| `var(--border-strong)` | stronger separators |
| `var(--ok)` / `var(--warn)` / `var(--danger)` / `var(--info)` | status colors |
| `var(--accent-subtle)` / `var(--ok-subtle)` / `var(--warn-subtle)` / `var(--danger-subtle)` | tinted fills/badges |

**Never** use `bg-gray-900`, `text-white`, `#fff`, or any hardcoded hex — they
break on the opposite theme. Zero hardcoded colors.

## Allowed scripts (CSP)

The iframe's Content-Security-Policy restricts `script-src` to inline scripts
plus these CDNs only:

- **Tailwind** — `https://cdn.tailwindcss.com` (preloaded; classes work out of the box)
- `https://cdn.jsdelivr.net`
- `https://cdnjs.cloudflare.com`

Load charting/visualization libs (e.g. Chart.js) from jsDelivr or cdnjs. Scripts
from any other origin are blocked. `connect-src` is `'none'` — widgets can't make
network calls; render data you already have.

## Interactive widgets

Widgets send events back to the agent. Add `data-action` (and optional
`data-payload`, a JSON string) to any clickable element:

```html
<button data-action="approve" data-payload='{"id":"123"}'>Approve</button>
```

On click, the dashboard auto-submits a user message: `[UI] approve: {"id":"123"}`.
You receive it as the next turn and can respond with text, a new widget, or both.

**Forms:** inputs with a `name` attribute are auto-collected on click and merged
into the payload as `formData`. Use this for creation forms — render pre-filled
inputs, the user adjusts values, clicks submit, and you receive every field:

```html
<input name="title" value="Untitled" class="px-2.5 py-2 rounded-md"
       style="background: var(--bg-elevated); color: var(--text); border: 1px solid var(--border);" />
<button data-action="create_task">Create</button>
```

**Living views (auto-refresh).** A widget can be a *living dashboard* that pulls fresh
data on demand instead of freezing a snapshot at creation. Give it a refresh button:

```html
<button data-action="refresh">Refresh</button>
```

When the widget has been **saved as an artifact** (it has a stable slug), the dashboard
appends `(refresh artifact "<slug>" in place)` to the `[UI] refresh` message it sends you.
On that turn: re-fetch the current data (call the tools/searches that produced it), then
`artifact_update("<slug>", ...)` with the freshly-rendered HTML — the SAME artifact
updates in place (versioned), so the open view re-renders with live data rather than
spawning a new artifact. This is how you build a "my open items" / "current status"
dashboard that stays current. (If the widget isn't a saved artifact yet, save it first so
it has a slug to refresh.)

## Sizing conventions

- Buttons: `text-xs py-1.5 px-3.5 rounded-md`
- Labels: `text-[11px]`
- Inputs: `text-sm px-2.5 py-2 rounded-md`

## Links

Render bare URLs as anchors. Always open external links safely and style them
with the accent color:

```html
<a href="https://example.com" target="_blank" rel="noopener noreferrer"
   style="color: var(--accent);">example.com</a>
```

## Drawn visuals — SVG, diagrams, illustrations, animations

Drawn visuals are emitted **inside a `<widget>`** (same container, theme vars, and
CSP as above). The container is easy; the craft is making the drawing itself
**centered, correctly proportioned, theme-aware, labelled, and legible** rather
than cramped, clipped, or off-canvas.

> The #1 failure is a fixed-size `<svg width="350" height="200">` that renders
> tiny in a corner. Always make the SVG responsive and centered (below).

### The responsive-SVG contract (use every time)

```html
<widget title="…">
  <div style="max-width: 640px; margin: 0 auto;">
    <svg viewBox="0 0 640 360" width="100%" height="auto"
         preserveAspectRatio="xMidYMid meet" role="img" aria-label="…describe it…"
         style="display:block">
      …
    </svg>
  </div>
</widget>
```

- **`viewBox` defines the coordinate space; `width="100%"` makes it scale.** Never
  set a fixed pixel `width`/`height` on the `<svg>` — it won't be responsive.
- **`preserveAspectRatio="xMidYMid meet"`** centers + fits without distortion.
- **`display:block` + `margin:0 auto`** on a `max-width` wrapper centers it and
  caps it so it doesn't sprawl edge-to-edge in a wide chat.
- Pick a viewBox aspect that fits the content: 16:9 (`640 360`) for scenes, 1:1
  (`400 400`) for cycles/radial, 3:1 (`720 240`) for timelines/flows.
- Leave **internal padding** — keep real content within ~6–8% of each edge so
  strokes and labels never touch the frame.

### Theme-aware drawing — no hardcoded colors

Style strokes/fills with the theme vars (injected into the iframe, same as above):

- structural lines / axes / grids → `var(--border)` (thin, 0.5–1px)
- primary subject / data → `var(--accent)`
- secondary / supporting → `var(--muted)`
- emphasis / alert → `var(--danger)`; positive → `var(--ok)`
- text labels → `var(--text)`, secondary labels → `var(--muted)`
- filled regions → `var(--accent-subtle)` / tinted fills, never opaque black/white

Put shared style in one `<style>` block with classes (`.axis{stroke:var(--border)}`)
rather than repeating `stroke=` on every element — easier to keep consistent.

### Alignment & composition

- **Establish a grid mentally**: align elements to consistent x/y coordinates;
  don't eyeball. Equal gaps read as intentional; ragged gaps read as broken.
- **Center the focal point** in the viewBox; balance mass around it.
- **Label directly** where possible (label next to the thing) over distant
  legends; when a legend is needed, align it to a consistent edge with even rows.
- **Text**: `font: 14px/1.4 -apple-system, sans-serif;` for body labels, ~12px for
  captions; use `text-anchor="middle"` for centered labels under a node and
  `dominant-baseline="middle"` for vertical centering. Never rely on the default
  baseline for centered text.
- Round coordinates to whole/half pixels for crisp strokes.

### Animating a drawing

- Prefer **CSS animations / transitions** (in the `<style>` block) or **SMIL**
  (`<animate>`, `<animateTransform>`) — both run in the sandbox without extra libs.
- **Easing + duration**: ease-in-out, 200–600ms for transitions, 2–6s loops for
  ambient motion. Avoid linear unless it's a steady cycle (rotation, orbit).
- **Loop with intent**: `animation-iteration-count: infinite` only for genuinely
  ambient visuals (waves, pulses, orbits). One-shot reveals should play once.
- **Layer rhythms**: when animating multiple elements, offset their delays/durations
  slightly so motion feels organic, not mechanically synchronized.
- **Respect reduced-motion** (same media query as the card motion section above).
- Keep it subtle — motion should clarify (show flow, change, cause→effect), not
  distract. If the still image already communicates, don't animate.

### Charts

- For data charts prefer **Chart.js** (load from jsDelivr — see CSP above) over
  hand-drawn SVG bars; pass theme-var colors into the dataset config so it tracks
  the theme. Give the canvas a fixed-aspect wrapper (`position:relative; height:0;
  padding-bottom:56.25%`) so it's responsive.
- Always title the axes + the chart; never render a chart with unlabelled axes.

## Cost discipline

Prefer plain markdown by default. Promote to a widget only when styled HTML
communicates clearly better — charts, color-coded tables, styled cards, visual
summaries, diagrams, or simple interactive tools. Don't wrap a paragraph in a widget.

The dashboard's `widget_density` setting (`more` / `less`, default `more`)
signals how freely to reach for widgets; on `less`, use them only when markdown
is plainly insufficient.

## Quality checklist (before emitting)

1. **Width capped + centered** (`max-w-* mx-auto`, or `max-width` wrapper for SVG)
   — not sprawling full-width?
2. **Consistent spacing** — one card padding + one gap size, on the 4px scale?
3. **Type hierarchy** — clear title vs body vs caption via size/weight/color, ≤2
   extra sizes? Numbers `tabular-nums`?
4. **Aligned** — multi-item layouts on a grid with equal gaps, one alignment spine?
5. **Zero hardcoded colors** — every color a theme var, looks right light + dark?
6. **One elevation** — no cards nested 3 deep; hairline borders over heavy shadows?
7. **Motion** (if any) — eased, 150–300ms (or 2–6s ambient), purposeful,
   reduced-motion-safe?
8. **Drawing a figure?** Responsive `viewBox` + `width="100%"` (no fixed px),
   centered, content inside the frame with breathing room, labels legible and
   correctly centered? Reads clearly at half size on both themes?
