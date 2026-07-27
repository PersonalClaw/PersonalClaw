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

## personalclaw-core

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
- `workspace` (string, optional) — Workspace name (required when scope='workspace'). Use the workspace name from your session context.

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

## personalclaw-schedule

### `schedule_add`

Add a scheduled cron job. Use when the user says 'every', 'daily', 'weekly', 'remind me', 'check regularly', or 'schedule'. Requires name + message, plus one of: every (seconds), cron_expr, at (unix timestamp), delay (seconds from now), or at_time (human string like '5pm', 'tomorrow 9am', 'in 2 hours').

**Response type:** `schedule.job`

**Safety:** requires approval, risk: caution

**Parameters:**
- `agent` (string, optional) — Agent name for this job (e.g. 'my-code-agent'). Empty or omitted uses the default personalclaw agent.
- `approval_mode` (string, optional) — Tool approval mode for this job. 'auto' auto-approves all tools without prompting. Empty or omitted uses default hook-based approval.
- `at` (number, optional) — Unix timestamp for one-shot job (auto-deletes after)
- `at_time` (string, optional) — Human time string for one-shot job, parsed server-side. Examples: '5pm', '17:00', 'tomorrow 9:30am', 'in 2 hours', '2026-03-28 14:00'. Uses server local timezone. Prefer this over 'at' for absolute times.
- `channel` (string, optional) — Channel ID to post results to (e.g. 'C0AP3QR7Z4M'). If omitted, posts in the originating thread/DM.
- `command` (string, optional) — Zero-token shell command (runs deterministically in the sandbox, no LLM). Mutually exclusive with script.
- `cron_expr` (string, optional) — Standard 5-field cron expression: "min hour dom month dow" where dow: 0=Sun,1=Mon..6=Sat (e.g. "0 9 * * 1-5" for weekdays at 9AM UTC, "30 15 * * 2,4" for Tue/Thu at 3:30PM UTC)
- `delay` (number, optional) — Seconds from now for one-shot job (e.g. 120 for 2 minutes). Converted to 'at' internally. Prefer this over 'at'.
- `every` (integer, optional) — Interval in seconds (min 60)
- `message` (string, required) — Message to send to agent
- `name` (string, required) — Job name
- `persistent_session` (boolean, optional) — Whether this cron reuses one agent session across runs (True, default) or opens a fresh session per run (False). Set False for polling/scanner jobs with no conversational state — avoids unbounded context growth. Set True (or omit) for conversational reminders that should remember prior runs.
- `script` (string, optional) — Zero-token Python script 'file.py:func' under ~/.personalclaw/crons/ (runs deterministically, no LLM). Mutually exclusive with command.
- `silent` (boolean, optional) — When true, suppress automatic message delivery. The agent controls when to notify via send_message.
- `skip_dates` (array, optional) — ISO dates to skip (e.g. ["2026-04-06", "2026-12-25"]). Job silently does not fire on these dates. Evaluated in job's timezone.
- `strict_schedule` (boolean, optional) — When true, fire exactly on schedule with no jitter. Default false — jobs get random delay (0-20min hourly, 0-2h daily) to spread load.
- `thread_ts` (string, optional) — Channel thread timestamp to reply in. Use with channel to post results as a thread reply instead of a new message.
- `timezone` (string, optional) — IANA timezone for skip_dates evaluation (e.g. 'Europe/Luxembourg'). Falls back to global config timezone.
- `zt_timeout` (integer, optional) — Timeout (s) for a zero-token script/command run. 0 = default (30s script / 300s command).

**Example — Schedule a recurring daily message:**

```json
{
  "cron_expr": "0 8 * * *",
  "message": "Summarize my calendar and unread inbox",
  "name": "morning-brief"
}
```

**Example — Schedule a one-off reminder after a delay:**

```json
{
  "delay": 3600,
  "message": "Take a break",
  "name": "stretch"
}
```

