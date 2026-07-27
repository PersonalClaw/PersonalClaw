# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

> **Note (0.x clean break):** model bindings in `active_models.json` now carry
> ordered fallback-chain semantics. Old stores read cleanly (a single binding is a
> one-entry chain); consider `personalclaw snapshot` before upgrading, per the
> pre-1.0 banner.
>
> **Note (0.x clean break):** true rewind adds a `rewound` field to persisted chat
> messages (the retained discarded tail). Old sessions read cleanly (missing field =
> today's behavior — no migration); consider `personalclaw snapshot` before upgrading.

### Added

- **Artifacts: collections + save-time dedup.** Saved artifacts can now carry a
  **collection** label (a free-form grouping for the coming library), settable at
  save time and reassignable later, and filterable via `GET /api/artifacts?collection=`
  and the `artifact_list` tool. And saving no longer silently mints duplicates: a
  fresh `artifact_save` (or `POST /api/artifacts`) whose name matches an existing
  artifact now **refuses with a hint** — the tool tells the agent to update the
  existing slug or pass `force`, and the REST route returns `409 similar_artifact_exists`
  with the existing slug (bypass with `?force=1`). File-backed saves keep their
  existing source-path dedup. Pre-existing artifacts load unchanged (tolerant read).
- **Agent routing: suggest the right specialist, never route silently.** Give an
  installed agent a **Specialty** and comma-separated **Routing hints** (in the agent
  editor), and when a message in a default-agent chat clearly fits it, a quiet
  "route to `<agent>`?" chip appears above the composer. One click re-targets the
  session (via the existing agent-switch path); the ✕ dismisses it and suppresses
  that agent for a cooldown (three dismissals mute it until you re-enable). It is a
  **proposal** — nothing changes until you click — and classification is
  deterministic-first (keyword-phrase overlap, then embedding cosine when an
  embedding model is bound), with the LLM never in the hot path. Silent auto-routing
  is deliberately out of scope. Route/dismiss also feed the routing pair's accuracy
  into Settings → AI feedback. Tune it in Settings → Chat → Agent routing
  (`agents_routing.*`); zero behavior change until you author routing metadata.
- **Chat craft: seven chat-surface mechanics.** The chat surface gains the pieces
  the sibling platforms proved out. **True rewind** — edit ANY past user message and
  replay from there; the discarded answers are kept in this chat's history (viewable
  under a "rewound from here" divider, restorable as a fork) and the provider context
  rebuilds from the truncated transcript, so the agent never references the undone
  turns. **Queue with manners** — each queued message now has an "Interrupt now" that
  gracefully stops the running turn and runs that message next. **Find in
  conversation** — Cmd/Ctrl+F opens an in-chat find bar (count, next/prev, jump-to-
  match) that highlights every occurrence without ever re-rendering the markdown.
  **Quote toolbar** — selecting transcript text floats a Quote + Copy toolbar; Quote
  inserts an attributed blockquote (who said it) into the composer, now from keyboard
  and touch selections too. **Follow-up chips** — after each reply, 2-3 suggested next
  messages appear via one cheap background call (never blocks the turn; skipped for
  temporary/incognito chats and silent when no model is bound; toggle in Settings →
  Chat). **Smoother streaming** — the reveal snaps to word boundaries so text lands in
  whole words, with a new Settings → Chat "Streaming text reveal" (smooth | immediate)
  control.
- **Background compression keeps long chats fast.** Old, idle conversation history
  is now topic-segmented and compressed in the background on the maintenance
  cadence — the always-on complement to on-demand tool-output projection. A
  transcript untouched for a week (default) is split into topics (by embedding drift
  when an embedding model is bound; a deterministic turn-count fallback otherwise),
  then compressed by attention: the most-recent topic stays verbatim, middle topics
  reduce to their request/response pairs, and the oldest tier is summarized by a
  cheap background model. It only ever touches sessions **at rest** (never a live
  turn), incognito/temporary chats are skipped entirely, every dropped span is
  archived first (fully recoverable) and any tool-result recovery handle is
  preserved, and savings land in the TokenJuice ledger under `bg_topic`. Toggle and
  idle window live in Settings → Chat config (`tools.bg_compress_enabled` /
  `tools.bg_compress_idle_days`); disabling it stops the pass within one tick.
