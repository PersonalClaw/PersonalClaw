# Plan: Session Management — Organize, Find, and Curate Conversations at Scale

**Status:** DONE (except T2.1) — Session 1 (FTS5 cross-session search + match snippets) shipped
2026-07-28; Session 2 T2.2/T2.3 (bulk ops + lifecycle + auto-archive) shipped 2026-07-29; Session 3
(retention surface, starters, redacted export) shipped 2026-07-30, which also fixed an S2 bug that
made the auto-archive rule inert for non-resident sessions.
🔴 **T2.1 (suggested organization) is NO LONGER BLOCKED (verified 2026-08-04).** Its stated blocker was
that `emit_attention_item(kind="proposal")` did not exist and `InboxItem` had no `kind` field;
INBOX-NOTIFICATIONS-UNIFICATION completed S1-S5 on 2026-07-30 and that contract is now live
(`inbox.py::emit_attention_item`, `ItemKind.PROPOSAL`, `InboxItem.item_kind`, two shipping emitters).
T2.1 is STARTABLE and remains the one unbuilt task. T1.3's sidebar windowing was re-scoped — there is
no chat sidebar; see the Execution log. Created 2026-07-18

---

## Context (code recon, 2026-07-18)

- **Session model** (`session.py`, `SessionManager`): FIFO message queue per session, pooling, channel-link map, memory modes (temporary/incognito). **History** (`history.py`): one JSONL per session under `sessions/`, 2MB rotation → `sessions/archive/`, 7-day archive retention. Metadata (`chat_persistence.py`): `folder_id`, tags, `is_pinned`, color, kanban tag-columns; `restore_recent_sessions` (folders/pins survive a restart window). API surface (from the earlier route audit): sessions CRUD, fork/undo/drop, resume/approve, regenerate/switch-variant/edit-resend, generate-title, color/folder/pin, tags, tag-columns (kanban), side-conversation lifecycle, handoff/channel-link.
- **Gaps for scale:** (1) **no cross-session search** (you can't find "that chat where I set up the Slack app"); (2) organization is **all-manual** (no auto-foldering, no suggested tags); (3) **no bulk ops** (archive/tag/delete many); (4) lifecycle is implicit (JSONL rotation is storage, not a user-facing archive/retention model — old sessions just accumulate in the sidebar); (5) **no templates/starters** (every chat starts blank; common setups aren't reusable); (6) **no export/share** of a conversation.

## Design

- **S1 — Cross-session search + a scalable sidebar:** an FTS index over session titles + message content (a `session_search` FTS5 table fed from history, incremental on turn-write; respects temporary/incognito — those are never indexed) with a search endpoint; the sidebar gains a search box + result grouping (by folder/date/match). Sidebar virtualization for large lists (the list is already `ListScaffold`-based — verify + add windowing). "Jump to message" from a result.
- **S2 — Smart organization + bulk:** **suggested organization** — a deterministic-first, LLM-last pass (per the corpus doctrine) that proposes a folder/tags for an untagged session (cheap heuristics: title keywords, workspace dir, channel origin → then an LLM suggestion only if ambiguous), surfaced as a *proposal* (propose-don't-write — an inbox `proposal` item per plan 42, or an inline accept chip). **Bulk operations**: multi-select → archive/tag/folder/delete/export. **Auto-archive rule**: sessions untouched for N days (configurable) move to an Archived view (not deleted — distinct from history JSONL rotation), decluttering the sidebar while staying searchable.
- **S3 — Lifecycle + templates + export:** an explicit **session lifecycle** (active / archived / pinned-never-archive) with a retention policy surface (what auto-archives, what never does, when archived sessions are purgeable — the user's call, defaults conservative); **session templates/starters** (save a session's setup — agent binding, model, system context, first prompt — as a reusable starter; "New from template"); **export** (a conversation → Markdown/JSON, credential-redacted via the existing `history.py` redaction) and **share** (a redacted read-only artifact via the artifacts system, never auto-published — owner action).

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md); class B per plan 31)

