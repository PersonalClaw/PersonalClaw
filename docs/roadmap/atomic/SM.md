# SESSION-MANAGEMENT — atomic plans

**Source plan:** [`SESSION-MANAGEMENT`](../plans/SESSION-MANAGEMENT.md)  
**Code:** `SM`  
**Source status:** in_progress

Decomposed SESSION-MANAGEMENT into 12 atoms: 9 done and 3 todo (the rev-18 research atoms `SM-10`-`SM-12`). Done: search index, search UI, windowing re-scope, bulk+lifecycle+auto-archive, suggested organization, retention surface, templates, export (SM-8) and share (SM-9). SM-9 exists because T3.3 asked for two halves — export **and** an optional read-only shared artifact — and only export shipped, while SM-8's evidence line already claimed both.

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
| `SM-8` | ✅ | Export: credential-redacted Markdown/JSON transcript download | — | GET /api/chat/sessions/{session}/export?format=md\|json returns a download with correct Content-Disposition/nosniff; redaction re-runs over ALL roles + title (PREMISE CORRECTED — user/system roles are stored raw, not redacted by history.py); content emitted as blockquote. Shipped 2026-07-30 (dashboard/session_export.py). **RECORD CORRECTED 2026-08-11:** this row previously also claimed "share produces a redacted read-only artifact only on explicit owner action, never auto-published" — that half never shipped (no route, no share verb, no frontend affordance; the plan's own execution-log entry is headed "T3.3 — export."). Only export shipped here; share is `SM-9`. |
| `SM-9` | ✅ | Share: one chat as a redacted, read-only artifact in the owner's own library | `SM-8` | POST /api/chat/sessions/{session}/share creates a `markdown` artifact whose body is session_export.render_markdown's output VERBATIM (one redactor, two callers), `readonly=True`, tagged `shared-chat`, with the source chat + `redacted` on the creation event; NativeArtifactProvider.update/update_binary/revert refuse a readonly artifact (in the STORE, so MCP tools and workflow actions hit the same wall) while delete stays allowed; restricted (incognito/temporary) chats are refused 403; the ChatHistory row menu carries "Share as read-only artifact" beside the two Export items and lands the user on the created artifact; never auto-published, proven by an AST census of every share_session call site in src/personalclaw plus a poisoned-source test that proves the census can fail. NOT a publish: no public URL, no share token, no auth bypass — external exposure stays EXTERNAL-ACCESS's decision. Shipped 2026-08-11 (dashboard/session_share.py, tests/test_session_share.py, 22 cases). |
| `SM-10` | ⬜ | Spend-ceiling price-key rail: reconcile price-table ids with catalog ids (T03) | — | Every catalog model id resolves to a price-table entry (one canonical id form + a normalization shim at the single lookup seam); a rail test enumerates the live catalog against the price table and fails on any unresolvable id; daily spend renders a nonzero value for a session that used a priced model; regression test pins the dot/hyphen normalization. |
| `SM-11` | ⬜ | Session-index fidelity: deleted chats must leave the FTS index (T22) | — | Chat deletion removes the corresponding FTS rows in the same transaction (or a compensating sweep with a test proving eventual removal); search over a deleted session returns nothing; a migration/backfill purges already-orphaned FTS rows with a stated count; tests cover delete-then-search and the backfill. |
| `SM-12` | ⬜ | MCP tool-name wire fidelity: preserve exact names across the chat turn boundary (T00) | — | A wire map documents every transform a tool name undergoes end-to-end; round-trip property test asserts name-in == name-out across the chat turn boundary for the full registered-tool census; any lossy transform is replaced by a reversible one; the failing turn shape from the draft reproduces green. |

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

### `SM-8` — Export: credential-redacted Markdown/JSON transcript download

**Status:** done

Session 3 (T3.3); Contracts §C3 (export)

**Done when:** GET /api/chat/sessions/{session}/export?format=md|json returns a download with correct Content-Disposition/nosniff; redaction re-runs over ALL roles + title (PREMISE CORRECTED — user/system roles are stored raw, not redacted by history.py); content emitted as blockquote. Shipped 2026-07-30 (dashboard/session_export.py).

**RECORD CORRECTED (2026-08-11).** This atom's title and evidence line used to cover both of
T3.3's halves and assert that "share produces a redacted read-only artifact only on explicit
owner action, never auto-published". Nothing in the code backed that: there was no share
route, no `share_session`/`publish_session`/`shareable` symbol anywhere in `src/personalclaw`,
and no frontend affordance — `web/src/lib/api.ts` carried only the export URL. The plan's own
execution-log entry for the session is headed "**T3.3 — export.**" and documents export alone.
SM-8 is therefore export only; the share half is `SM-9` below, which makes the claim true.

### `SM-9` — Share: one chat as a redacted, read-only artifact in the owner's own library

**Status:** done

Session 3 (T3.3, second half); Design §S3 + Integration points ("the artifacts system (share)")

**Done when:** `POST /api/chat/sessions/{session}/share` creates a redacted, read-only
artifact of one conversation on an explicit authenticated owner action, and nothing else in
the tree can create one.

**Design.**

- **Scope: inside the instance, not on the internet.** "Share" means an artifact in the
  owner's own artifact library, reachable only through this gateway's existing session auth.
  No public URL, no token-bearing link, no unauthenticated route, and nothing added to any
  auth-bypass allowlist. Exposing a conversation outside the machine is EXTERNAL-ACCESS's
  scope and an owner decision, so this atom deliberately does not gesture at it.
- **Redaction is reused, not reimplemented.** The artifact body is
  `session_export.render_markdown(...)`'s output **verbatim**. That function re-runs both
  redaction passes over EVERY role because the dashboard write path deliberately skips
  `user`/`system` (`chat_persistence.py`), so the share inherits the only redaction those
  roles ever get. `session_export._redact` was renamed to the public `redact_field` and is
  the single implementation both callers use — the artifact NAME is redacted through it too,
  because an auto-titled chat can carry a secret in its title.
- **Read-only is enforced in the STORE.** New persisted `Artifact.readonly`, written only by
  the share path; `NativeArtifactProvider.update`, `update_binary` and `revert` raise
  `PermissionError` when it is set (the route layer already maps that to a 400 with the
  message). The guard lives in the provider rather than `artifacts/handlers.py` because the
  MCP artifact tools and workflow action providers call the provider directly — a route-level
  check would leave the model an unguarded door to the same edit. `delete` stays allowed:
  read-only is not undeletable. This is what stops the transcript round-tripping back into a
  session — an edited copy of a redacted transcript is neither the conversation that happened
  nor a redacted export of it, and no path turns an artifact back into chat history.
- **Kind `markdown`, source `manual`.** A TEXT kind (nothing executes it, unlike
  `widget`/`html`/`react`), and `manual` because the other sources (`chat`, `cron`,
  `subagent`) all name an automated producer.
- **Never auto-published.** `share_session` has exactly one call site: the explicit POST
  handler. No heartbeat tick, no post-turn hook, no "share on archive" convenience. Proven
  structurally by an AST census of every call site under `src/personalclaw`, plus a second
  test that runs the census against a poisoned source with automated callers and asserts it
  flags them — a structural assertion that cannot fail is decoration.
- **Restricted chats are refused (403).** An incognito/temporary chat promises to leave
  nothing durable behind, and an artifact is durable library state. Export still works: a
  download is the user holding their own text for a moment. The asymmetry is deliberate and
  asserted in a test so a reader does not have to guess.

**Implementation plan.**

1. `artifacts/models.py`: `Artifact.readonly` (persisted + on the wire), tolerant read.
2. `artifacts/provider.py` + `artifacts/native.py`: `create(..., readonly=False)`; refuse
   `update`/`update_binary`/`revert` on a frozen artifact via one `_refuse_if_readonly`.
3. `dashboard/session_export.py`: `_redact` → public `redact_field` (one redactor, two callers).
4. New `dashboard/session_share.py`: `share_session(provider, ...)` — the ONE share path.
5. `dashboard/session_starters.py`: `POST /api/chat/sessions/{session}/share`, restricted-chat
   refusal, SEL audit (`chat.session_share`), plus `_read_transcript` extracted so export and
   share resolve the same history key the same way.
6. Frontend: `api.shareSession`; "Share as read-only artifact" in the ChatHistory row context
   menu beside the two Export items, landing on the created artifact; `ArtifactViewer` reads
   `readonly` (banner, no editor/save/snapshot/revert) and `ArtifactsSection` hides the
   collection control, so no affordance is offered that the server will refuse.
7. `tests/test_session_share.py` (22): body-equals-export, credential + title redaction,
   provenance, all three store refusals, delete still works, meta round-trip, route
   201/404/403/405/503, the call-site census and its teeth proof.
8. Regenerate `src/personalclaw/reference/` (one new route: 632 → 633 agent-callable).