### `schedule_list`

List all scheduled cron jobs

**Response type:** `schedule.list`

**Safety:** requires approval

**Parameters:**
- _(no parameters)_

**Example — List all scheduled jobs:**

```json
{}
```

### `schedule_natural`

Schedule a RECURRING job from a plain-English cadence — e.g. 'every weekday at 9am', 'the first of each month', 'every 30 minutes'. The cadence is converted to a validated cron expression and the job is created. For a ONE-OFF time ('in 5 minutes', 'tomorrow 3pm') use schedule_add with delay/at/at_time instead.

**Response type:** `schedule.job`

**Safety:** requires approval, risk: caution

**Parameters:**
- `cadence` (string, required) — Plain-English recurring cadence (e.g. 'every weekday at 9am').
- `channel` (string, optional) — Optional delivery channel id.
- `message` (string, required) — The agent prompt to run on each fire.
- `name` (string, required) — Job name
- `silent` (boolean, optional) — Suppress delivery (run quietly).

**Example — Schedule from a natural-language cadence:**

```json
{
  "cadence": "every weekday at 9am",
  "message": "Post my standup update",
  "name": "standup"
}
```

### `schedule_pause`

Pause a cron job

**Response type:** `schedule.job`

**Safety:** requires approval, risk: caution

**Parameters:**
- `job_id` (string, required) — Job ID

**Example — Pause a job without deleting it:**

```json
{
  "job_id": "morning-brief"
}
```

### `schedule_remove`

Remove a cron job by ID

**Response type:** `schedule.remove.result`

**Safety:** requires approval, risk: destructive

**Parameters:**
- `job_id` (string, required) — Job ID

**Example — Delete a scheduled job:**

```json
{
  "job_id": "morning-brief"
}
```

### `schedule_remove_all`

Remove all cron jobs

**Response type:** `schedule.remove.result`

**Safety:** requires approval, risk: destructive

**Parameters:**
- _(no parameters)_

**Example — Delete every scheduled job:**

```json
{}
```

### `schedule_resume`

Resume a paused cron job

**Response type:** `schedule.job`

**Safety:** requires approval, risk: caution

**Parameters:**
- `job_id` (string, required) — Job ID

**Example — Resume a paused job:**

```json
{
  "job_id": "morning-brief"
}
```

### `schedule_trigger`

Fire a cron job immediately (on-demand), regardless of its schedule. Runs through the live gateway and returns at once; the run appears in execution history.

