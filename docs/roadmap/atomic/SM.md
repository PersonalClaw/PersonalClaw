# SESSION-MANAGEMENT — atomic plans

**Source plan:** [`SESSION-MANAGEMENT`](../plans/SESSION-MANAGEMENT.md)  
**Code:** `SM`  
**Source status:** in_progress

Decomposed SESSION-MANAGEMENT into 8 atoms: 7 done (search index, search UI, windowing re-scope, bulk+lifecycle+auto-archive, retention surface, templates, export) and 1 todo — SM-5 (T2.1 suggested organization), the plan's one unbuilt task, now unblocked since its cross-plan dependency on INBOX-NOTIFICATIONS-UNIFICATION's proposal contract has landed.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `SM-1` | ✅ | session_search.py: FTS5 cross-session index + incremental heartbeat reindex | — | Indexing a chat makes it findable via search_sessions; temporary/incognito sessions are never indexed and restricted-session exclusion holds at index/reindex/read time (test); reindex_all runs on the heartbeat (tick 1 + tick 5). Shipped 2026-07-28, tests/test_session_search.py (56 cases). DEVIATION: sqlite_compat (plan 39) does not exist — used except sqlite3.OperationalError fallback; session-grained (not turn-grained) index because history rewrites the full JSONL per turn. |
| `SM-2` | ✅ | Search endpoint + ChatHistoryPage search box, marked snippets, jump-to-message | `SM-1` | Search returns ranked sessions; clicking a result opens it scrolled to the match; the page renders the index's best-match snippet with matched terms marked (rendered as parsed parts, never HTML); endpoint reports which path answered (source: index\|scan). Shipped 2026-07-28. |
| `SM-3` | ✅ | Sidebar windowing — premise corrected + deferred pending measurement | `SM-2` | Re-scoped: there is no chat sidebar (ChatPage documents 'No left sidebar'); the session list lives in ChatHistoryPage which already had a search box. Virtualization deferred as speculative until a real jank profile exists on a large fixture; no windowing code shipped. Recorded in the 2026-07-28 Execution log. |
| `SM-4` | ✅ | Bulk ops + session lifecycle + auto-archive rule + Archived view | — | Multi-select → archive/tag/folder/export in one action (delete deliberately excluded as irreversible); archived rows leave the active list but stay searchable; stale sessions auto-move to Archived (incl. non-resident, archived on-disk without loading — S3 fix) while never_archive pins never do; session.auto_archive_days (default 30, 0=off) round-trips through _EDITABLE_CONFIG PATCH; last_activity_at==0.0 reads as not-stale (upgrade safety). Shipped 2026-07-29 (+ non-resident fix 2026-07-30); ui/forms Checkbox primitive added; tests/test_session_lifecycle.py. |
| `SM-5` | ✅ | T2.1 Suggested organization: deterministic→LLM folder/tag proposals via inbox proposal contract | `EXT:INBOX-NOTIFICATIONS-UNIFICATION:emit_attention_item(kind=proposal)/ItemKind.PROPOSAL/InboxItem.item_kind (S1-S5 landed 2026-07-30 — edge now satisfied)` | An untagged session gets a sensible folder/tag proposal from deterministic heuristics (title keywords, workspace dir, channel origin) with an LLM suggestion only when ambiguous, surfaced as an inbox proposal item (ItemKind.PROPOSAL) or an inline accept chip; accept applies, ignore leaves it; never auto-applies. New session_organize.py + frontend chip. |
| `SM-6` | ✅ | Retention surface: editable auto-archive policy with live dry-run preview | `SM-4` | Retention policy editable in ChatPanel's 'Context & lifecycle' section with a live dry_run preview of exactly what would move (not an estimate); purge deliberately NOT built (single-session DELETE keeps its own confirm; bulk delete stays excluded). Shipped 2026-07-30 — closed the S2 gap where auto_archive_days ran hourly with no UI. |
| `SM-7` | ✅ | Session templates/starters: save setup + New-from-template prefill | — | Save a configured session (agent/model/reasoning effort/optional first prompt — setup only, no transcript, no workspace binding) as a starter in entity_settings/session_templates.json; picking a starter prefills the composer selection + prompt and enables Send. DEVIATION: no server-side create_from_template() — reuses the one lazy ensureSession creation path. Shipped 2026-07-30, tests/test_session_starters.py (33). |
| `SM-8` | ✅ | Export + share: credential-redacted Markdown/JSON export and read-only artifact | — | GET /api/chat/sessions/{session}/export?format=md\|json returns a download with correct Content-Disposition/nosniff; redaction re-runs over ALL roles + title (PREMISE CORRECTED — user/system roles are stored raw, not redacted by history.py); content emitted as blockquote; share produces a redacted read-only artifact only on explicit owner action, never auto-published. Shipped 2026-07-30 (dashboard/session_export.py). |