- **Feedback that actually teaches: 👍/👎 on AI judgments.** Inbox classifications,
  drafted replies, digests, and loop findings now carry a quiet thumbs pair. 👍 is
  silent-positive ("Mark accurate" — it only feeds the accuracy denominator); 👎
  optionally takes a one-line "why". Every verdict is attributed to the source that
  produced the judgment — the bound prompt, the loop judge, a workflow's surfacing —
  and per-source rolling accuracy lives in Settings → AI feedback (honest counts,
  shown only after enough verdicts). A source that keeps being wrong **stops
  surfacing** and raises a one-time "retire this rule?" notification with a deep
  link; snooze or clear it after an edit. Everything is deterministic counting —
  no model calls, and feedback never leaves the instance. Apps record feedback on
  their own judgments via `personalclaw.sdk.feedback` / `POST /api/feedback`
  (namespaced server-side, so an app can never impersonate a core source).
- **Investigate anywhere: chat about any entity with its context pre-loaded.** Inbox
  items and loop findings (more surfaces to follow) gain an "Investigate in chat"
  button that opens a fresh chat carrying the entity's full context — composed
  server-side from the owning store, injected as fenced untrusted data on your
  first message (never pasted into your visible text), with the composer pre-filled
  with an editable opening question. The session opens in read-only **Ask** mode —
  investigating never mutates the entity; you escalate the mode yourself. A header
  chip deep-links back to the source. Apps get the same primitive via
  `useInvestigate` in the app SDK.
- **Model use-cases v2: routing sub-categories + fallback chains.** Chat work is
  now routable by kind — `background` (titles, tags, suggestions, digests,
  consolidation), `orchestration` (supervising turns and model-less subagents),
  `loops` (goal-loop workers and judges), alongside the existing `code_tools` and
  `reasoning` — each bindable in Settings → Models under a new **Chat routing**
  group, falling back to your Chat chain when unbound. Bind a cheap or local model
  to `background` and housekeeping chores stop burning your flagship chat model.
- **Every model binding is an ordered fallback chain.** The first model is the
  default; later entries take over when an earlier provider's circuit breaker is
  open or a call fails (background calls advance mid-batch; a failed chain surfaces
  one clear error). The Models panel gains a chain editor with reordering and
  per-entry provider-health dots; the composer's model pick sits above the chain —
  if the picked model fails, the chain takes over.
- **Type-routed tool-output compressors.** Large tool results now project smarter: a
  JSON array of thousands of items becomes a per-field schema (names, types, ranges,
  null counts) plus the first/last item verbatim; a large code file becomes a
  signatures-and-docstrings outline with a line map (`code` is a new content type,
  sniffed conservatively). The full raw always stays one `tool_result_get` away.
- **Projection rules: three layers + line operations.** A builtin rule pack now
  recognises common command output (git, pytest, npm, docker, cargo…) so e.g. a
  `git diff` run through the shell projects as a diff; a repo can ship its own
  `.personalclaw/projection_rules.json` (project layer, beats user rules); and every
  rule may carry declarative line operations — head/tail window, keep/skip filters,
  and a fold-repeats counter — editable in Settings → Tool output.
- **Background prose summarizer.** Long natural-language output on background paths
  can be model-summarized with a guaranteed deterministic fallback (never wired into
  the synchronous tool path).

Forward-looking work is tracked in [docs/roadmap/](docs/roadmap/roadmap.md).

## [0.1.2] — 2026-07-26

The **safety-and-resilience** release: the autonomy guardrails program (kill switch,
spend budgets, denylist, outbound scanning, named safety profiles), the full Platform
Resilience program (Doctor health probes, no-model degraded mode, mid-turn message
policy, confirm-gated fixes + trust simulators + crash capture, and a health-scored
self-maintenance engine), first-party apps in the Store on a plain install, the
legibility surfaces (self-documenting UI kit, Discover, routed project context, offline
agent reference), and a render-smoke gate that closes the v0.1.0 blank-dashboard hole.

### Added

