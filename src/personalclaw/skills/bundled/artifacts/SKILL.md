---
name: artifacts
description: Persist, version, and iterate on LLM-generated UI (widgets, HTML, markdown). Load when the user wants to save, find, update, or iterate on a previously-rendered widget — anything that should outlive the chat scrollback.
triggers: artifact, save widget, save this, iterate, iterate on, update the widget, change the widget, version, library, find widget, what have we built, iterate again, redo the
---

# Artifacts (`@personalclaw-core/artifact_*`)

An **Artifact** is a named, versioned piece of generated content — a widget,
HTML tool, dashboard, markdown doc, SVG, or JSON — that persists beyond the chat
scrollback and can be reopened and iterated on by name in a later session. The
Artifacts library lists them; each lives at `/artifacts/<slug>` in the dashboard.

Without artifacts, a widget you render scrolls away and is gone. With them, the
user can say "iterate on that dashboard" three days later and you can fetch the
current version, change it, and save a new checkpoint.

## Mental model

- **slug** — the stable handle (e.g. `sales-dashboard`). Derived from the name,
  or you pass an explicit one. Everything references an artifact by slug.
- **version** — every `artifact_update` with new content snapshots a new numbered
  version (like a commit). The live version is what `/artifacts/<slug>` shows; old
  snapshots are kept up to `MAX_VERSIONS = 50` (FIFO-pruned beyond that).
- **base** — every read names one (`v3-1a2b3c4d5e6f7a8b`): the version you read. A
  write over an existing artifact's text passes the base of the version it was made
  from. The user can edit an artifact too, so a write over a version someone made
  after your read is refused (it names who), and so is a write with no base: read it
  again with `artifact_get` and redo the change on what it holds now. A write's reply
  names the new base, so a second change needs no second read.
- **kind** — `widget` (default), `html`, `markdown`, `svg`, `json`, `text`,
  `infographic` (AntV declarative DSL → SVG — see the `infographic-syntax` skill),
  `document` (editorial long-form HTML — see the `editorial-document` skill).

## Tools

| Tool | Use |
|---|---|
| `artifact_list` | List saved artifacts (name/slug/kind/version/tags). Filter by `tag`, `kind`, or text `q`. |
| `artifact_save` | Save new content. Returns the **slug** — the handle for later. |
| `artifact_get` | Read by `slug`: its text, version and **base** (`version=N` for an earlier one; `offset` for the next part of a long one). A Word document, deck or spreadsheet reads as the markup its tool takes. |
| `artifact_update` | Update by `slug` + the `base` you read → new version snapshot. New content, or metadata only (no base). |
| `artifact_versions` | List the numbered snapshots of a slug. |
| `artifact_delete` | Delete an artifact + its history by slug (does not touch any source file). |

`artifact_save`/`artifact_update` take content inline via `content=` or from a
file via `content_file=` (absolute path — a Workspace/cwd file).

## Rules (follow these)

1. **Always `artifact_list` before `artifact_save(kind="widget")`.** Check
   whether a matching widget already exists. If it does, `artifact_update` it
   instead of creating a near-duplicate.

2. **Re-emitting a saved widget requires its slug.** When you render the HTML of
   a saved artifact again in chat, put its slug on the tag so the dashboard links
   the rendered widget back to the library entry:
   `<widget title="…" slug="sales-dashboard">…</widget>`.

3. **Iterate-without-a-slug decision tree.** The user says "iterate on that" but
   you don't have a slug:
   - It was saved earlier this session → you have the slug; `artifact_get` →
     change → `artifact_update` with the base that read named.
   - It was rendered but never saved → **save it yourself now** (`artifact_save`),
     then iterate. Never tell the user "it wasn't saved, so I can't iterate" —
     saving is your job, do it silently.
   - You're unsure which artifact → `artifact_list` (filter by `q`/`kind`) and
     pick by name, or ask only if genuinely ambiguous.

4. **Metadata-only updates don't bump content.** Changing just `description`/`tags`
   via `artifact_update` needs no base and cuts no version — so don't re-send identical
   content just to touch metadata.

## Don't

- Don't save throwaway one-off widgets the user didn't ask to keep. Cost
  discipline: a quick chart inline is fine unsaved.
- Don't create a second artifact when one already covers it — update instead.
- Don't delete an artifact to "replace" it — `artifact_update` is the replace.
- Don't reference a slug you haven't confirmed exists (`artifact_get`/`list` first).

## Worked example

> **User:** "Save this as the team dashboard." *(after you rendered a widget)*

1. `artifact_list(kind="widget", q="team dashboard")` → none.
2. `artifact_save(name="Team Dashboard", kind="widget", content="<the HTML>")`
   → returns `slug="team-dashboard"`.
3. Tell the user: saved → `/artifacts/team-dashboard`.

> **User (next day):** "Add a burndown chart to the team dashboard."

1. `artifact_get(slug="team-dashboard")` → current HTML and its base, e.g. `v3-1a2b3c4d5e6f7a8b`.
2. Add the chart, drawn in inline SVG (see the `visual-output` skill for CSP/theme).
3. `artifact_update(slug="team-dashboard", base="v3-1a2b3c4d5e6f7a8b", content="<new HTML>")`
   → new version. If the user changed it since your read, the call says so and writes
   nothing: read it again and apply the chart to what it holds now.
4. Re-render in chat with `<widget title="Team Dashboard" slug="team-dashboard">…`.