## Atom scopes

### `SM-1` — session_search.py: FTS5 cross-session index + incremental heartbeat reindex

**Status:** done

Session 1 (T1.1); Contracts §C1 (session_search.py)

**Done when:** Indexing a chat makes it findable via search_sessions; temporary/incognito sessions are never indexed and restricted-session exclusion holds at index/reindex/read time (test); reindex_all runs on the heartbeat (tick 1 + tick 5). Shipped 2026-07-28, tests/test_session_search.py (56 cases). DEVIATION: sqlite_compat (plan 39) does not exist — used except sqlite3.OperationalError fallback; session-grained (not turn-grained) index because history rewrites the full JSONL per turn.

### `SM-2` — Search endpoint + ChatHistoryPage search box, marked snippets, jump-to-message

**Status:** done

Session 1 (T1.2); Contracts §C1 (search_sessions), Design §S1

**Done when:** Search returns ranked sessions; clicking a result opens it scrolled to the match; the page renders the index's best-match snippet with matched terms marked (rendered as parsed parts, never HTML); endpoint reports which path answered (source: index|scan). Shipped 2026-07-28.

### `SM-3` — Sidebar windowing — premise corrected + deferred pending measurement

**Status:** done

Session 1 (T1.3)

**Done when:** Re-scoped: there is no chat sidebar (ChatPage documents 'No left sidebar'); the session list lives in ChatHistoryPage which already had a search box. Virtualization deferred as speculative until a real jank profile exists on a large fixture; no windowing code shipped. Recorded in the 2026-07-28 Execution log.

### `SM-4` — Bulk ops + session lifecycle + auto-archive rule + Archived view

**Status:** done

Session 2 (T2.2, T2.3); Contracts §C2 (session-meta fields), §C3 (bulk endpoint)

**Done when:** Multi-select → archive/tag/folder/export in one action (delete deliberately excluded as irreversible); archived rows leave the active list but stay searchable; stale sessions auto-move to Archived (incl. non-resident, archived on-disk without loading — S3 fix) while never_archive pins never do; session.auto_archive_days (default 30, 0=off) round-trips through _EDITABLE_CONFIG PATCH; last_activity_at==0.0 reads as not-stale (upgrade safety). Shipped 2026-07-29 (+ non-resident fix 2026-07-30); ui/forms Checkbox primitive added; tests/test_session_lifecycle.py.

### `SM-5` — T2.1 Suggested organization: deterministic→LLM folder/tag proposals via inbox proposal contract

**Status:** done

Session 2 (T2.1); Design §S2 (suggested organization), Integration points (emit_attention_item kind=proposal)

**Done when:** An untagged session gets a sensible folder/tag proposal from deterministic heuristics (title keywords, workspace dir, channel origin) with an LLM suggestion only when ambiguous, surfaced as an inbox proposal item (ItemKind.PROPOSAL) or an inline accept chip; accept applies, ignore leaves it; never auto-applies. New session_organize.py + frontend chip.

### `SM-6` — Retention surface: editable auto-archive policy with live dry-run preview

**Status:** done

Session 3 (T3.1); Design §S3 (retention policy surface)

**Done when:** Retention policy editable in ChatPanel's 'Context & lifecycle' section with a live dry_run preview of exactly what would move (not an estimate); purge deliberately NOT built (single-session DELETE keeps its own confirm; bulk delete stays excluded). Shipped 2026-07-30 — closed the S2 gap where auto_archive_days ran hourly with no UI.

### `SM-7` — Session templates/starters: save setup + New-from-template prefill

**Status:** done

Session 3 (T3.2); Contracts §C3 (templates)

**Done when:** Save a configured session (agent/model/reasoning effort/optional first prompt — setup only, no transcript, no workspace binding) as a starter in entity_settings/session_templates.json; picking a starter prefills the composer selection + prompt and enables Send. DEVIATION: no server-side create_from_template() — reuses the one lazy ensureSession creation path. Shipped 2026-07-30, tests/test_session_starters.py (33).

### `SM-8` — Export + share: credential-redacted Markdown/JSON export and read-only artifact

**Status:** done

Session 3 (T3.3); Contracts §C3 (export)

**Done when:** GET /api/chat/sessions/{session}/export?format=md|json returns a download with correct Content-Disposition/nosniff; redaction re-runs over ALL roles + title (PREMISE CORRECTED — user/system roles are stored raw, not redacted by history.py); content emitted as blockquote; share produces a redacted read-only artifact only on explicit owner action, never auto-published. Shipped 2026-07-30 (dashboard/session_export.py).