- **One health-scored maintenance engine replaces scattered upkeep.** PersonalClaw now
  computes a **health score** (100 − measured deficits: knowledge items missing an
  embedding, orphaned stale locks, skills due for aging — each capped, and an *unfixable*
  deficit like "no embedder bound" is excluded rather than held against a score you can't
  improve) and runs a **dependency-ordered remediation plan** to raise it: re-index,
  orphan-prune, skill-age, stopping when the target score is reached, the per-run dollar
  cap is spent, or the plan is exhausted. It runs itself on an **adaptive heartbeat cadence**
  (further apart when healthy, sooner when degraded) and is visible + runnable on demand
  from Settings → Doctor → Maintenance, with a run ledger. Deterministic jobs are free;
  model-touching jobs (future) charge the guardrails spend meter. Every run is idempotent
  (per-job cooldowns) and the whole engine is one toggle — disabling it falls back to the
  legacy per-tick heartbeat maintenance. This is the final slice of the **Platform
  Resilience** program (Doctor · degraded mode · mid-turn · fixes/simulators/crash-capture ·
  this engine).
- **The Doctor can now fix what it finds, explain what it surfaces, and remember what
  crashed.** Three additions to the health surface (all Settings → Doctor):
  **confirm-gated fixes** — a finding that has a repair (a `static/dist` copy shadowing
  the runtime symlink, stale locks/rollback leftovers, model bindings pointing at removed
  providers) shows a **Fix** button with a read-only preview; nothing auto-applies, a
  two-step confirm runs it, and every application is security-audited and touches harness
  mechanics only (never your content). A **per-provider selftest** fires a tiny real
  inference per capability (a one-token chat / short embed) for true ground-truth instead
  of a reachability guess. A **surfacing simulator** dry-runs the skill scorer in explain
  mode — type a query and see, per candidate, the keyword/semantic scores, the thresholds,
  and exactly why each skill was included or excluded (zero model calls). And **structured
  crash capture**: an unhandled failure at a turn/loop/gateway boundary now writes one
  redacted, recoverable artifact under `~/.personalclaw/crashes/` (capped, never uploaded)
  that the Doctor surfaces as a card — a mid-stream death leaves a record instead of a lost
  stack trace.
- **Mid-turn message policy: queue (default) or cancel-and-replace.** A follow-up sent
  while a turn is still generating now follows a *declared* policy. The default,
  **`queue`**, is today's behavior formalized — the message is delivered next turn. Opt
  into **`cancel_and_replace`** (a platform default in Settings, overridable per channel)
  and a rapid follow-up instead cancels the in-flight answer and starts fresh with the new
  message — no stale ghost response, no wasted compute. A per-session debounce coalesces a
  burst of messages into ONE cancel + the last message. The guard is strict: only
  **interactive** turns (the web chat, a channel DM) are ever cancel-and-replaced —
  unattended work (goal loops, cron, subagents, the heartbeat) always queues, so a user
  message can never pull the rug out from under a background job. Built on the existing
  soft-cancel verb and turn-end queue drain (no new dispatch path); a new
  `resilience/active_jobs.py` tracks each turn's origin as the bookkeeping behind the
  decision.
- **No-model degraded mode: the assistant stays useful, and honest, with no model bound.**
  Every model-dependent surface now declares its **LLM-free floor** explicitly, so an
  offline laptop (dead ollama, wiped cache, no API key) degrades by design instead of
  error-walling: search drops from hybrid to keyword (FTS) + graph + recency ranking;
  the inbox keeps raising keyword/name-mention alerts (only auto-classify/draft/digest
  pause); knowledge still captures documents (only entity/insight extraction is skipped,
  marking the item partial); memory keeps its deterministic preference-facet capture;
  speech features turn visibly off rather than erroring; and chat says so plainly rather
  than faking a reply. A compact **degraded chip** appears in the shell (with a popover
  listing each degraded surface, its floor, and any pending-enrichment backlog) whenever a
  surface is running on its floor, and a notification fires on the transition down
  (`warning`) and on recovery (`info`). A lint test asserts every non-interactive
  model-call site maps to a registered contract, so a future surface can't ship without
  declaring its floor. New `GET /api/resilience/degraded`; two guard-class config switches
  (`resilience.doctor_enabled`, `resilience.degraded_indicator` — a missing/unknown value
  keeps the surface visible).
