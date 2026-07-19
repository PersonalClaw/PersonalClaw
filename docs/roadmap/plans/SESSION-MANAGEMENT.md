# Plan: Session Management — Organize, Find, and Curate Conversations at Scale

**Status:** DESIGNED — created 2026-07-18 (roadmap rev 10; owner ask: chat session management improvements)
**Created:** 2026-07-18
**Wave:** 2 (S1-2: search + organization) + 3 (S3: lifecycle + templates)
**Depends on:** nothing hard (builds on the shipped session model). Coordinates with ONBOARDING-UX (43 — progressive disclosure of session features), DESIGN-SYSTEM-CONSISTENCY (51 — the chat sidebar is a flagship surface), INBOX-NOTIFICATIONS-UNIFICATION (42 — sessions surface needs-input items), CONTEXT-ECONOMY (12 — long-session compaction is upstream).
**Scope:** the chat sidebar already has folders/tags/kanban/pin/color/fork/undo/variants/side-conversations — but at 100+ sessions it becomes a scroll. This plan makes conversations **findable, organizable at scale, and curatable through a lifecycle**: cross-session search, smart auto-organization, bulk operations, session lifecycle (active→archived→retention), templates/starters, and export/share. **Soul guardrail:** sessions are core-owned standalone entities (not pluggable) — this stays in core; the FIFO-queue, memory-mode, and history-rotation invariants are untouched. Class **B** where it adds persisted session metadata (gate `session_management` + migration).

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