### C1 — Session search (`session_search.py`, new; FTS5 via `sqlite_compat` per plan 39)
```python
def index_turn(session_key: str, role: str, text: str) -> None: ...   # incremental; SKIPS temporary/incognito
def search_sessions(query: str, *, limit=30, folder=None) -> list[dict]: ...  # {session_key, title, snippet, ts, match_count}
def reindex_all() -> int: ...   # boot/repair; heartbeat-driven like the existing FTS reindex
```
Store: `~/.personalclaw/session_search.db` (its own FTS5 DB; not memory/knowledge). Respects the restriction registry (`session_restrictions.py`).

### C2 — Session metadata additions (`chat_persistence.py` session meta; additive)
```python
# NEW meta fields (tolerant reads; old sessions default):
lifecycle: str = "active"        # active | archived
last_activity_at: float = 0.0    # drives auto-archive
never_archive: bool = False      # pinned-lifecycle
template_id: str = ""            # if created from a template
```

### C3 — Templates + bulk + export (new routes; §2.2 envelope)
```python
# templates stored in entity_settings/session_templates.json
def save_template(*, name, agent, model, system_context, first_prompt="") -> str: ...
def create_from_template(template_id) -> str: ...  # returns new session_key
# bulk
POST /api/chat/sessions/bulk {ids:[...], action:"archive|tag|folder|delete|export", ...}
# export
GET /api/chat/sessions/{session}/export?format=md|json   # credential-redacted (history.py redaction)
```

### Integration points
- **Calls:** `history.py` (index source + redacted export), `session_restrictions` (index gating), `sqlite_compat`/`require_fts5` (plan 39), the heartbeat reindex hook, `emit_attention_item(kind="proposal")` (plan 42, org suggestions), the artifacts system (share), plan-31 migration.
- **Called by:** the chat sidebar (search, bulk, archived view), "New from template".
- **Storage owned:** `session_search.db`, `session_templates.json`, the four session-meta fields.
- **Gate/migration:** `session_management` (class B); migration backfills `last_activity_at` from history mtimes + reindexes.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Cross-session search + scalable sidebar

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `session_search.py`: FTS5 index (via `sqlite_compat`), `index_turn` on turn-write (skip temporary/incognito), `search_sessions`, `reindex_all` on the heartbeat (mirror the existing FTS reindex) | `src/personalclaw/session_search.py`, turn-write hook, heartbeat | indexing a chat makes it findable; an incognito session is never indexed (test) |
| T1.2 | Search endpoint + sidebar search box + result grouping + jump-to-message | chat handlers, `web/src/pages/ChatPage.tsx` sidebar | search returns ranked sessions; clicking a result opens it scrolled to the match |
| T1.3 | Sidebar windowing for large lists (verify ListScaffold; add virtualization if absent) | sidebar component | 500-session fixture scrolls smoothly (no jank; measure) |
| V1 | Validation: 100+ seeded sessions → find one by content in <2s; incognito exclusion verified; token-lint/theme pass | — | holds |

### Session 2 — Smart organization + bulk + auto-archive

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Suggested organization: deterministic heuristics → LLM only if ambiguous → propose (accept chip or inbox proposal); never auto-applies | new `session_organize.py`, frontend chip | an untagged session gets a sensible folder/tag *proposal*; accept applies, ignore leaves it |
| T2.2 | Bulk ops endpoint + multi-select bar (archive/tag/folder/delete/export) | chat handlers, sidebar | select many → archive in one action; archived leave the active list but stay searchable |
| T2.3 | Auto-archive: `last_activity_at` + a config rule (`session.auto_archive_days`, default 30, 0=off, 5-point wired) + an Archived view + `never_archive` pin | `chat_persistence.py`, config, sidebar | a stale fixture session auto-moves to Archived; a `never_archive` one never does |
| V2 | Validation: declutter a busy sidebar via bulk + auto-archive; nothing lost (all findable) | — | holds |