- **A Doctor tab now diagnoses every subsystem from one read-only view.** Settings →
  Doctor runs **tiered health probes** — process → socket → cheap-RPC → per-capability
  — across memory (db + faiss consistency), channels, local models (availability +
  phantom bindings), app backends (+ interrupted-update leftovers), the SPA
  `static/dist` symlink (the stale-SPA bug-class), and model-provider breakers
  (composed from the guardrails audit). The core doctrine is enforced: **a degraded
  capability never marks the gateway down and never suggests a restart** — only a
  core-tier failure does. Every probe is read-only and fail-safe (an exception
  becomes a failed row, never a 500), and secrets are redacted from probe output. New
  endpoints `GET /api/doctor` (all capabilities, cached 30s) and
  `GET /api/doctor/{capability}` (re-run one card); the dashboard System Health strip
  gains a one-line rollup that appears only when something needs attention and links
  to the tab. Confirm-gated auto-fixes and the trust/debug simulators land in later
  Platform-Resilience sessions.
- **First-party apps now appear in the Store on a plain install.** The published
  first-party apps repository (`github.com/PersonalClaw/PersonalClawApps`) ships as
  a default Store source, so a bare `pip install personalclaw` surfaces every
  first-party app — model providers (OpenAI, Anthropic, Bedrock, Ollama, …),
  search, speech, channels — without the dev workspace tree. They appear
  **uninstalled**: nothing runs until you click Install, so the per-app
  install-consent + provider-agnostic-core contracts are unchanged. The source is a
  built-in default (not user-removable); the dev filesystem source and the
  `PERSONALCLAW_FIRST_PARTY_APPS_DIR` override still work for offline/local-clone
  development. The Store's catalog scan is cached (5-minute TTL) and runs off the
  event loop, so the first open clones once in the background.
- **Every non-interactive model call now passes through one guarded seam.**
  Background LLM calls (the `reasoning` axis behind `one_shot_completion`, the
  goal-loop judges, the loop gates, web-extract) are now wrapped in a
  **model-call guard** — the LLM twin of the network egress chokepoint. It adds a
  **per-provider circuit breaker** (opens after N consecutive failures, half-opens
  after a recovery window): during a provider outage an overnight run fails in
  microseconds instead of stacking timeouts. It adds a **hard wall-clock timeout**
  on every call, and an **attempt-level JSONL audit trail**
  (`~/.personalclaw/model_calls.jsonl`, one line per attempt, trimmed to the most
  recent entries) recording provider, model, latency, tokens, and outcome. The
  **interactive chat stream is deliberately untouched** — a human is watching it.
  `one_shot_completion` also gains a typed **`output_type`** option: pass `dict`
  or `list` to require a parseable JSON shape, and a parse miss is retried once
  with a targeted correction note before raising a loud `OutputContractError` —
  replacing the silent `None` degrade that `parse_llm_json` returned at every
  call site (migrated: web-extract, inbox classify). Goal-loop and eval judge
  verdicts gain a bounded **`reasoning`** field written before the verdict, so a
  structured-output constraint no longer suppresses the judge's chain of thought.
  A new graded provider capability descriptor (`structured_output`:
  `none`/`json_mode`/`json_schema`) lets provider apps opt into native
  schema enforcement in a later change; until then every provider gets the
  universal parse-with-retry path. This is a **clean break** (pre-1.0): the new
  audit trail is additive on-disk state under `~/.personalclaw/` — **run
  `personalclaw snapshot` before upgrading** if you want a rollback point.
  (AUTONOMY-GUARDRAILS §2, Session 1.)
- **Unattended spend now has budgets, and outbound prompts are scanned for
  secrets.** A new **Guardrails** settings section (`config.json` → `guardrails`)
  adds daily spend ceilings for unattended work: set a **max tokens/day** or
  **max dollars/day** and when the day's automated spend hits the ceiling, further
  unattended LLM calls are refused (a cron agent fire is skipped with a one-time
  "daily automation budget reached" notification, a subagent spawn is refused) —
  interactive chat is never budget-gated. Spend is metered at the model-call seam
  into `~/.personalclaw/spend.json` (per-day, pruned after 30 days), with dollars
  estimated from the existing per-model price table (provider-reported cost
  preferred). Every outbound prompt bound for a **remote** provider is scanned for
  secrets/PII (AWS keys, private keys, Slack tokens, emails, phone numbers) and
  handled per a configurable **scan mode**: `warn` (log + send), `redact`
  (substitute + send, the default), or `block` (refuse the call); a local provider
  is always `warn` since its content never leaves the machine. The circuit-breaker
  thresholds from Session 1 are now configurable here too (failure threshold,
  recovery seconds). Defaults are **unlimited budget + redact**, so an existing
  install's behavior is unchanged until you set a ceiling. Clean break (pre-1.0):
  additive on-disk state (`spend.json`) — **run `personalclaw snapshot` before
  upgrading** for a rollback point. (AUTONOMY-GUARDRAILS §1.1, §2.2, Session 2.)
