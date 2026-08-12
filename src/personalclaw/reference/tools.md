# PersonalClaw Tool Reference

Generated from the live tool registry (manifest apiVersion 1). Every registered in-process tool, grouped by provider, with its exact input schema and worked examples.

**Never guess a tool signature — copy it from here.** A hallucinated parameter is the dominant driving failure; the arg names below are schema-verified against the registered tool by the drift test.

## personalclaw-artifacts

### `artifact_delete`

Delete a saved artifact (and its version history) by slug. The source file/widget is not touched.

**Response type:** `artifact.delete.result`

**Safety:** requires approval, risk: destructive

**Parameters:**
- `slug` (string, required)

**Example — Delete an artifact:**

```json
{
  "slug": "launch-plan"
}
```

### `artifact_get`

Fetch a saved artifact's content by slug. Pass version=N for a historical snapshot; omit for the live version.

**Response type:** `artifact.detail`

**Safety:** requires approval

**Parameters:**
- `slug` (string, required)
- `version` (integer, optional) — Snapshot number (omit for live)

**Example — Read an artifact by slug:**

```json
{
  "slug": "launch-plan"
}
```

### `artifact_list`

List saved artifacts (name/slug/kind/version/tags). Filter by tag, kind, collection, or a text query q.

**Response type:** `artifact.list`

**Safety:** requires approval

**Parameters:**
- `collection` (string, optional)
- `kind` (string, optional)
- `q` (string, optional)
- `tag` (string, optional)

**Example — List artifacts of a kind:**

```json
{
  "kind": "document"
}
```

### `artifact_save`

Save content as a named, versioned artifact so it persists beyond chat scrollback and can be iterated on by name in a later session. Use for widgets/HTML tools/dashboards (kind='widget'/'html'), live React components (kind='react' — content is JSX defining a top-level `App` component authored against the window React/ReactDOM globals; renders in a sandboxed canvas), infographics (kind='infographic' — content is AntV declarative DSL, see the infographic-syntax skill), editorial long-form documents (kind='document' — the content must be semantic HTML, NOT markdown; see the editorial-document skill), or docs (kind='markdown' for markdown/prose — headings, lists, tables, code fences; or 'json'/'svg'/'text'). Rule of thumb: markdown body → kind='markdown', HTML body → kind='document'. Returns the slug — the stable handle to reference it later. Pass an explicit slug to re-save/overwrite a known artifact.

**Response type:** `artifact.detail`

**Safety:** requires approval, risk: caution