### Session 3 — Lifecycle + templates + export (Wave 3)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Retention surface: what auto-archives / never / when purgeable (conservative defaults; purge is explicit + confirmed, distinct from history JSONL rotation) | Settings + sidebar | retention policy editable; purge requires confirm + shows what's affected |
| T3.2 | Templates: save-as-template (agent/model/system-context/first-prompt) + "New from template" | `session_templates.json`, template API, composer/new-chat UI | save a configured session as a starter; new-from-template reproduces the setup |
| T3.3 | Export + share: Markdown/JSON export (redacted) + optional read-only shared artifact (never auto-published) | export endpoint, artifacts integration | export round-trips redacted; share produces a redacted artifact only on explicit action |
| V3 | Validation: full lifecycle — template→chat→archive→search→export | — | holds |

## Owner tasks (real world)
1. **Dogfood the auto-archive default** (30 days) on your real instance — tune it to your rhythm before it ships as a default.
2. Decide the **retention/purge defaults** — conservative proposed (archive freely, purge never-by-default, always explicit). This touches your data; your call.
3. Approve **suggested-organization** behavior (propose-only, never auto) — confirm you don't want silent auto-foldering.

## Risks & open questions
- **Search-index build cost** on a large existing history — `reindex_all` runs incrementally on the heartbeat (not a boot-blocking sweep); a fixture with thousands of sessions is the test.
- **Redaction completeness on export** — reuses `history.py`'s existing redaction (the same path session-archive reads use); a seeded-secret fixture proves it.
- **Open:** whether templates should capture *loaded skills/knowledge context* too — defer to a v2 (starters cover the 80%); DISCOVERY-file if demand appears.

---

## Execution log

### 2026-07-28 — Session 1 (T1.1, T1.2 + V1): DONE. T1.3 re-scoped — see below.

New `session_search.py`: an FTS5 index over session transcripts with `index_turn` on
turn-write, `search_sessions`, and an incremental `reindex_all` on the heartbeat.
Replaces a linear scan that read up to 500 transcript files per query and silently
stopped finding anything past that window.

**DEVIATION (E1) — `sqlite_compat` does not exist.** §C1 specifies "FTS5 via
`sqlite_compat` per plan 39". That module has never been written: it appears only as
aspirational text in PLATFORM-REACH and in this plan's own §C1/§C3. Rather than
build a compat layer this plan doesn't own, availability is handled the way the rest
of the repo already handles it — `except sqlite3.OperationalError` around table
creation and queries, returning empty so the caller falls back. Verified by
simulating an FTS5-less SQLite build in a test. When PLATFORM-REACH lands
`sqlite_compat`, this is a one-line swap.

**DEVIATION — a session-grained index, not a turn-grained one.** §C1's `index_turn`
signature is preserved, but the turn's own text is not what gets stored: the whole
transcript is re-read and the session's single row replaced. Reason:
`_save_session_to_history` REWRITES the entire JSONL on every turn, so appending
per-turn rows would drift out of sync with the file it's supposed to mirror. One row
per session also makes `snippet()` return the best-matching passage from the whole
conversation instead of an arbitrary turn.

**Restricted-session exclusion is enforced at three points**, not one: when indexing
(the persisted `memory_mode` and the live registry), when re-indexing (a
now-restricted session is purged), and again at READ time. The read-time check is
what makes a session hidden the instant it's reclassified, rather than at the next
sweep.

**T1.3 (sidebar windowing) — RE-SCOPED, and the premise was wrong.** The task says
"the sidebar gains a search box… the list is already ListScaffold-based — verify +
add windowing". **There is no chat sidebar**: `ChatPage.tsx:350` documents "No left
sidebar" explicitly. The session list lives in `ChatHistoryPage` (a dedicated
`#/chat/history` page), which already HAS a search box wired to
`/api/sessions/search` with a 300ms debounce — so T1.2's "add a search box" was
already built. What was missing is *why* a result matched: the page discarded
everything but the keys. It now renders the index's snippet under the title with the
matched terms marked. Windowing is deferred to Session 2 with a measurement first —
the page renders plain rows and the honest answer is that nobody has shown it janks;
adding virtualization to an un-profiled list is speculative work, and Session 2
already owns that page for bulk actions.

**Two bugs found by driving a real gateway with 120 seeded sessions** (neither
visible to unit tests):