- **A kill switch, a path/action denylist, and a live-write guard for unattended
  work.** Three safety-floor controls land. (1) **`personalclaw incident on`** (and
  `POST /api/incident`) suspends every unattended fire — cron, hooks, event
  triggers, subagent spawns — within one poll interval; **interactive chat keeps
  working**, and resuming requires an explicit `personalclaw incident off` (or
  `POST /api/incident/resume {confirm:true}`). Activation/resume are tamper-evidently
  logged. (2) A **path/action denylist** (`security.autonomy_denylist`, rules of
  `{paths, actions, verdict: block|needs_human}`) is enforced at all three
  action-dispatch seams (script hooks, scheduled jobs, memory-event triggers), so
  an app-contributed action provider inherits it without cooperating; it composes
  with the always-on built-in sensitive-path + destructive-command denylists. A
  `needs_human` rule holds the action and raises a needs-input notification instead
  of dropping it. (3) **`PERSONALCLAW_DISABLE_LIVE_WRITES=1`** makes live,
  hard-to-reverse writes (deleting a downloaded model, a non-GET request to a
  non-loopback host) refuse with a loud typed error instead of executing — and it
  is auto-set for the whole test suite, structurally closing the bug class where a
  destructive test once deleted a real bound model. Guard flags parse fail-safe
  (a missing/typo'd value keeps the guard ON), and the outbound scan now defaults
  to `redact` (never the leaky `warn`), enforced by a schema test. Clean break
  (pre-1.0): additive config + an `incident.json` flag file — **run `personalclaw
  snapshot` before upgrading**. (AUTONOMY-GUARDRAILS §1.2–§1.4, §5, Session 3.)
- **A Guardrails settings surface, a provider-health view, and named safety
  profiles.** The safety floor gets its cockpit and its posture layer. A new
  **Settings → Guardrails** panel gathers the incident kill switch (with a
  one-click toggle), the daily spend budgets, the outbound scan mode, and the
  circuit-breaker tuning — and a **provider-health view** derived from the
  model-call audit (per-provider breaker state, pass rate, p50/p90/p99 latency,
  recent failure modes; `GET /api/models/health`, computed from files already on
  disk — no telemetry). A **persistent incident banner** now shows on every page
  while incident mode is active, with inline Resume. Under the hood, **named safety
  profiles** (`interactive` / `coding` / `review-only` / `cleanup` / `incident` /
  `headless`) become the single object that decides approval + tool grants + egress
  tier + budget + scan for a run; unattended runs (cron, subagents, channel, inbox,
  loop workers) resolve to the read-only **`headless`** profile *by construction*
  from their session key, and a curated **package-registry egress tier** lets a
  sandboxed run reach pypi/npm/crates/GitHub/… without opening the whole internet.
  Defaults preserve today's behavior. Clean break (pre-1.0), additive. (AUTONOMY-
  GUARDRAILS §2.5, §3, §4.2, §4.4, Session 4.)
- **The animated dot-wave backdrop is now a choosable background style.** A new
  **Background** control in Settings → Design → Backdrop & motion switches the
  surface behind chat, the new-chat composer, and onboarding between four modes:
  `waves` (the animated breathing dot-wave surface, default), `still` (the same
  dot field frozen — the lattice without the motion), `glow` (only the soft light
  hugging the composer, no dots), and `none` (a plain, empty canvas). The choice
  persists in your appearance settings and applies live with no reload. Motion
  modes still honor `prefers-reduced-motion` (they render one static frame).