**Parameters:**
- `collection` (string, optional) — Optional library collection label to group this artifact under.
- `content` (string, optional) — Artifact body (inline)
- `content_file` (string, optional) — Absolute path to read content from instead of inline content
- `description` (string, optional)
- `force` (boolean, optional) — Save a NEW artifact even if one with the same name exists (skip the dedup hint).
- `kind` (string, optional) — Content kind (default widget). Use 'markdown' for prose/markdown bodies (# headings, **bold**, tables, lists); 'document' ONLY for semantic HTML editorial docs, never for markdown.
- `name` (string, required) — Display name
- `slug` (string, optional) — Explicit slug (else derived from name)
- `tags` (array, optional)

**Example — Save generated text as a named artifact:**

```json
{
  "content": "# Launch plan\n...",
  "kind": "document",
  "name": "Launch plan"
}
```

### `artifact_update`

Update a saved artifact by slug, creating a new version snapshot (each agent update is a checkpoint, like a commit). Pass new content inline or via content_file; or update metadata only (description/tags).

**Response type:** `artifact.detail`

**Safety:** requires approval, risk: caution

**Parameters:**
- `collection` (string, optional) — Reassign the library collection label (metadata-only).
- `content` (string, optional)
- `content_file` (string, optional) — Absolute path to read new content from
- `description` (string, optional)
- `slug` (string, required)
- `tags` (array, optional)

**Example — Replace an artifact's content:**

```json
{
  "content": "# Launch plan v2\n...",
  "slug": "launch-plan"
}
```

### `artifact_versions`

List the numbered snapshot versions of an artifact by slug.

**Response type:** `artifact.versions`

**Safety:** requires approval

**Parameters:**
- `slug` (string, required)

**Example — List an artifact's version history:**

```json
{
  "slug": "launch-plan"
}
```

### `deck_create`

Generate a real PowerPoint deck (.pptx) from a markdown OUTLINE and save it as a versioned artifact. Each `##` heading starts a slide, the lines under it become bullets, and `<!-- notes: ... -->` becomes that slide's speaker notes. A leading `#` titles the deck. Write an outline, not prose — paragraphs on a slide are what makes generated decks unreadable. Returns the slug and a download URL.

**Response type:** `artifact.detail`

**Safety:** requires approval, risk: caution

**Parameters:**
- `description` (string, optional) — Optional short description
- `format` (string, optional) — Output format (default 'pptx')
- `markdown` (string, optional) — Outline: `##` per slide, bullets beneath, `<!-- notes: -->` for notes
- `name` (string, required) — Display name for the deck
- `slides` (array, optional) — Alternative to markdown: [{title, body:[str], notes}]
- `slug` (string, optional) — Existing artifact slug to update in place (bumps a version)
- `tags` (array, optional)
- `title` (string, optional) — Deck title slide

**Example — Turn a markdown outline into a PowerPoint deck:**

```json
{
  "markdown": "# Q3 Strategy\n\n## Where we are\n\n- Revenue up 18%\n",
  "name": "Q3 Strategy"
}
```

### `document_create`

Generate a real Word document (.docx) from MARKDOWN and save it as a versioned artifact the user can download. Write ordinary markdown — headings, paragraphs, bullet and numbered lists, tables, fenced code, `---` for a page break — and it is rendered into the document. Do NOT attempt to emit OOXML or base64. Use this when the user wants a file to send, print or hand to someone; use artifact_save with kind='markdown' or 'document' when they just want to read it in the app. Re-running with the same `slug` updates that document and bumps its version instead of creating a near-duplicate. Returns the slug and a download URL.

**Response type:** `artifact.detail`

**Safety:** requires approval, risk: caution

**Parameters:**
- `description` (string, optional) — Optional short description
- `format` (string, optional) — Output format (default 'docx'). Call document_formats to see what is available.
- `html` (string, optional) — Alternative to markdown: HTML (sanitized before use)
- `markdown` (string, optional) — The document body as markdown (the primary input)
- `name` (string, required) — Display name for the document
- `slug` (string, optional) — Existing artifact slug to update in place (bumps a version)
- `source` (string, optional) — Instead of markdown: a knowledge item id or TEXT artifact slug to export as a document
- `tags` (array, optional)
- `title` (string, optional) — Document title; a leading markdown H1 is used when omitted

**Example — Export an existing knowledge item as a Word document:**

```json
{
  "name": "Saved research",
  "source": "<knowledge item id>"
}
```

**Example — Turn markdown into a downloadable Word document:**

```json
{
  "markdown": "# Q3 Review\n\nRevenue grew.\n\n- EMEA up 18%\n",
  "name": "Q3 Review"
}
```

### `document_formats`

List the document formats this instance can actually generate right now. Check before promising the user a format.

**Response type:** `text`

**Safety:** requires approval

**Parameters:**
- _(no parameters)_

**Example — Check which formats are available:**

```json
{}
```

### `image_generate`

Generate an image from a text prompt (or edit an existing one), using the model bound to the 'image_gen' use-case in Settings → Models. The result is saved as a versioned kind='image' artifact; returns its slug so it can be shown, referenced, or embedded in a document. Pass edit_artifact=<slug> to edit a prior generated image in place (a new version on that artifact) instead of creating a new one. Requires an image_gen model to be configured; if none is, it says so.

**Response type:** `artifact.detail`

**Safety:** requires approval, risk: caution

**Parameters:**
- `edit_artifact` (string, optional) — Slug of a prior kind:image artifact to edit in place
- `name` (string, optional) — Artifact display name (else derived from the prompt)
- `prompt` (string, required) — What to generate / how to edit
- `size` (string, optional) — e.g. '1024x1024' (provider-specific; omit for default)

**Example — Generate an image and save it as an artifact:**

```json
{
  "prompt": "a watercolor fox",
  "size": "1024x1024"
}
```

### `sheet_create`

Generate a real spreadsheet (.xlsx) and save it as a versioned artifact. Supply `sheets` as {sheet name: rows} for multiple tabs, or `rows` for a single tab, or `csv` text. Row 0 is treated as the header. KEEP NUMBERS AS NUMBERS (not strings) so the result can be summed and charted — that is the main reason to produce a spreadsheet rather than a table. Returns the slug and a download URL.

**Response type:** `artifact.detail`

**Safety:** requires approval, risk: caution

**Parameters:**
- `csv` (string, optional) — Single-sheet CSV text
- `description` (string, optional) — Optional short description
- `format` (string, optional) — Output format (default 'xlsx')
- `name` (string, required) — Display name for the spreadsheet
- `rows` (array, optional) — Single-sheet rows (array of arrays; row 0 = header)
- `sheets` (object, optional) — Map of sheet name → array of row arrays (row 0 = header)
- `slug` (string, optional) — Existing artifact slug to update in place (bumps a version)
- `tags` (array, optional)

**Example — Build a spreadsheet with numbers kept numeric:**

```json
{
  "name": "Regional sales",
  "sheets": {
    "Sales": [
      [
        "Region",
        "Q1"
      ],
      [
        "EMEA",
        120
      ]
    ]
  }
}
```

### `video_generate`

Generate a video from a text prompt, using the model bound to the 'video_gen' use-case in Settings → Models. The result is saved as a versioned kind='video' artifact; returns its slug so it can be referenced or embedded. Video generation is asynchronous and may take 1-3 minutes. Requires a video_gen model to be configured; if none is, it says so.

**Response type:** `artifact.detail`

**Safety:** requires approval, risk: caution

**Parameters:**
- `aspect_ratio` (string, optional) — e.g. '16:9', '9:16', '1:1' (provider-specific; omit for default)
- `duration_seconds` (number, optional) — Target video duration in seconds (default 5; provider may cap)
- `name` (string, optional) — Artifact display name (else derived from the prompt)
- `prompt` (string, required) — What to generate (scene description)

**Example — Generate a short video:**

```json
{
  "duration_seconds": 5,
  "prompt": "timelapse of clouds"
}
```

### `visualize`

Turn structured DATA into a generative-UI widget (charts, stat tiles, tables, callouts) rendered inline — the agency-free two-step pattern: you produce the data, this separate no-tools step renders it. Pass `data` (a JSON object/array or text) and an optional `hint` describing how to present it (e.g. 'show the monthly totals as a bar chart'). Returns a `<widget kind="genui">` block to embed directly in your reply. Use this instead of hand-writing a widget when you have data to show; it emits ONLY registered components, so invalid output is dropped, never rendered.

**Response type:** `genui.widget`

**Safety:** requires approval

**Parameters:**
- `data` (any, required) — The data to visualize (JSON object/array, or text)
- `hint` (string, optional) — How to present it (chart type, framing, emphasis)
- `title` (string, optional) — Widget title (default 'Visualization')

**Example — Render monthly totals as a bar chart:**

```json
{
  "data": {
    "Feb": 150,
    "Jan": 120,
    "Mar": 180
  },
  "hint": "show as a bar chart of monthly totals"
}
```

## personalclaw-automation

### `automation_create`

Create an automation from ONE natural-language message. Use for 'when a file in ~/notes changes', 'every weekday at 9', 'when my nightly run finishes'. The `when` phrase is routed to the right trigger kind (file/clock/web_watch/…) — a cadence becomes a cron schedule, an event becomes an event trigger. Give `when` + `name` + `message` (what the automation should do). Announced to you on creation and capped at 20 agent-created automations.

**Response type:** `automation.create.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `kind` (string, optional) — Optional explicit kind, bypassing NL routing (file/clock/event/web_watch/idle/webhook/run_completed).
- `message` (string, optional) — What the automation should do when it fires.
- `name` (string, required) — A short name for the automation.
- `spec` (object, optional) — Optional explicit trigger spec when `kind` is given.
- `when` (string, optional) — Plain English for WHEN it runs: a cadence ('every weekday at 9') or an event ('when a file in ~/notes changes').

**Example — Create a file-watch automation in one message:**

```json
{
  "message": "Summarize the changed file into my knowledge base",
  "name": "Summarize notes",
  "when": "when a file in ~/notes changes"
}
```

**Example — Create a scheduled automation:**

```json
{
  "message": "digest",
  "name": "Daily digest",
  "when": "every weekday at 9"
}
```

### `automation_delete`

Delete an automation permanently. Requires confirm: true — pause it instead if you might want it back.

**Response type:** `automation.delete.result`

**Safety:** requires approval, risk: destructive

**Parameters:**
- `confirm` (boolean, required)
- `id` (string, required) — The automation id (e.g. 'file:my-notes').

**Example — Delete an automation permanently:**

```json
{
  "confirm": true,
  "id": "file:summarize-notes"
}
```

### `automation_delete_all`

Delete every automation YOU created (created_by=agent), in one call. Requires confirm: true. Never touches automations the user made.

**Response type:** `automation.delete_all.result`

**Safety:** requires approval, risk: destructive

**Parameters:**
- `confirm` (boolean, required)

**Example — Delete every automation you created:**

```json
{
  "confirm": true
}
```

### `automation_history`

Recent run/fire rows for an automation, with typed outcomes — to self-debug why an automation did or did not do something.

**Response type:** `automation.history.result`

**Safety:** requires approval

**Parameters:**
- `id` (string, required) — The automation id (e.g. 'file:my-notes').
- `n` (integer, optional) — How many rows (default 10).

**Example — Recent runs of an automation:**

```json
{
  "id": "file:summarize-notes",
  "n": 10
}
```

### `automation_list`

List automations with health rollups. Optional `kind` and `state` ('active'/'paused') filters. Broken rows are shown, not hidden.

**Response type:** `automation.list.result`

**Safety:** requires approval

**Parameters:**
- `kind` (string, optional)
- `state` (string, optional)

**Example — List all automations with health:**

```json
{}
```

**Example — List only active file automations:**

```json
{
  "kind": "file",
  "state": "active"
}
```

### `automation_pause`

Pause an automation — it stops firing on its own but is not deleted.

**Response type:** `automation.pause.result`

**Safety:** requires approval

**Parameters:**
- `id` (string, required) — The automation id (e.g. 'file:my-notes').

**Example — Pause an automation:**

```json
{
  "id": "file:summarize-notes"
}
```

### `automation_resume`

Resume a paused automation. Refuses (with the reason) if the row has a parse error that must be fixed first.

**Response type:** `automation.resume.result`

**Safety:** requires approval

**Parameters:**
- `id` (string, required) — The automation id (e.g. 'file:my-notes').

**Example — Resume a paused automation:**

```json
{
  "id": "file:summarize-notes"
}
```

### `automation_run`

Fire an automation now. `dry_run: true` walks the gates and reports what WOULD run without executing. A manual run bypasses quiet-hours and duty limits but never the injection screen, capability allowlist, or budget.

**Response type:** `automation.run.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `dry_run` (boolean, optional) — Observe without executing.
- `id` (string, required) — The automation id (e.g. 'file:my-notes').

**Example — Fire an automation now:**

```json
{
  "id": "file:summarize-notes"
}
```

**Example — Preview what would run without executing:**

```json
{
  "dry_run": true,
  "id": "file:summarize-notes"
}
```

### `automation_update`

Patch an automation. Only settable fields apply (name, spec, gates, workflow, enabled, delivery, …); health/run fields are rejected and reported.

**Response type:** `automation.update.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `id` (string, required) — The automation id (e.g. 'file:my-notes').
- `patch` (object, required) — Fields to change.

**Example — Rename an automation:**

```json
{
  "id": "file:summarize-notes",
  "patch": {
    "name": "Notes summarizer"
  }
}
```

## personalclaw-core

### `dashboard_tile_propose`

PROPOSE a saved artifact as a dashboard tile on the user's composable home. The artifact must already be saved (a slug); this pins a PROPOSAL that renders with an accept/dismiss chip — the user decides. You never silently rearrange their home. Use when you've built a view/artifact the user would want to keep visible (a live dashboard, a status board). Args: slug (the artifact slug), size (s|m|l|full, default m), view_id (target view; omit for the Overview home).

**Response type:** `dashboard.tile.propose.result`

**Safety:** requires approval

**Parameters:**
- `size` (string, optional) — Flow-layout size hint (default m). No coordinates.
- `slug` (string, required) — The saved artifact's slug to pin as a tile.
- `view_id` (string, optional) — Target view id. Omit to propose onto the Overview home.

**Example — Propose a saved dashboard artifact as a tile on the home:**

```json
{
  "size": "l",
  "slug": "sales-live-board"
}
```

### `get_context`

Call at the START of every task to load this project's routed context. Returns, in lost-in-the-middle order: hard RULES & directives (the project brief + operating procedure) at the top; then scored mid-tier content — how this user works (memory-derived lessons/preferences), the skills available here, and titled pointers to reference material (knowledge items — retrieve a body on demand, never inlined); and at the bottom an L0 CATALOG of what was NOT loaded, each with the tool/route that pulls it (memory_recall, skill_invoke, GET /api/knowledge/items). Optionally pass a `query` to score the mid tier against the task at hand, and a `project_id` to target a specific project (defaults to this session's project). Read-only: never writes to memory or knowledge.

**Response type:** `context.routed.manifest`

**Safety:** requires approval

**Parameters:**
- `project_id` (string, optional) — Target project id (e.g. 'p-1a2b3c4d'). Omit to use the current session's bound project, else the Personal default.
- `query` (string, optional) — What you're about to do — scores the mid-tier memory/knowledge content. Omit to score against the project itself.

**Example — Load the current project's routed context at task start:**

```json
{}
```

**Example — Score the context against the task at hand:**

```json
{
  "project_id": "p-1a2b3c4d",
  "query": "add a settings toggle"
}
```

### `hook_register`

Register a webhook listener so an external system can inject a message into a dedicated agent session later. Returns the webhook URL and session key. Use this when you need to hand off to an external process (e.g. submit a PR, then wait for CI to call back with results). The external system POSTs to the returned URL with the results.

**Response type:** `hook.register.result`

**Safety:** requires approval

**Parameters:**
- `context_summary` (string, required) — Summary of current work context for session resume
- `hook_id` (string, required) — Unique identifier for this hook (e.g. 'review:pr-123')

**Example — Register a follow-up hook for the current work:**

```json
{
  "context_summary": "re-check the site is live after deploy",
  "hook_id": "verify-deploy"
}
```

### `loop_nudge_stop`

Stop the auto-nudge loop driving your current session. Call this when you determine the loop should halt (e.g. goal complete, blocked on user input, or a STOP sentinel file indicates shutdown). Removes the loop from the AutoNudgeService so no further nudges fire into this session. Safe to call even if no loop is active — returns a no-op message.

**Response type:** `loop.nudge_stop.result`

**Safety:** requires approval

**Parameters:**
- `reason` (string, optional) — Why the loop is being stopped (logged for audit)

**Example — Stop the autonomous nudge loop for this session:**

```json
{
  "reason": "goal reached"
}
```

### `notify`

Notify the user via their configured notification channel(s) (dashboard notification, plus any connected messaging channel such as Slack or Discord). By default reaches the owner. Use this whenever you decide someone should be told something — most commonly in silent cron jobs, but any time proactive notification is needed.

Delivery contract for cron jobs:
  1. Try the originating dashboard session first (session="origin"), so the session agent can react to the message, not just display it. When injection succeeds, the message appears in the chat UI — no extra notification is fired.
  2. Fall through to the owner's messaging channel if origin is unreachable (tab closed, history deleted, or cron has no origin — e.g. created from the dashboard UI).
  3. On the fallback path (including session="channel" and non-cron callers), a dashboard notification also fires so messages that couldn't reach their origin still surface. Invariant: messages are never silently dropped.

session param:
  "origin"  — inject into the session that spawned this cron.
  "channel" — explicitly route to the owner's messaging channel, bypassing origin.
  omitted + cron caller → auto-applies "origin" (you usually want this — pick "channel" only if the message should specifically reach the messaging channel and not the spawning chat).
  omitted + non-cron caller → owner channel (default behavior).

Explicit channel=... or user=... always wins and suppresses the auto-default.

**Response type:** `notify.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `blocks` (array, optional) — Optional rich-message blocks array (Block Kit format). When provided, the message is sent as a rich message with text as fallback.
- `channel` (string, optional) — Target channel ID (e.g. C0123ABC456). Must be a tracked channel. Omit to send to owner DM.
- `reply_broadcast` (boolean, optional) — When true and 'thread_ts' is set, also broadcast the threaded reply to the channel's main message list. Requires 'thread_ts' — passing reply_broadcast=true without thread_ts returns 400. Defaults to false.
- `session` (string, optional) — Routing opt-in/opt-out for cron messages. "origin" injects into the dashboard session that created this cron (auto-applied for cron callers that set neither channel nor user). "channel" explicitly routes to the owner's messaging channel, bypassing origin. Fallback paths (origin unreachable, explicit "channel", non-cron caller) also fire a dashboard notification so the message isn't silently dropped.
- `text` (string, required) — Message text. Also used as fallback when blocks are provided.
- `thread_ts` (string, optional) — Optional channel thread timestamp (e.g. '1712793600.123456'). When provided, the message is posted as a threaded reply under that parent message. Works with 'channel' (thread in channel) or 'user' (thread in DM).
- `title` (string, optional) — Optional title for the notification
- `unfurl_links` (boolean, optional) — Whether to unfurl URL link previews. Defaults to true.
- `unfurl_media` (boolean, optional) — Whether to unfurl media (images/video) previews. Defaults to true.
- `user` (string, optional) — Target user ID (e.g. U0123ABC456) to DM. Must be an allowed user. Omit to send to owner DM.

**Example — Send a notification to the user:**

```json
{
  "text": "The nightly backup finished cleanly."
}
```

### `notify_attachment`

Send a file to the user. Copies the file to the outbox and notifies the dashboard/channel with a download link. Use when you've generated a report, export, artifact, or any file the user should receive.

**Response type:** `notify.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `description` (string, optional) — Brief description of what the file is
- `path` (string, required) — Absolute path to the file to send

**Example — Notify with a file attachment:**

```json
{
  "description": "Weekly report",
  "path": "artifacts/report.pdf"
}
```

### `project_context_review`

Review THIS conversation and propose updates to the current project's context — its instructions, an inlined context file, or a skill. Call ONLY when the user asks you to review/capture what was established here (e.g. 'review this chat and update the project'); it does not run automatically. You identify the changes from the conversation and pass them as `items`, each with a one-line `rationale` the user reads before deciding. Nothing is written: each item becomes a PROPOSAL in the review queue, and the project changes only when the user accepts it there. A change the user already declined is not re-proposed.

**Response type:** `project.context.review.result`

**Safety:** requires approval

**Parameters:**
- `items` (array, required) — The proposed changes.
- `project_id` (string, optional) — Target project id. Omit to use this session's bound project.

**Example — Propose a project instruction from what this chat established:**

```json
{
  "items": [
    {
      "body": "Always run `make lint` before committing.",
      "kind": "project_instruction",
      "rationale": "We agreed lint must pass pre-commit"
    }
  ]
}
```

### `skill_invoke`

Load a skill's full instructions by name. Your context carries only a compact INDEX of available skills (name + one-line description); when a listed skill fits the task, call this to pull its complete step-by-step body before acting. Prefer this over reading the skill file directly — it records the skill as used so the library can keep what helps and retire what doesn't.

**Response type:** `skill.invoke.result`

**Safety:** requires approval

**Parameters:**
- `name` (string, required) — The skill name from the index (e.g. 'tiny-url' or 'auto/release').

**Example — Load a skill's instructions into the session:**

```json
{
  "name": "pclaw-api"
}
```

### `skill_promote`

PROPOSE a finished piece of work as a reusable skill — the retroactive companion to skill_remember. Use after a task or workflow run SUCCEEDED and the procedure is worth having next time; you may call it unprompted if you notice you worked something out that you (or the user) will need again. Nothing is written: this files a PROPOSAL in the review queue, and the skill exists only once the user accepts it there. A promotion the user already declined is not re-proposed. Args: name (proposed skill name), description (when to use it — one line), procedure (the steps, as markdown), rationale (why it is worth keeping — the line the user reads before deciding), run_id (optional; a completed workflow run to promote — it must have finished successfully).

**Response type:** `skill.promote.proposal.result`

**Safety:** requires approval

**Parameters:**
- `description` (string, required) — One line on when this skill applies.
- `name` (string, required) — Proposed skill name, e.g. 'publish the nightly report'.
- `procedure` (string, required) — The steps as markdown — exactly what the skill will contain once accepted. Do not put the rationale here.
- `rationale` (string, required) — Why keep this — shown in the review queue.
- `run_id` (string, optional) — A completed workflow run to promote. Omit to promote this conversation instead.

**Example — Promote a completed run into a skill proposal (nothing written):**

```json
{
  "description": "Build and publish the nightly report end to end",
  "name": "publish the nightly report",
  "procedure": "1. Fetch the source feed and validate the payload.\n2. Render the report.\n3. Publish it and verify the published copy.",
  "rationale": "We worked this out from scratch and it will recur nightly",
  "run_id": "run-2f8a1c"
}
```

### `skill_remember`

Capture a skill the USER just taught you ("from now on…", "always do X", "remember this workflow"). Writes a SESSION-LIVE draft: it's active for the rest of THIS chat immediately, and at the chat's end the user is asked whether to save it permanently (to this agent or all agents) or forget it. Use ONLY for durable how-to the user explicitly wants kept — not for one-off facts (that's memory) or transient state. Args: title (short name), body (the steps/rule).

**Response type:** `skill.remember.result`

**Safety:** requires approval

**Parameters:**
- `body` (string, required) — The procedure/rule to remember (markdown).
- `title` (string, required) — Short skill name, e.g. 'deploy checklist'.

**Example — Persist a reusable how-to as a new skill:**

```json
{
  "body": "Run npm run deploy from personalclaw.dev/",
  "title": "Deploy the website"
}
```

### `skill_search`

Find a skill by capability across your ENTIRE skill library — not just the skills surfaced in your context this turn. Use when the task might have a matching skill but you don't see one in the index. Returns ranked name + description; then call skill_invoke(name) to load its full steps. Args: query (str), optional limit (int).

**Response type:** `skill.search.results`

**Safety:** requires approval

**Parameters:**
- `limit` (integer, optional) — Max results (default 20).
- `query` (string, required) — What you're trying to do (capability/intent).

**Example — Find skills matching a query:**

```json
{
  "limit": 5,
  "query": "write a blog post"
}
```

### `suggest_template`

Offer to save a recurring task shape as a reusable workflow template. LOCAL-ONLY: it decides whether the offer is welcome and returns the wording, it never saves anything — workflow_plan then workflow_author do that. Call it when you notice the user has asked for the same SHAPE of work several times (the shape, not the exact words: 'summarize my new issues' and 'summarize today's issues' are one shape). Anti-nag rules are enforced here and the state persists, so a shape the user declined stays declined across restarts and a recently-offered one is in cooldown. When it answers no, do not mention templates in that turn.

**Response type:** `template.nudge.decision`

**Safety:** requires approval

**Parameters:**
- `decision` (string, optional) — 'observe' (default) counts one more occurrence and asks whether to offer. Report the user's answer to a previous offer with 'accepted' or 'declined' — a decline is permanent for this shape.
- `shape` (string, required) — A short stable name for the recurring shape, e.g. 'summarize new issues'. The SAME shape must produce the same string each time or the recurrence count never accumulates.

**Example — Count a recurring shape and ask whether to offer a template:**

```json
{
  "shape": "summarize new issues"
}
```

**Example — Record that the user refused — permanent for this shape:**

```json
{
  "decision": "declined",
  "shape": "summarize new issues"
}
```

### `template_save_from_session`

Propose saving the multi-step procedure just carried out in this session as a reusable workflow template. Files a DRAFT proposal for the user to accept or reject — it never writes a definition, so use it freely when the work looks repeatable (use workflow_author instead when the user asks to SAVE a workflow outright). A deterministic gate scores the steps first and may decline (one-step plans, no reusable placeholders, a template that already exists); the decline and its reason come back to you. Put {{placeholders}} wherever a value would differ on the next run — steps with nothing parameterizable are a recording of one run, not a template, and get declined.

**Response type:** `template.save.proposal.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `description` (string, optional) — One line on what the procedure accomplishes.
- `name` (string, required) — Proposed template name: lowercase, digits, hyphens.
- `steps` (array, required) — The procedure, one step per entry, in order. Use {{placeholders}} for values that change between runs.

**Example — Propose the session's procedure as a reusable template (draft only):**

```json
{
  "description": "Build and publish the nightly report",
  "name": "nightly-report",
  "steps": [
    "fetch {{source_url}} and validate the payload",
    "transform the result into {{format}}",
    "publish it to {{target}} and verify the output"
  ]
}
```

### `wait`

Pause execution for a specified duration while preserving full session context. Use when waiting for external systems (code review, CI pipeline, deployment). Max 1800s (30 min).

**Response type:** `wait.result`

**Safety:** requires approval

**Parameters:**
- `reason` (string, required) — Why we are waiting (shown to user)
- `seconds` (integer, required) — Duration to wait in seconds (60-1800)

**Example — Pause before re-checking a long-running job:**

```json
{
  "reason": "let the build finish",
  "seconds": 30
}
```

## personalclaw-inbox-tools

### `post_to_inbox`

Surface a message to the user in their Inbox triage queue — use when you finish something worth reporting, need a decision, or have a heads-up, and no one is watching the chat live. Args: message (str), kind ('notification'|'question'|'fyi', default 'notification'; 'question' asks for a reply), optional context (str — why/what you used).

**Response type:** `inbox.post.result`

**Safety:** risk: caution

**Parameters:**
- `context` (string, optional)
- `kind` (string, optional)
- `message` (string, required)

**Example — Post a message to the user's inbox:**

```json
{
  "kind": "notification",
  "message": "PR #42 is ready for review"
}
```

## personalclaw-knowledge-tools

### `knowledge_create`

Add an item to the user's knowledge library. Args: type ('note'|'fleeting'|'journal'|'gist'|'bookmark', default 'note'), title (str), content (str — the note/gist body), url (str — for bookmark), optional tags (list of str), optional gist_language (str — the code language for a gist, e.g. 'python').

**Response type:** `knowledge.detail`

**Safety:** risk: caution

**Parameters:**
- `content` (string, optional)
- `gist_language` (string, optional)
- `tags` (array, optional)
- `title` (string, optional)
- `type` (string, optional)
- `url` (string, optional)

**Example — Save a note to the knowledge base:**

```json
{
  "content": "1. npm ci\n2. npm run deploy",
  "title": "Deploy runbook",
  "type": "note"
}
```

### `knowledge_get`

Fetch one knowledge item by id (title, type, content, tags, summary). Args: id (str).

**Response type:** `knowledge.detail`

**Parameters:**
- `id` (string, required)

**Example — Read a knowledge item by id:**

```json
{
  "id": "kn_abc123"
}
```

### `knowledge_search`

Search the user's knowledge library (notes, bookmarks, docs). Args: query (str), optional limit (int, default 8).

**Response type:** `knowledge.search.results`

**Parameters:**
- `limit` (integer, optional)
- `query` (string, required)

**Example — Search the knowledge base:**

```json
{
  "limit": 5,
  "query": "deployment runbook"
}
```

### `knowledge_stats`

Get an overview of the knowledge library for gap detection: total item count, a by-type breakdown, and the most common tags. No args.

**Response type:** `knowledge.stats`

**Parameters:**
- _(no parameters)_

**Example — Get knowledge-base counts:**

```json
{}
```

### `knowledge_update`

Update an existing knowledge item and re-enrich it. Args: id (str, required), and any of title (str), content (str), tags (list of str), url (str), gist_language (str — only for gist items; sets the code language for syntax highlighting), is_pinned (bool), is_archived (bool). Editing content/url re-runs extraction.

**Response type:** `knowledge.detail`

**Safety:** risk: caution

**Parameters:**
- `content` (string, optional)
- `gist_language` (string, optional)
- `id` (string, required)
- `is_archived` (boolean, optional)
- `is_pinned` (boolean, optional)
- `tags` (array, optional)
- `title` (string, optional)
- `url` (string, optional)

**Example — Pin a knowledge item:**

```json
{
  "id": "kn_abc123",
  "is_pinned": true
}
```

## personalclaw-memory

### `memory_forget`

Remove lessons whose rule contains the given substring

**Response type:** `memory.forget.result`

**Safety:** requires approval, risk: destructive

**Parameters:**
- `query` (string, required) — Substring to match

**Example — Forget rules matching a query:**

```json
{
  "query": "concise commit messages"
}
```

### `memory_list`

List all saved lessons and corrections

**Response type:** `memory.list`

**Safety:** requires approval

**Parameters:**
- _(no parameters)_

**Example — List all remembered rules:**

```json
{}
```

### `memory_recall`

Look up your persistent memory on demand — query-relevant facts and past conversation fragments. Your always-on context only carries a small manifest of your most-used facts; call this when you need to recall something specific the user told you before, or context from an earlier session. Set deep=true for a broader, deeper search.

**Response type:** `memory.recall.results`

**Safety:** requires approval

**Parameters:**
- `deep` (boolean, optional) — Broader/deeper search (default false)
- `query` (string, required) — What to recall (a topic, name, or question)

**Example — Recall relevant memories for a topic:**

```json
{
  "deep": false,
  "query": "how do I like commit messages"
}
```

### `memory_remember`

Save a learned correction or preference that persists across all future sessions. MUST be called when the user corrects you, says 'always do X', 'never do Y', or 'remember that'. Include both the rule (what to do) and negative (what not to do).

**Response type:** `memory.remember.result`

**Safety:** requires approval

**Parameters:**
- `category` (string, required) — Category: tool, preference, or knowledge
- `negative` (string, optional) — What NOT to do (optional)
- `rule` (string, required) — The lesson to remember
- `scope` (string, optional) — Where to save: 'global' (default, all workspaces) or 'workspace' (active workspace only)
- `workspace` (string, optional) — Absolute working-directory path (required when scope='workspace'). Copy it verbatim from the WORKSPACE IDENTITY block in your session context — a relative name or a bare project name is refused, because a workspace lesson is matched to a directory exactly.

**Example — Persist a durable preference:**

```json
{
  "category": "style",
  "rule": "Prefer concise commit messages"
}
```

## personalclaw-project-tools

### `project_run_create`

Create a project RUN — an autonomous, multi-cycle execution (a 'loop') — from a plan you shaped with the user. USE WHEN the user wants substantial over-many-cycles work rather than a one-shot chat answer. The `kind` selects the engine: 'code' (SDLC plan→execute in a codebase — feature/refactor/bugfix, gated stages, its own workspace + tasks), 'goal' (open-ended research-or-action toward an outcome — investigate/monitor/drive to done), 'research' (deep web research → a synthesized report), 'design' (a design system — tokens/components/exports), or 'general' (a generic iterative task). Offer it, then create on the user's go. Does NOT start it — call project_run_start on their go. (To create a plain task CONTAINER instead, use project_create.) Args: kind (required), task (str, required, 12+ chars — the goal/work), name?, project_id? (bind under an existing Project container), attended?, max_cycles?, success_criteria?. kind 'code': project_kind? (greenfield|brownfield), entry_stage?, workspace_dir? (brownfield needs one to start), stage_plan? ([{stage,title,objective,exit_criteria?,tasks?}]), verify_command?, test_command?. kind goal/research/design/general: sub_goals? ([str]), deliverables? ([str]), scope? ([str]), goal_type? (goal only), rubric? ([str]).

**Response type:** `project.run.detail`

**Safety:** requires approval, risk: caution

**Parameters:**
- `attended` (boolean, optional)
- `deliverables` (array, optional)
- `entry_stage` (string, optional)
- `goal_type` (string, optional)
- `kind` (string, required)
- `max_cycles` (integer, optional)
- `name` (string, optional)
- `project_id` (string, optional)
- `project_kind` (string, optional)
- `rubric` (array, optional)
- `scope` (array, optional)
- `stage_plan` (array, optional)
- `sub_goals` (array, optional)
- `success_criteria` (string, optional)
- `task` (string, required)
- `test_command` (string, optional)
- `verify_command` (string, optional)
- `workspace_dir` (string, optional)

**Example — Create a code project run:**

```json
{
  "kind": "code",
  "name": "health-endpoint",
  "task": "Add a health endpoint to the API"
}
```

### `project_run_list`

List the user's project runs (autonomous executions) with kind + live status, to find one to report on or resume. Args: optional kind (filter: code|goal|general|design|research), limit (int).

**Response type:** `project.run.list`

**Parameters:**
- `kind` (string, optional)
- `limit` (integer, optional)

**Example — List recent project runs:**

```json
{
  "kind": "code",
  "limit": 10
}
```

### `project_run_start`

Launch a created project run (any kind), or resume a paused/failed one. Args: project_id (str, required — the run id).

**Response type:** `project.run.detail`

**Safety:** requires approval, risk: caution

**Parameters:**
- `project_id` (string, required)

**Example — Start a created project run:**

```json
{
  "project_id": "prj_abc123"
}
```

### `project_run_status`

Read live progress of any project run — status, stage/phase progress, cycles, latest finding, and any blocker / needs-input — to report to the user. Args: project_id (str, required — the run id).

**Response type:** `project.run.status`

**Parameters:**
- `project_id` (string, required)

**Example — Check a project run's status:**

```json
{
  "project_id": "prj_abc123"
}
```

## personalclaw-prompts

### `prompt_render`

Load a saved Prompt and render it with variable values filled in, returning the final prompt text for you to act on. Saved Prompts are reusable, parameterized instructions the user maintains (with {{variable}} placeholders). Use when a defined prompt covers what you need — e.g. to follow a standard report/checklist procedure on demand for a specific subject. Pass values for the prompt's variables in 'vars'. Read-only: this returns the rendered text; you then carry it out with your other tools.

**Response type:** `prompt.render.result`

**Safety:** requires approval

**Parameters:**
- `prompt_id` (string, required) — The saved prompt name to render.
- `vars` (object, optional) — Values for the prompt's {{variable}} placeholders (name → value).

**Example — Render a saved prompt with variables:**

```json
{
  "prompt_id": "review",
  "vars": {
    "file": "server.py"
  }
}
```

## personalclaw-subagents

### `subagent_list`

List all running and completed subagents (read-only, no commands executed)

**Response type:** `subagent.list`

**Safety:** requires approval

**Parameters:**
- _(no parameters)_

**Example — List running/finished subagents:**

```json
{}
```

### `subagent_run`

Spawn subagent(s) to run tasks in the background. Returns immediately — results arrive as [Subagent completion event] messages in your conversation. For parallel work, use 'tasks' array. Tasks are automatically batched if they exceed the concurrency limit. WAIT for all completion events before responding to the user.

**Response type:** `subagent.run.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `agent` (string, optional) — Agent name for the subagent. Use subagent_list to see available agents.
- `agents` (array, optional) — Agent names corresponding to each task in 'tasks' array
- `cwd` (string, optional) — Optional absolute path to launch the subagent subprocess in, instead of the default sandbox. Enables cwd-relative resource globs (.personalclaw/steering, AGENTS.md) to resolve against this directory. Must be under a configured subagent_cwd_allowed_roots entry (default: [~/workspace, ~/workplace]). Applies to all tasks in a batch spawn.
- `max_turns` (integer, optional) — Override tool-call budget for this spawn (default: config or 100)
- `task` (string, optional) — Single task description
- `tasks` (array, optional) — Multiple tasks to run in parallel

**Example — Run one subagent task:**

```json
{
  "agent": "general-purpose",
  "task": "Summarize the open PRs"
}
```

### `subagent_status`

Call with the agent ID from a subagent completion event to retrieve the full output in the event of truncation.

**Response type:** `subagent.status`

**Safety:** requires approval

**Parameters:**
- `agent_id` (string, required) — Subagent ID from completion event

**Example — Check one subagent's status:**

```json
{
  "agent_id": "sub-abc123"
}
```

## personalclaw-tasks-tools

### `project_create`

Create a project (a scoping container for task lists). Args: name (str, required, unique), optional agent_instructions_template (str).

**Response type:** `project.detail`

**Safety:** risk: caution

**Parameters:**
- `agent_instructions_template` (string, optional)
- `name` (string, required)

**Example — Create a project to group tasks:**

```json
{
  "name": "Launch"
}
```

### `project_list`

List projects (with their task lists). No args.

**Response type:** `project.list`

**Parameters:**
- _(no parameters)_

**Example — List projects:**

```json
{}
```

### `task_create`

Create a task in the user's task system. Args: title (str, required), optional description (str), priority ('critical'|'high'|'medium'|'low'|'trivial', default medium), task_list_id (str — place it in a task list; the task's project label is derived from the list), labels (list of str), due (str ISO date), exit_criteria (list of {description, met?}), action_plan (list of {content} ordered), depends_on (list of task ids that must finish first). Cycles are rejected.

**Response type:** `task.detail`

**Safety:** risk: caution

**Parameters:**
- `action_plan` (array, optional)
- `depends_on` (array, optional)
- `description` (string, optional)
- `due` (string, optional)
- `exit_criteria` (array, optional)
- `labels` (array, optional)
- `priority` (string, optional)
- `task_list_id` (string, optional)
- `title` (string, required)

**Example — Create a task:**

```json
{
  "due": "2026-08-01",
  "priority": "high",
  "title": "Write launch email"
}
```

### `task_get`

Fetch one task by id (full detail incl. exit criteria, plan, deps). Args: id (str).

**Response type:** `task.detail`

**Parameters:**
- `id` (string, required)

**Example — Read a task by id:**

```json
{
  "id": "tsk_abc123"
}
```

### `task_list`

List tasks, most-recent first. Args: optional status ('open'|'in_progress'|'blocked'|'done'|'cancelled'), project (str label), task_list_id (str), limit (int, default 25).

**Response type:** `task.list`

**Parameters:**
- `limit` (integer, optional)
- `project` (string, optional)
- `status` (string, optional)
- `task_list_id` (string, optional)

**Example — List open tasks:**

```json
{
  "limit": 20,
  "status": "open"
}
```

### `task_list_create`

Create a task list inside a project. Args: name (str, required), optional project_id (str) or project_name (str, find-or-create); repeatable (bool — place under the Repeatable project). With no project it lands in 'Chore'.

**Response type:** `task.list_container.detail`

**Safety:** risk: caution

**Parameters:**
- `name` (string, required)
- `project_id` (string, optional)
- `project_name` (string, optional)
- `repeatable` (boolean, optional)

**Example — Create a task list inside a project:**

```json
{
  "name": "Backlog",
  "project_name": "Launch"
}
```

### `task_ready`

List tasks that can be started now (no unfinished prerequisites), optionally scoped. Args: optional project (str), task_list_id (str).

**Response type:** `task.list`

**Parameters:**
- `project` (string, optional)
- `task_list_id` (string, optional)

**Example — List tasks whose dependencies are met:**

```json
{
  "project": "launch"
}
```

### `task_search`

Search tasks by text + filters. Args: optional query (str over title+description), status (list), priority (list), tags (list), project (str), sort_by ('relevance'|'created_at'|'updated_at'|'priority'), limit (int).

**Response type:** `task.list`

**Parameters:**
- `limit` (integer, optional)
- `priority` (array, optional)
- `project` (string, optional)
- `query` (string, optional)
- `sort_by` (string, optional)
- `status` (array, optional)
- `tags` (array, optional)

**Example — Search tasks:**

```json
{
  "limit": 20,
  "query": "email",
  "status": [
    "open"
  ]
}
```

### `task_update`

Update a task. Args: id (str, required), and any of title, description, status ('open'|'in_progress'|'blocked'|'done'|'cancelled' — 'done' is rejected while exit criteria are incomplete), priority, task_list_id, labels, due, exit_criteria, action_plan, depends_on. The 'project' label is derived from the task list and cannot be set directly.

**Response type:** `task.detail`

**Safety:** risk: caution

**Parameters:**
- `action_plan` (array, optional)
- `depends_on` (array, optional)
- `description` (string, optional)
- `due` (string, optional)
- `exit_criteria` (array, optional)
- `id` (string, required)
- `labels` (array, optional)
- `priority` (string, optional)
- `status` (string, optional)
- `task_list_id` (string, optional)
- `title` (string, optional)

**Example — Mark a task done:**

```json
{
  "id": "tsk_abc123",
  "status": "done"
}
```

## personalclaw-ui-docs

### `ui_get`

Get the full documentation for one ui/ component (props with types + whether required, best-practice Do/Don'ts, and the anatomy), or for a design token, or the whole token catalog (name='tokens'). Optionally narrow to one section.

**Response type:** `ui.get.doc`

**Parameters:**
- `name` (string, required) — Component name (e.g. 'Button', 'SidePanel'), a design token var (e.g. '--color-primary'), or 'tokens' for the full token catalog.
- `section` (string, optional) — Optional: restrict the component doc to one of 'props', 'bestPractices', 'anatomy', or 'description'.

**Example — Read a component's full props + best practices:**

```json
{
  "name": "SidePanel"
}
```

**Example — Read just one section of a component's doc:**

```json
{
  "name": "Button",
  "section": "props"
}
```

### `ui_search`

Search the web/src/ui design-system kit (components + design tokens) by keyword. Returns brief hits — name, kind, one-line description — so you can find the right primitive to reach for instead of hand-rolling markup. Follow up with ui_get(name) for the full props + best-practices of any hit.

**Response type:** `ui.search.results`

**Parameters:**
- `limit` (integer, optional) — Max hits to return (default 8, cap 25).
- `query` (string, required) — Search terms, e.g. 'button', 'side panel', 'text input', or a token like 'primary color'.

**Example — Find the design-system primitive for a labelled action:**

```json
{
  "limit": 5,
  "query": "button submit"
}
```

**Example — Search for a design token:**

```json
{
  "query": "primary color"
}
```

## personalclaw-workflows

### `workflow_audit`

Diagnose workflow runs that drifted — nodes stuck running, gates nobody can answer, expired waits, runs whose status was never written. Defaults to dry_run=true, which only REPORTS. Pass dry_run=false to repair; a run with a live controller is reported and left alone either way.

**Response type:** `workflow.audit.report`

**Safety:** requires approval

**Parameters:**
- `dry_run` (boolean, optional) — true (default) = report only; false = repair.

**Example — Report drifted runs without repairing:**

```json
{}
```

### `workflow_author`

Save a workflow definition from an explicit DAG spec — the low-level authoring tool. Use when you already know the node structure; use workflow_plan instead to turn a natural-language goal into a spec. Pass save=false to VALIDATE ONLY and get the issue list back without writing anything, which is the cheap way to iterate. Never put a literal API key in the spec: reference credentials as {{secret:KEY}}.

**Response type:** `workflow.def.saved`

**Safety:** requires approval

**Parameters:**
- `description` (string, optional)
- `inputs` (object, optional) — Declared inputs: name → {type, required, default, help}.
- `name` (string, required) — Definition name: lowercase letters, digits, hyphens.
- `root` (object, required) — The root node of the spec tree. Call workflow_manifest for the node taxonomy, binding pipes and allowed shapes.
- `save` (boolean, optional) — false = validate only, write nothing (default true).
- `tags` (array, optional)

**Example — Validate a two-stage spec without saving it:**

```json
{
  "name": "triage-inbox",
  "root": {
    "children": [
      {
        "config": {
          "prompt": "Classify: {{inputs.text}}"
        },
        "id": "classify",
        "kind": "infer"
      }
    ],
    "id": "main",
    "kind": "sequence"
  },
  "save": false
}
```

### `workflow_cancel`

Cancel a run. The intent is persisted, so it is honoured even if the gateway restarts mid-cancel; in-flight nodes are stopped and the run finalizes as cancelled.

**Response type:** `workflow.run.cancelled`

**Safety:** requires approval

**Parameters:**
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Cancel a run:**

```json
{
  "run_id": "a1b2c3d4"
}
```

### `workflow_delete_def`

Delete a workflow definition. Existing runs of it are unaffected — they carry their own copy of the spec. Bundled templates cannot be deleted.

**Response type:** `workflow.def.deleted`

**Safety:** requires approval, risk: destructive

**Parameters:**
- `name` (string, required)

**Example — Delete a definition:**

```json
{
  "name": "old-workflow"
}
```

### `workflow_edit`

Edit a RUNNING workflow's unexecuted nodes. Ops: update_node, insert, delete, move, set_input, skip. Returns a cascade preview naming every node that would re-run; if it would re-run already-completed work you must resubmit with confirm_cascade=true. Running and finished nodes cannot be edited — rewind one first. Pass expect_version from workflow_status to avoid editing a spec that changed under you.

**Response type:** `workflow.mutation.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `confirm_cascade` (boolean, optional) — Accept re-running completed nodes.
- `expect_version` (integer, optional)
- `ops` (array, required) — Mutation ops. See workflow_manifest for the catalog.
- `preview_only` (boolean, optional) — true = compute the cascade and queue NOTHING.
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Preview what editing a pending prompt would re-run:**

```json
{
  "ops": [
    {
      "fields": {
        "prompt": "Be concise."
      },
      "node_id": "produce",
      "op": "update_node"
    }
  ],
  "preview_only": true,
  "run_id": "a1b2c3d4"
}
```

### `workflow_fork`

Branch a NEW run from this one, leaving the original untouched — for exploring an alternative when the first result must be preserved. Works on a finished run. The fork shares the filesystem workspace and any external resources the original created; the response names exactly what is NOT isolated. The child starts as a draft so you can edit it before running it.

**Response type:** `workflow.fork.result`

**Safety:** requires approval

**Parameters:**
- `checkpoint_id` (string, optional) — Fork from this checkpoint instead of current state.
- `note` (string, optional) — Why this branch exists.
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Branch a run to try an alternative:**

```json
{
  "note": "stricter judge",
  "run_id": "a1b2c3d4"
}
```

### `workflow_get_def`

Retrieve one workflow definition in full, including its node tree and declared inputs. Read-only. Credential values are replaced by _has_* presence flags — the definition tells you a key is SET, never what it is.

**Response type:** `workflow.def.detail`

**Safety:** requires approval

**Parameters:**
- `name` (string, required)

**Example — Read one definition:**

```json
{
  "name": "triage-inbox"
}
```

### `workflow_list_defs`

List the available workflow definitions — the user's own plus any bundled template packs. Read-only. Start here when the user asks what workflows exist or which one to run.

**Response type:** `workflow.def.list`

**Safety:** requires approval

**Parameters:**
- `source` (string, optional) — Filter by origin: 'user' or 'bundled'.
- `tag` (string, optional) — Only defs carrying this tag.

**Example — List every workflow definition:**

```json
{}
```

### `workflow_manifest`

The authoring reference, generated from the engine itself: node kinds and their lanes, gate kinds, join and loop modes, binding pipes, mutation ops and outcome states. Read-only. Call this before authoring a spec by hand — it cannot drift from what the engine actually accepts.

**Response type:** `workflow.manifest`

**Safety:** requires approval

**Parameters:**
- _(no parameters)_

**Example — Get the authoring reference:**

```json
{}
```

### `workflow_observe`

Watch a run for a short bounded window and return what changed, with the events from that window. Read-only. Prefer this over repeated workflow_status calls: one call, one wait, a real delta. The window is clamped (100ms-30s) and returns early if the run finishes.

**Response type:** `workflow.run.delta`

**Safety:** requires approval

**Parameters:**
- `duration_ms` (integer, optional) — How long to watch, in ms (default 5000, max 30000).
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Watch a run for three seconds:**

```json
{
  "duration_ms": 3000,
  "run_id": "a1b2c3d4"
}
```

### `workflow_output`

Retrieve one node's structured output from a run. Read-only. Use after workflow_status shows the node is done, to read what it actually produced.

**Response type:** `workflow.node.output`

**Safety:** requires approval

**Parameters:**
- `node_id` (string, required)
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Read a node's output:**

```json
{
  "node_id": "produce",
  "run_id": "a1b2c3d4"
}
```

### `workflow_pause`

Pause a running workflow: in-flight nodes finish, nothing new launches. Resume with workflow_resume.

**Response type:** `workflow.run.paused`

**Safety:** requires approval

**Parameters:**
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Pause a run:**

```json
{
  "run_id": "a1b2c3d4"
}
```

### `workflow_plan`

Turn a natural-language goal into a workflow spec for review BEFORE anything runs. Returns a draft spec plus its validation issues; nothing is saved or started, so the user approves first. Use for 'set up a workflow that…' requests. To save the result, pass it to workflow_author. To turn a conversation you just had into a workflow, pass source_session_id and the plan is mined from that transcript's real tool use.

**Response type:** `workflow.plan.draft`

**Safety:** requires approval

**Parameters:**
- `goal` (string, optional) — What the workflow should accomplish, in plain language. Optional when source_session_id is given — the session's first user turn is then the goal.
- `project_id` (string, optional) — Optional: a project this plan targets. When it binds an existing codebase, the plan is grounded in that project's real layout, README and stack so generated stages assume the right conventions.
- `rigor` (string, optional) — How much structure to propose (default standard).
- `source_session_id` (string, optional) — Optional: mine an existing chat session. The plan then reports the tools that session actually ran and the ones the user DENIED there, so the workflow declares a pre-validated permission set instead of a guessed one.
- `template` (string, optional) — Optional: a template name to base the plan on.

**Example — Draft a plan from a goal:**

```json
{
  "goal": "summarize new issues each morning",
  "rigor": "standard"
}
```

### `workflow_resume`

Answer a workflow that is waiting on a human, or clear a pause. For an approval gate pass answer=true/false; for a choice or form pass the value or object. To change ONE step instead of accepting or rejecting the whole plan, pass answer={"revise": {"step_ref": "<step id>", "comment": "what to change"}} — that step's instruction is amended and the gate re-asks, leaving every other step exactly as it was. With no answer this just lifts a pause. Each answer is consumed once — calling twice will not approve twice. If several gates are pending you must name one with resume_token.

**Response type:** `workflow.gate.resolved`

**Safety:** requires approval

**Parameters:**
- `always_allow` (boolean, optional) — Auto-approve this same operation for the rest of THIS run (cleared if the run is rewound).
- `answer` (any, optional) — true/false for an approval; a value or object otherwise; or {"revise": {"step_ref", "comment"}} to amend one step and re-ask.
- `resume_token` (string, optional) — Which gate to answer (required if several are pending).
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Approve a waiting gate:**

```json
{
  "answer": true,
  "run_id": "a1b2c3d4"
}
```

### `workflow_rewind`

Reset a node AND everything that consumes its output, so they re-run — the in-place fix for 'redo this stage with a better prompt'. Consumers are found through data bindings, not tree position, so a later sibling reading the node's output is reset too. Outputs are archived, not destroyed. If a node in the reset region already fired an external effect, pass redo_effects=true to deliberately fire it again.

**Response type:** `workflow.mutation.result`

**Safety:** requires approval

**Parameters:**
- `force` (boolean, optional) — Re-run even where inputs are unchanged (skips cache).
- `node_id` (string, required)
- `redo_effects` (boolean, optional)
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Re-run a stage and everything reading its output:**

```json
{
  "node_id": "produce",
  "run_id": "a1b2c3d4"
}
```

### `workflow_run_from`

Re-run only what comes AFTER a node, keeping that node's output as-is — 'redo the synthesis with the same gathered data'. Cheaper than rewind when the upstream work was expensive and correct.

**Response type:** `workflow.mutation.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `node_id` (string, required)
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Redo only what follows a node:**

```json
{
  "node_id": "gather",
  "run_id": "a1b2c3d4"
}
```

### `workflow_skip`

Skip one or more pending nodes in a running workflow. A skipped node produces no output and its subtree is skipped with it, so anything binding its output will fail — skip leaves, or rewind and edit instead.

**Response type:** `workflow.mutation.result`

**Safety:** requires approval

**Parameters:**
- `node_ids` (array, required)
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Skip a pending node:**

```json
{
  "node_ids": [
    "optional_review"
  ],
  "run_id": "a1b2c3d4"
}
```

### `workflow_start`

Start a workflow run from a saved definition. mode='background' (default) returns immediately with a run id — poll with workflow_status or watch with workflow_observe. mode='blocking' waits for the run to finish and returns the final state, which suits a short workflow the user is waiting on. Pass idempotency_key when retrying so a retry returns the EXISTING run instead of starting a second one.

**Response type:** `workflow.run.started`

**Safety:** requires approval

**Parameters:**
- `idempotency_key` (string, optional) — Caller-chosen key; a retry with the same key is deduped.
- `inputs` (object, optional) — Values for the definition's declared inputs.
- `mode` (string, optional)
- `name` (string, required) — The definition to instantiate.
- `project_id` (string, optional) — Optional project binding.

**Example — Start a run in the background:**

```json
{
  "inputs": {
    "since": "1h"
  },
  "name": "triage-inbox"
}
```

### `workflow_status`

Current status of a run plus per-node progress and any failure detail. Read-only. For watching a run that is actively moving, workflow_observe is cheaper than calling this in a loop.

**Response type:** `workflow.run.status`

**Safety:** requires approval

**Parameters:**
- `run_id` (string, required) — The run id (from workflow_start).

**Example — Check a run:**

```json
{
  "run_id": "a1b2c3d4"
}
```

## workflows-tools

### `code_map`

Look up where a symbol is defined and which files reference it, or outline one file's imports and definitions — from a pre-built index, in ONE call instead of several grep/read round-trips. Prefer this over grep when you're navigating by symbol or function name. Falls back to reporting no index (use grep/read then); indexes Python, TypeScript, JavaScript, Rust and Go.

**Response type:** `code.map.symbol`

**Parameters:**
- `file` (string, optional) — Outline this file instead: its imports and every definition with line numbers. A workspace-relative or trailing path fragment both work.
- `refresh` (boolean, optional) — Re-index changed files before answering. The index self-updates, so this is only for a tree you just modified outside the session.
- `symbol` (string, optional) — Function, class, method or type name to locate. Returns its definition sites plus the files that reference it.
- `workspace` (string, optional) — Directory to query. Defaults to the active workspace; you rarely need to set this.

**Example — Find where a function is defined and what calls it:**

```json
{
  "symbol": "parse_source"
}
```

**Example — Outline one file's imports and definitions:**

```json
{
  "file": "src/personalclaw/codegraph/parse.py"
}
```

**Example — Re-index a tree changed outside the session, then look up:**

```json
{
  "refresh": true,
  "symbol": "CodeGraphIndex"
}
```

### `code_map_overview`

The codebase's shape: the most-referenced modules and their public surface, with line numbers. Read this once when you're new to a repository instead of exploring file by file.

**Response type:** `code.map.overview`

**Parameters:**
- `workspace` (string, optional) — Directory to summarize (defaults to the active one).

**Example — Get the shape of an unfamiliar codebase before exploring it:**

```json
{}
```