**Response type:** `schedule.trigger.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `job_id` (string, required) — Job ID to trigger now

**Example — Fire a scheduled job right now:**

```json
{
  "job_id": "morning-brief"
}
```

### `schedule_update`

Update an existing cron job's name, message, schedule, agent, or channel.

**Response type:** `schedule.job`

**Safety:** requires approval, risk: caution

**Parameters:**
- `agent` (string, optional) — New agent name
- `approval_mode` (string, optional) — New tool approval mode
- `channel` (string, optional) — New channel ID
- `command` (string, optional) — Zero-token shell command (runs deterministically in the sandbox, no LLM). Mutually exclusive with script.
- `cron_expr` (string, optional) — New cron expression
- `every` (integer, optional) — New interval in seconds (min 60)
- `job_id` (string, required) — Job ID to update
- `message` (string, optional) — New message
- `name` (string, optional) — New job name
- `script` (string, optional) — Zero-token Python script 'file.py:func' under ~/.personalclaw/crons/ (runs deterministically, no LLM). Mutually exclusive with command.
- `silent` (boolean, optional) — Whether the job runs silently
- `skip_dates` (array, optional) — ISO dates to skip. Replaces existing list.
- `strict_schedule` (boolean, optional) — When true, fire exactly on schedule with no jitter.
- `thread_ts` (string, optional) — New thread timestamp to reply in.
- `timezone` (string, optional) — IANA timezone for skip_dates evaluation.
- `zt_timeout` (integer, optional) — Timeout (s) for a zero-token script/command run. 0 = default (30s script / 300s command).

**Example — Change a job's schedule:**

```json
{
  "cron_expr": "0 9 * * *",
  "job_id": "morning-brief"
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

### `workflow_create`

Author a new workflow SOP — an ordered, reusable playbook for a recurring task. Capture a procedure you've worked out so it can be recalled or auto-surfaced later. Choose the narrowest scope that fits: 'session' (this chat only), 'agent' (this agent), 'workspace' (this project dir), or 'global'. Provide 'match_text' (a natural-language description of when this SOP applies) so it can be matched to future turns.

**Response type:** `workflow.detail`

**Safety:** requires approval, risk: caution

**Parameters:**
- `description` (string, optional) — One-line summary of what this workflow accomplishes.
- `match_text` (string, optional) — Natural-language intent this SOP answers (used for auto-surfacing).
- `name` (string, required) — Lowercase handle, e.g. 'release-checklist' (^[a-z0-9][a-z0-9-]{0,62}$).
- `scope` (string, optional) — Visibility/promotion scope (default: session).
- `steps` (array, required) — Ordered steps. Each: {title, instruction?}.
- `tags` (array, optional) — Optional tags for filtering.

**Example — Create a two-step workflow:**

```json
{
  "name": "weekly-digest",
  "steps": [
    {
      "args": {
        "query": "this week"
      },
      "tool": "knowledge_search"
    }
  ]
}
```

### `workflow_get`

Retrieve one workflow SOP in full — its description and every ordered step (title + instruction). Use this to recall the exact procedure for a known workflow before following it. Read-only.

**Response type:** `workflow.detail`

**Safety:** requires approval

**Parameters:**
- `workflow_id` (string, required) — The workflow id or name (from workflow_list).

**Example — Read a workflow definition:**

```json
{
  "workflow_id": "weekly-digest"
}
```

### `workflow_list`

List the workflow SOPs (standard operating procedures) available to you — defined, ordered playbooks the user maintains for recurring tasks. Matching SOPs are also auto-injected as guidance when the turn matches one; use this to see the full catalog, confirm a workflow exists, or recall its steps on demand. Read-only. Filter by 'scope' (global/workspace/agent/session) or 'tag'.

**Response type:** `workflow.list`

**Safety:** requires approval

**Parameters:**
- `scope` (string, optional) — Only list workflows in this scope
- `tag` (string, optional) — Only list workflows carrying this tag

**Example — List saved workflows:**

```json
{
  "scope": "global"
}
```

### `workflow_promote`

Widen a workflow's visibility once it has proven useful. Scope only moves UP the ladder: session → agent → workspace → global. Use this to graduate an SOP you first captured for one chat so it applies to this agent, this project, or everywhere. Promoting to 'workspace' needs a scope_ref (the project dir); 'global' clears it.

**Response type:** `workflow.detail`

**Safety:** requires approval

**Parameters:**
- `scope` (string, required) — The wider target scope (must be above the current one).
- `scope_ref` (string, optional) — Required for 'workspace' (the project dir). Omit for 'global'; reused for 'agent'.
- `workflow_id` (string, required) — The workflow id (from workflow_list) to promote.

**Example — Promote a session workflow to global scope:**

```json
{
  "scope": "global",
  "workflow_id": "weekly-digest"
}
```

### `workflow_run`

Load a workflow SOP as the procedure to follow for the current task. Workflows are guidance, not executable code: this returns the ordered steps as an actionable checklist for you to carry out with your other tools, in order. Use when a defined playbook covers what the user asked for.

**Response type:** `workflow.run.result`

**Safety:** requires approval, risk: caution

**Parameters:**
- `workflow_id` (string, required) — The workflow id or name (from workflow_list) to follow.

**Example — Run a saved workflow:**

```json
{
  "workflow_id": "weekly-digest"
}
```