- **PersonalClaw describes its own UI kit, guides you to the parts of itself you
  haven't tried, and hands external agents a routed project context.** Three
  legibility surfaces land together. (1) The `ui/` component kit is now
  self-documenting: each primitive ships a `.doc.ts` object (purpose, props,
  best-practice tenet) compiled into `ui-docs.json` at build time, and two agent
  tools — `ui_search(query)` for a budgeted brief and `ui_get(name)` for
  machine-readable props — let an app-building agent find the right primitive
  instead of hand-rolling chrome; a drift test fails the build if a primitive ships
  without its doc. (2) A **Discover** surface guides you through the parts of
  PersonalClaw you haven't tried yet — a hand-authored catalog of user-facing areas
  (Chat, goal loops, automation, Tasks, Projects, Inbox, Knowledge, Memory, Skills,
  Apps), each a one- or two-sentence lesson with a deep link into the page that owns
  it. It is deliberately NOT tool-derived: the tool surface is an implementation
  detail you're never meant to drive by hand. The dashboard shows a rotating
  spotlight of the first few; a dedicated **Discover hub** (`#/discover`, also in the
  command palette) lists every tip grouped by area. A tip leaves the feed two ways,
  both hide-only: an explicit dismiss that persists forever, and an auto-hide once
  you've actually used that area (detected from state that already exists — a chat on
  disk, a knowledge item, a scheduled job…). It only points and hides, never enables
  anything, and the whole surface is behind the `legibility.discover_tips` config
  flag. (3) PersonalClaw
  can act as a **routed-context provider** for external coding agents: per project it
  assembles a tiered manifest — hard rules/brief at the top, scored memories + skills
  + knowledge *pointers* in the middle, and an L0 catalog of what was NOT loaded (with
  the tool to pull each) at the bottom — exposed as the in-process `get_context` MCP
  tool and, opt-in per project (`legibility.context_adapters`, default off), rendered
  into the project's `CLAUDE.md` / `AGENTS.md` / `.cursorrules` inside a
  `<!-- PCLAW:START -->` fence that regenerates in place and never touches your own
  content outside the markers. Memory-derived and knowledge-derived content stay under
  distinct headings, and knowledge items render as titled pointers — never inlined
  bodies. A "Refresh context files" action on the project page (re)writes the block.
- **Apps surface their skills and backend routes to the agent (declared, not
  discovered).** An app now declares two legible surfaces in `app.json`, both
  readable without executing app code. `skills[]` names SKILL.md directories the
  app ships and OWNS: on enable they seed into the user skills tree **through the
  supply-chain chokepoint** (quarantine → scan at the app's trust tier →
  `.pclaw-lock.json` provenance) — an app skill never bypasses the gate just
  because it arrived inside an app — and are removed provenance-keyed on disable,
  never touching a user's own or another app's skill. `backend.routes[]` names the
  app's agent-callable HTTP surface (`op`, method, path, summary, param/body
  hints); one generic tool provider turns every enabled app's `agentCallable`
  routes into `app_<name>_<op>` tools (risk keyed off the verb) and drives them
  through the existing loopback reverse proxy, and a `call-app-route` action lets
  hooks/crons fire the same routes — both share one resolver so the callable gate
  can't diverge. The routes also render into `GET /api/manifest`'s `app_surfaces[]`
  (a non-callable route documents the surface with `tool: null`), and a declared
  route whose backend answers 404 raises a one-shot drift notification so a
  dead-declared route is caught the moment it's called. First-party Growth (17
  routes) and Minutes (24 routes) ship their route tables.
- **Offline agent reference + `pclaw-api` skill** — an agent driving PersonalClaw
  from outside a running gateway now reads exact tool/route signatures instead of
  guessing them. The distribution ships a generated markdown reference
  (`personalclaw/reference/`: every registered tool with its input schema +
  examples, the agent-callable HTTP routes, and the provider taxonomy) rendered
  from the same source as the live `GET /api/manifest`, plus a bundled `pclaw-api`
  operator skill (the never-guess-copy-it + verify-after-mutate discipline). Locate
  the files from the installed binary with the new `personalclaw doctor --paths`,
  which prints the resolved reference / config / skills / install directories. A
  drift test byte-compares the checked-in reference against a fresh render, so a
  tool or route added without its metadata reddens the build.
- **Render-smoke gate** (`npm run smoke:render`): the built SPA is now loaded
  in headless Chromium — key routes must mount real content with no uncaught
  errors — before any frontend-affecting push (repository-owned pre-push hook,
  `npm run hooks:install`) and on every PR (CI `web` job). Closes the
  verification hole behind the v0.1.0 blank dashboard, where typecheck, unit
  tests, and the production build all passed without ever rendering the
  artifact in a browser.

### Changed