1. **A fresh install had no index for five minutes.** `HeartbeatService._tick` starts
   at 1, so `_tick % 5 == 0` first fires on tick 5 — exactly the window where a new
   user tries search and finds the narrow scan. The sweep now also runs on tick 1.
   (Worth noting the same off-by-cadence affects the pre-existing tick-15 FTS
   rebuild; left alone as out of scope, but it's the same shape.)
2. **The fallback scan leaked restricted sessions.** `ConversationLog.search_sessions`
   checked only the persisted `memory_mode`, so a session marked incognito AFTER some
   lines were written — its metadata still saying "persistent" — was returned in
   content search. This is a PRE-EXISTING gap, not introduced here, but the new
   endpoint made it observable: the index correctly excluded the session while the
   scan surfaced it. Both paths now consult the live registry too.

**V1 validated as a user** (isolated dev home, 120 seeded sessions + a needle):
first-tick index build; `source: index` with a `<<pomegranate>>`-marked snippet in
**31ms**; a session marked incognito after the fact refused by BOTH the index and the
scan; zero errors in the gateway log. The endpoint now reports which path answered
(`source: index|scan`), which is what made the leak visible in the first place.

**Snippets are rendered as parsed parts, never HTML** — the text is user-authored
transcript content, so `<<`/`>>` markers are split into marked spans rather than
injected as markup; an unpaired marker degrades to plain text.

Tests: `tests/test_session_search.py`, 56 cases. Full suite 8631 passed; lint clean;
web typecheck + 268 vitest + build green. **Remaining: Sessions 2-4** (organization/
bulk/auto-archive, templates, export) — separate clean sub-scopes, not started.

- 2026-07-29 — **DONE (S2, T2.2 + T2.3).** Bulk session ops + the session lifecycle
  (archive / restore / never-archive) + the auto-archive rule, end to end.
  - **Backend:** new `dashboard/session_lifecycle.py` (the pure rule: `stale_session_keys`
    previews, `run_auto_archive` applies, `set_lifecycle` transitions) and
    `dashboard/session_bulk.py` (`POST /api/chat/sessions/bulk`,
    `POST /api/chat/sessions/auto-archive`, `PATCH /api/chat/sessions/{session}/lifecycle`).
    Three fields on `_ChatSession` (`lifecycle`, `last_activity_at`, `never_archive`)
    wired through `__slots__`, `to_dict()`, and **all three** `chat_persistence` meta
    sites. `session.auto_archive_days` (default 30, 0=off) wired through the full
    round-trip contract incl. `_EDITABLE_CONFIG`. Hourly heartbeat pass via a new
    `on_auto_archive` callback (the heartbeat has no `DashboardState` and shouldn't grow
    one, so the gateway injects it like `on_due_commitments`).
  - **Frontend:** Active/Archived segmented view (server-filtered via `?archived=1`, URL-
    backed so the archive is deep-linkable), row multi-select + a selection bar
    (Archive / Restore / Never archive), per-row context-menu lifecycle actions, and a
    persistent outcome note ("Archived 38 · 2 not found").
  - **New primitive:** `ui/forms.tsx` gains **`Checkbox`** (+ `.doc.ts` + 3 tests). The
    primitive-adoption ratchet correctly rejected a raw `<input type="checkbox">`, and
    there was no checkbox primitive; it owns the `stopPropagation` guard that clickable
    list rows need, so no call site can forget it.
  - **DEVIATION — T2.1 (suggested organization) NOT taken; it is BLOCKED.** Its spec
    requires `emit_attention_item(kind="proposal")`, which **does not exist**: the only
    occurrence in the tree is a forward-looking comment at `feedback.py:379`, and
    `InboxItem` has no `kind` field at all (`inbox.py:62-88`; `Classification` is
    needs_reply/fyi/noise). Building it means inventing the attention contract that
    **INBOX-NOTIFICATIONS-UNIFICATION owns** — a guaranteed rewrite. Deferred to after
    that plan lands. (Note `chat_retag.py` already covers much of T2.1's user value.)
  - **BUG FOUND IN OWN WORK, during as-a-user validation:** rehydration replays a
    transcript through `_ChatSession.append()`, so the un-archive-on-use rule fired on
    LOAD — an archived chat silently un-archived itself just by being opened, or by a
    restart restoring it. Fixed by using `ts` as the live-vs-replay discriminator (a live
    turn passes none; replay passes each message's stored ts). Test added.
  - **Upgrade safety:** `last_activity_at == 0.0` (every session predating the field)
    reads as **not stale**. Treating unknown as ancient would archive a user's entire
    history on first run. Also: default-valued sessions write **no** new meta keys, so
    existing meta lines stay byte-identical.
  - **Deliberate omission:** `delete` is NOT a bulk op. Bulk deletion is irreversible and
    must not sit one mis-click from archive, which is reversible.
  - **Validated as a user** on an isolated dev home: bulk-archived 2 of 3 (one bogus key
    reported `missing` without failing the batch); active/archived lists split correctly;
    restore round trip returned all rows; `never_archive` set; the config kill switch
    (`0` → `enabled:false`) verified through the real PATCH route; multi-select →
    Archive → outcome note observed stably across 8 samples with the row count dropping;
    0 console errors, 0 gateway tracebacks.
  - **Gates:** `make lint` clean (mypy 538 files) · backend **8809 passed** · web
    **283 passed** (32 files) + typecheck + build. The offline agent reference was
    regenerated (`python -m personalclaw.manifest_reference`) since three routes are new —
    its drift test caught that, as designed.
  - Pre-existing/unrelated: `test_cron.py`'s spring-forward test (core issue #85).

### 2026-07-30 — Session 3 (T3.1, T3.2, T3.3 + V3): DONE. Plan COMPLETE.

Retention surface, session starters, and redacted export — plus a **latent S2 bug this
session's validation exposed** (below), which was the difference between a retention rule
that works and one that only appeared to.

**BUG FOUND IN S2's SHIPPED WORK — the auto-archive rule was inert for its main case.**
`stale_session_keys` iterated `state._sessions` (resident sessions) only. But
`dashboard.restore_sessions` defaults to **False** (`config/loader.py:739-745`), and
`restore_recent_sessions` otherwise only loads sessions modified inside a 30-minute
window (or pinned/foldered) — so **a chat idle for months is precisely the one that is
NOT in memory**. The rule therefore skipped exactly the sessions it exists to archive,
and reported `count: 0` while doing it. Measured on a real gateway with 3 sessions seeded
90 days stale: preview said `0`; the same 3 after adding a `folder_id` (which forces a
restore) said `1`. `stale_session_keys` now sweeps both halves and `run_auto_archive`
archives a non-resident session by writing `lifecycle` into its transcript metadata line
(`_archive_on_disk`) — **without loading it**, since restoring every stale chat just to
archive it would undo the reason it wasn't resident. Live re-verify: `count: 0` → `count:
4`, archived on disk, idempotent on a second run, gone from the active list, present in
`?archived=1`, and **restorable** via both the single-session PATCH and bulk restore
(reversibility is the entire safety argument for archiving on a timer). 14 new tests
pin the disk half, including the same upgrade-safety rule (`last_activity_at`
missing ⇒ NOT stale), channel-thread exemption, no-double-count when a session is both
resident and on disk, and a broken-listing degrade-to-resident-only path.

- **T3.1 — retention surface.** `session.auto_archive_days` shipped in S2 wired through
  four of the config contract's five points; **the frontend control was never built**, so
  a rule that archives chats after 30 days was running hourly with no way to see or change
  it. Added to `ChatPanel`'s existing "Context & lifecycle" section (not a new panel), with
  a **live preview** driven by the existing `dry_run` endpoint — so the number shown is the
  number that would move, not an estimate. A retention rule the user can't inspect is
  indistinguishable from data loss.
  - **Purge deliberately NOT built.** §T3.1 mentions a purge surface; S2 deliberately
    excluded bulk delete because irreversible actions must not sit beside reversible ones,
    and the single-session DELETE already carries its own confirmation. Adding a bulk purge
    here would undo that decision for no user demand. Recorded rather than silently skipped.
- **T3.2 — starters.** New `dashboard/session_templates.py` (`entity_settings/
  session_templates.json`, so it rides the durability inventory's existing
  `entity_settings` item and needs no backup wiring) + routes. Captures **setup only** —
  agent, model, reasoning effort, optional first prompt — never the transcript, and
  deliberately not the workspace binding (a template dragging a workspace path along would
  point a new chat at a directory the user wasn't thinking about). The field dict doubles
  as the key **allowlist** and type schema, mirroring `INBOX_DEFAULTS`.
  - **DEVIATION — no server-side `create_from_template()`.** §C3 sketched
    `create_from_template(template_id) -> session_key`. The chat page mints a session
    lazily on first send (`ChatPage.tsx::ensureSession`), so a second server-side creation
    path would mean two ways a session comes into existence AND would leave an empty chat
    behind whenever a user opened a starter and walked away. Picking a starter instead
    **prefills** the composer selection + prompt and lets the one existing `ensureSession`
    path run. Same user outcome, one creation path.
- **T3.3 — export.** New `dashboard/session_export.py`: Markdown or JSON, via
  `GET /api/chat/sessions/{session}/export?format=md|json`, following `portability.py`'s
  established `Content-Disposition` download pattern.
  - **PREMISE CORRECTED — export does NOT inherit "history.py's existing redaction."**
    The plan's §S3/risks say export "reuses `history.py`'s existing redaction". The
    dashboard write path redacts assistant/tool content but **deliberately SKIPS `user` and
    `system` roles** (`chat_persistence.py:606-608`), and `ConversationLog.append` redacts
    nothing at all — so a credential the user *typed* is stored raw. Export re-runs both
    passes over **every** role: defense-in-depth for the already-redacted ones, and the
    only redaction the user/system roles ever get before text leaves the machine. Verified
    live end-to-end: a transcript containing `AKIAIOSFODNN7EXAMPLE` and a Slack bot token
    in a **user** message exported with both replaced by `[REDACTED: credential]`. Titles
    are redacted too (an auto-titled session can carry the secret in its title).
  - Message content is emitted as a **blockquote** so a conversation about markdown can't
    restructure the export around its own headings. The artifact declares `redacted: true`
    so a consumer never mistakes it for a verbatim transcript.
- **BUG IN OWN WORK, caught by a test written for it:** `export_filename` filtered on
  `str.isalnum()`, which is **True for CJK and accented letters** — so a Japanese chat
  title produced a non-ASCII filename, and the route emits the plain `filename="…"` form,
  which cannot carry those bytes. Now filters on `isascii() and isalnum()` with a `chat`
  fallback, so the download works regardless of the user's locale.
- **Validated as a user** on an isolated dev home (never `~/.personalclaw`), driving the
  real gateway + browser: template CRUD incl. the 400 on a blank name; the literal
  `templates` path **not** captured by `/{session}` (the aiohttp ordering hazard `bulk`
  already had); export md/json + 400 on a bad format + 404 on an unknown session + correct
  `Content-Disposition`/`nosniff` headers; the config round-trip through the real PATCH
  route (`45` persisted → rule reads it → `0` reports `enabled:false` → `99999` rejected);
  the retention row rendering `30 days` with its live preview; the starter chip applying
  agent+model+effort+prompt and enabling Send; starter delete from Settings. **0 console
  errors, 0 gateway tracebacks from the new code** (the log's only tracebacks are
  `no model provider resolves for use case 'background'` — expected with no model bound in
  a throwaway home). SEL audit confirmed for `chat.session_export`,
  `chat.template_create`, `chat.sessions_auto_archive`.
- **Gates:** `make lint` clean (mypy 550 files) · backend **9034 passed** · web
  **283 passed** (32 files) + typecheck + build. Offline agent reference regenerated
  (5 new routes) — its drift test caught the staleness, as designed.
- Tests: `tests/test_session_starters.py` (33) + 14 added to `tests/test_session_lifecycle.py`.

**The plan is now COMPLETE except T2.1 (suggested organization)**, which stays BLOCKED on
INBOX-NOTIFICATIONS-UNIFICATION owning the `emit_attention_item(kind="proposal")` contract
— unchanged from S2's finding, and `chat_retag.py` already covers much of its user value.
