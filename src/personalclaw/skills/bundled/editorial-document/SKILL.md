---
name: editorial-document
description: Write long-form editorial documents as clean, semantic HTML saved as an artifact (kind=document) — reports, briefs, explainers, proposals, write-ups. Covers the house style (structure, voice, typography) AND the security contract (the HTML is sanitized fail-closed, so author within the allowlist). Load when the user wants a polished written document, report, brief, or explainer that should read like a publication, not a chat message.
triggers: document, write a report, write up, brief, explainer, proposal, white paper, editorial, long-form, publish a doc, formatted document, article, memo, one-pager
---

# Editorial Documents

PersonalClaw renders long-form written documents from semantic HTML, saved as an
artifact with `kind=document`. Unlike a `widget` (interactive HTML in a sandboxed
iframe) or `markdown`, a document is **prose-first editorial HTML** rendered in the
page with a tuned reading type scale — it should read like a published piece.

Save with `artifact_save(kind="document", name="…", content=<HTML>)`. The body is
HTML (semantic tags, no `<html>`/`<body>` wrapper needed — just the content). The
viewer renders it with the editorial stylesheet, lets the user edit the HTML with a
live split preview, comment on any passage, and export it (standalone HTML / copy).

## When to use this vs other types

- **Document** — a written piece meant to be *read*: reports, briefs, explainers,
  proposals, post-mortems, design write-ups. Prose is the substance.
- **Markdown** — quick notes / READMEs / chat-adjacent text where Markdown's
  shorthand is enough and you don't need editorial typography.
- **Widget** (`visual-output` skill) — interactive HTML, dashboards, custom charts.
- **Infographic** (`infographic-syntax` skill) — structured visual info-design.

## The security contract (author within the allowlist)

The document body is **sanitized fail-closed** before rendering: anything not on
the allowlist is dropped. This is a security control because the content may echo
untrusted material (e.g. crawled pages). Author accordingly:

- **Allowed:** semantic prose + structure — headings (`h1`–`h6`), `p`, `ul`/`ol`/
  `li`, `blockquote`, `pre`/`code`, `table`/`thead`/`tbody`/`tr`/`th`/`td`,
  `figure`/`figcaption`, `img`, `a`, `strong`/`em`, `hr`, `section`/`article`,
  inline `svg` for simple diagrams.
- **Dropped (don't bother emitting):** `<script>`, `<style>`, `<iframe>`, `<object>`,
  `<form>`, inline event handlers (`onclick=…`), inline `style=` attributes, and
  `javascript:`/`data:text/html` URLs. Links must be `http(s)`, `mailto:`, `tel:`,
  relative, or `#anchors`; `data:image/*` is allowed for inline images.
- Don't fight the sanitizer with inline styles — the editorial stylesheet handles
  typography. Write clean structure and let it style.

## House style

**Structure.** Open with an `<h1>` title and a one-paragraph lede that states the
point. Use `<h2>` for major sections, `<h3>` for sub-sections. Prefer short
sections with descriptive headings over one long wall. Close with a clear takeaway
or next-steps section when the piece calls for it.

**Voice.** Direct and concrete. Lead with the conclusion, then support it (BLUF).
Active voice. Cut filler ("it should be noted that", "in order to"). One idea per
paragraph; 2–4 sentences each.

**Evidence.** Use `<table>` for comparisons/data, `<ul>` for parallel points, a
`<blockquote>` for a pulled quote or key caveat. Link sources with real `<a href>`.
Use `<figure>` + `<figcaption>` for images/diagrams.

**Typography.** Don't hand-set fonts/sizes/colors — the reader stylesheet owns the
type scale (heading hierarchy, line length ~72ch, readable line-height) and is
theme-aware. Your job is correct *semantics*; the styling follows.

## Example

```html
<h1>Q3 Reliability Review</h1>
<p>Availability held at 99.95% despite a 40% traffic increase — but two incidents
share a root cause we haven't fixed. This brief lays out what happened and the one
change that closes both.</p>

<h2>What happened</h2>
<p>…</p>
<table>
  <thead><tr><th>Incident</th><th>Duration</th><th>Root cause</th></tr></thead>
  <tbody>
    <tr><td>Aug 12 latency</td><td>34 min</td><td>Connection-pool exhaustion</td></tr>
    <tr><td>Sep 3 errors</td><td>18 min</td><td>Connection-pool exhaustion</td></tr>
  </tbody>
</table>

<h2>The fix</h2>
<blockquote>One change — bounded pools with backpressure — removes the shared cause.</blockquote>
<p>…</p>
```

## Workflow

1. Confirm a *document* is right (a written piece to be read, not interactive).
2. Draft semantic HTML within the allowlist; structure with headings + the lede.
3. `artifact_save(kind="document", name="…", content=<HTML>)` (or `artifact_update`
   a prior one — `artifact_list` first, per the `artifacts` skill).
4. The user reads it, edits the HTML with live preview, comments on passages (those
   route back to you), and can export a standalone HTML file.

## Pitfalls

- Don't emit `<script>`/`<style>`/inline `style=`/`onclick=` — they're stripped.
- Don't wrap the body in `<widget>` — `kind=document` is its own type.
- Don't hand-style — semantics + the reader stylesheet, not inline CSS.
- Keep links to safe schemes (http(s)/mailto/tel/relative/#).