- **The dashboard's system indicators are now a docked bottom rail.** The System
  strip (uptime, version, CPU/memory/network/disk/load, triggers, subagents, and
  the update action) was the last item in the scrolling column; it's now a
  shell-like rail pinned to the bottom edge, so the live indicators stay visible
  while the rest of the dashboard scrolls. The dashboard header's at-a-glance
  pulse strip sheds two now-redundant indicators: the gateway connectivity pill
  ("Live/Offline") and the gateway-version pill. The app shell's top-right corner
  already carries a live connectivity dot on every page, and its expanded system
  card now shows the gateway version (sourced from `/api/system`) — so the header
  strip is just the live count pills. The rail itself is width-responsive (a CSS
  container query, keyed to the content-width preset + sidebar, not the viewport):
  it sheds the decorative CPU sparkline and the metric word-labels — icon + value
  keep carrying the reading, with the full text on hover — to stay on one line as
  the available width tightens, and the "Details →" action stays anchored to the
  right edge.

## [0.1.1] — 2026-07-22

### Fixed

- **Blank dashboard in v0.1.0 (critical).** The released SPA crashed at first
  render with `TypeError: Cannot read properties of null (reading 'useContext')`
  — a dependency-group bump had split the installed tree across React 18 and
  React-DOM 19 (the classic dual-React invalid-hook failure), so every install
  kind (pip/uv, container, git) served an empty page. The web toolchain is
  reverted to its known-good React-18 set, a root npm `overrides` pins
  `@types/react`/`@types/react-dom` so transitive packages cannot drag React-19
  types back in, and the lockfile is regenerated from a clean install so the
  declared and resolved trees agree.
- **`monaco-editor` was never declared as a dependency** — it is a peer of
  `@monaco-editor/react` and imported directly, but resolved only by lockfile
  accident; a clean reinstall broke the build. Now a direct dependency
  (`^0.55.1`, the version v0.1.0 shipped transitively).

## [0.1.0] — 2026-07-19

### Added

- **App-contributed CLI seams** — an app can now hook into `personalclaw setup` and
  `personalclaw doctor` via manifest `cli.setup` / `cli.doctor` (`module:function`),
  and declare its log namespaces via `loggerRoots`. `personalclaw setup --app <name>`
  runs just one app's setup step. Core names no channel vendor in its CLI.
- **CI & release engineering** — GitHub Actions for both repos: `ci.yml`
  (lint/test/web/rails, ≤10-min budget) and `full.yml` (3.12/3.13 × ubuntu/macos
  matrix, audit, coverage) on core; manifest-validate/tests/boundary on the apps repo.
  A tag-triggered `release.yml` builds the wheel (with the prebuilt SPA) + multi-arch
  GHCR images, publishes to PyPI via Trusted Publishing behind an owner-approval gate,
  and attaches an SBOM + build-provenance attestations. `uv.lock` pins the dependency
  graph (CI installs `--locked`); Dependabot watches pip/npm/actions weekly. See the
  [supply-chain posture](README.md#supply-chain).

### Changed

- **Provider-boundary completion (Slack residue retired from core):** the Slack
  channel app now ships its own token/slash-command setup and doctor probe (via the
  new `cli.setup`/`cli.doctor` seams) instead of living hardcoded in core's CLI; app
  logger roots are derived from installed manifests (`constants.APP_LOGGER_ROOTS`
  removed); `slack-sdk` is no longer a core runtime dependency (kept as the `[slack]`
  extra, and the slack-channel app declares it via manifest `pythonDependencies`, which
  the app-install pipeline installs). A residue-sweep test + a machine-checked keeps
  table (`docs/architecture/provider-boundary-keeps.txt`) prevent vendor residue from
  regrowing in core.
- **LLM SDKs demoted out of core dependencies (`openai`, `anthropic`):** a bare
  `pip install personalclaw` no longer pulls the OpenAI or Anthropic SDKs. They now
  ship via (a) the `[openai]` / `[anthropic]` packaging extras for pip/uv users, and
  (b) the branded provider apps' manifest `dependencies.pythonDependencies`, which the
  app-install pipeline installs into the shared venv (plan 32 T2.1). The provider
  adapters import their SDK lazily and now raise a clear `MissingSDKError` naming the
  exact `pip install 'personalclaw[openai]'` remedy (and `personalclaw doctor`) when a
  hosted provider is used without its SDK. This trims the default install; users who
  install a provider app or the matching extra are unaffected (plan 34 T1.4).
- **Self-update is now install-kind aware (git · pip · container · desktop):** the
  in-app updater (Settings → Updates) and the update check no longer assume a git
  checkout. The availability signal is the **latest GitHub release tag** (ETag-cached,
  offline-tolerant) compared against the running version — tags are the release truth
  for every install path. Apply adapts to the install kind: a **git** checkout runs the
  existing pull → reinstall → rebuild → restart pipeline (with a new *Developer update
  mode* toggle, `dashboard.update_dev_mode`, to track every commit instead of only
  tagged releases); a **pip/uv/pipx** install runs `pip install -U personalclaw==<tag>`
  into its own interpreter and gracefully re-execs (no web build — the wheel ships the
  dashboard); a **container** install shows the exact `docker compose … pull && up -d`
  commands (no in-place apply); a **desktop** install delegates to the app shell. The
  Updates panel renders the right affordance per kind, and git installs also surface
  commits-behind as secondary info.

  This is a **clean break** (pre-1.0): the old git-only updater is replaced directly,
  not gated — LIFECYCLE-DOCTRINE's gate machinery is deferred, so there is no
  `update_kind_aware` gate to flip (owner decision 2026-07-20). Behavior change: a git
  checkout now updates on new *release tags* by default instead of every commit — flip
  *Developer update mode* on to restore per-commit updates. **Run `personalclaw
  snapshot` before updating.** (plan 34 S4.)

### Removed

- **`personalclaw gateway --slack-only`** — the legacy alias for `--headless` is
  removed. Use `--headless`.

### Fixed

- **Release wheel now bundles the SPA when built via `python -m build`.** The release
  pipeline (and `make build`) build the sdist first, then build the wheel from that
  sdist; the built `web/dist` was not included in the sdist, so the wheel-from-sdist
  shipped without the dashboard and failed `scripts/verify_wheel.py`. A new
  `MANIFEST.in` grafts `web/dist` into the sdist, which also makes the sdist itself
  self-contained (a wheel built from the PyPI sdist serves the dashboard too). Guarded
  by `tests/test_sdist_bundles_spa.py`. (plan 34; caught in the release dry-run.)


Initial public release — the first end-to-end PersonalClaw: a self-hosted, local-first,
provider-agnostic personal AI agent behind one gateway and one web dashboard.

### Added

- **Agentic chat** — multi-session chat with tool use and approval controls, session
  forking/undo, answer variants/regenerate, folders/tags/kanban, side conversations,
  per-session model and reasoning-effort overrides, and temporary/incognito memory modes.
- **Goal loops** — give the agent a target; it classifies, plans, and loops autonomously
  under a deterministic supervisor you can pause, nudge, or stop.
- **Memory** — layered semantic/episodic/procedural memory with active recall, after-turn
  learning from corrections, promotion of repeated facts, and an Obsidian-compatible vault.
- **Knowledge base** — document/media/web ingestion, AI enrichment, entity extraction, a
  knowledge graph, and semantic search wired into chat context.
- **Skills** — SKILL.md procedures with a marketplace, supply-chain scanning on install,
  session-scoped ephemeral skills, and an approval inbox for agent-proposed skills.
- **Automation** — cron/interval/webhook triggers, background subagents, a channel-watching
  inbox with drafted replies, and workflow SOPs surfaced on match.
- **App platform** — a permission-gated, scanner-gated Store: model providers, search,
  speech (STT/TTS), local models, channel connectors, agents, and full backend+UI apps,
  each installed through a quarantine → scan → consent lifecycle with subprocess isolation.
- **Agent runtimes** — the built-in native loop plus external CLI agents over ACP
  (Agent Client Protocol) as pluggable runtimes.
- **Model layer** — per-use-case model bindings (chat, background, embedding, ingestion,
  speech) over 16 provider apps; nothing is hardwired to a vendor.
- **Security** — four auth modes (loopback-forced `none`), command screening (denylist +
  suspicious-pattern watchers), an OS child sandbox, one egress chokepoint with host
  policy, untrusted-content fencing, a non-overridable "dangerous" install verdict, an
  HMAC-chained tamper-evident security event log, and credential-excluding exports.
- **Delivery surfaces** — local gateway, Docker Compose, systemd/launchd service install,
  a desktop shell, and portable snapshot/restore.

### Notes

- Single-user, self-hosted, MIT-licensed. **Zero telemetry** — no usage data leaves your
  machine.
- Requires Python 3.12+; a model-provider API key (or a local Ollama) to start chatting.

[Unreleased]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/PersonalClaw/PersonalClaw/releases/tag/v0.1.0
