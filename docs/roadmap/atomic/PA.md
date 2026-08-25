# PROACTIVE-ASSISTANT — atomic plans

**Source plan:** [`PROACTIVE-ASSISTANT`](../plans/PROACTIVE-ASSISTANT.md)  
**Code:** `PA`  
**Source status:** proposed

6 atoms, all todo. Triage flagship = PA-1 (approval memory + config foundation, independently landable) → PA-2 (5-stage digest pipeline) → PA-3 (inbox-op action provider + budgeted auto-execution) → PA-5 (triage FE + validation). Decision journal = PA-4 (native type + tools + horizon triggers + R18 lesson) → PA-6 (journal view + calibration FE). Cross-plan deps (substrate/guardrails/flywheel/inbox-notif) are already DONE/shipped enough to unblock, but recorded as EXT edges.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PA-1` | ✅ | Approval memory + ProactiveConfig foundation (Session 1) | — | `user.approval.` prefix resolves to an `approval` MemoryKind in `_kind_from_key` and is excluded from `_NON_FACT_KEY_CLAUSE`; deterministic most-specific/deny-wins rule matcher, reply-grammar parser, and 24h/7d/30d suppression cooldowns are unit-tested pure functions; `ProactiveConfig` round-trips through the 5-point wiring (test_config_roundtrip green); `triage_rules` tool lists/adds/revokes rules with provenance |
| `PA-2` | ✅ | Triage pipeline: collect → classifier gate → tiered strict-JSON proposals → rank → deliver + Morning-triage template (Session 2) | `PA-1`, `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:Run Ledger materiality rows (AUTO-R2) + delivery contract + fire→spawn classifier machinery`, `EXT:AUTONOMY-GUARDRAILS:NEW-2 typed structured-output (output_type) for the proposal schema`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:notify gate + notification-kind registry the digest delivers through` | Firing the bundled "Morning triage" WorkflowDef collects inbox + channel + Run-Ledger items into a stable ordinal manifest, the classifier gate drops/surfaces per per-source NL rules (zero-item windows short-circuit before LLM spend), ONE strict-JSON call emits ≤8 tier-clamped proposals honoring the exact-ordinal-id contract, and a materiality-ranked digest is delivered through the notify gate as one normal WorkflowRun |
| `PA-3` | ⬜ | Trivial-tier auto-execution + `inbox-op` action provider (Session 3) | `PA-2`, `EXT:AUTONOMY-GUARDRAILS:NEW-1 budget floor (per-run/day token+dollar+action ceilings) consulted before each auto-executed action` | `inbox-op` implements ActionProvider, is registered via register_action_provider, added to ALLOWED_HOOK_PROVIDERS, and carries a settings-schema manifest; trivial/always-approve proposals auto-execute bounded by the NEW-1 budget floor + max_auto_actions_per_run cap, each emitting a named-rule ledger row with one-click undo, and budget breach demotes remaining proposals to pending with skipped_budget rows; adversarial injection test (criterion 2) passes |
| `PA-4` | ⬜ | Decision journal core: `decision` native type + tools + horizon triggers + R18 lesson (Session 4) | `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:one-shot clock/at trigger with delete_after_run (commitment-conversion pattern)`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:LEARN-R18 pending→resolved lesson lifecycle (write_lesson)` | `decision` is the 13th NATIVE_TYPES entry riding the Passthrough graph; log_decision/decision_list/decision_resolve tools exist; log_decision creates a searchable/@-pickable knowledge item and mints exactly one one-shot clock trigger with a deterministic `system:decision-journal:<id>`; the decision-review WorkflowDef captures the outcome, sets status=resolved, and writes a `lesson.*` memory row via write_lesson citing expectation-vs-outcome, linked only by soft reference (criterion 5) |
| `PA-5` | ⬜ | Triage FE surfaces + as-a-user validation (Session 5a) | `PA-2`, `PA-3` | Digest card renders auto-done+undo, pending proposals with tier badges and one-tap yes/no/always, and the ledger "what your machine did" section with permalinks; the rules-manager settings card shows/revokes rules with the send-capable graduation toggle; the Morning-triage template pack card installs an editable trigger; triage flow validated as-a-user incl. quiet-hours deferral, gateway-restart reply idempotency, and rule revocation (criteria 1/3/4/9/10) |
| `PA-6` | ⬜ | Decision Journal view + calibration strip FE + validation (Session 5b) | `PA-4` | The filtered knowledge Decision-Journal view shows pending (horizon countdown + overdue flag), resolved (expectation-vs-outcome side-by-side + linked lesson chip), and a per-domain calibration strip computed from knowledge.db alone (count-caveat under n=10, no LLM/new store); too_early defers at most twice then shows stale-pending; grep-audit confirms neither store writes the other (criteria 5/6/7/8) |

## Atom scopes

### `PA-1` — Approval memory + ProactiveConfig foundation (Session 1)

**Status:** done

§1.4 Approval memory; Provider & Config Plug-in Map (`approval` MemoryKind, ProactiveConfig); §4 tools (triage_rules); Implementation Effort Session 1

**Done when:** `user.approval.` prefix resolves to an `approval` MemoryKind in `_kind_from_key` and is excluded from `_NON_FACT_KEY_CLAUSE`; deterministic most-specific/deny-wins rule matcher, reply-grammar parser, and 24h/7d/30d suppression cooldowns are unit-tested pure functions; `ProactiveConfig` round-trips through the 5-point wiring (test_config_roundtrip green); `triage_rules` tool lists/adds/revokes rules with provenance

**DONE.** `user.approval.` → `MemoryKind.APPROVAL` in `_kind_from_key`, excluded from
`_NON_FACT_KEY_CLAUSE` (asserted in both directions: the rule stays out of the fact block, an
ordinary fact next to it still renders), with the kind mapped in `_DECAY_PROFILES`,
`_DEFAULT_TIER` and `decay.KIND_MULTIPLIERS` (0.4 — a taught rule is a standing instruction).
`personalclaw/proactive/approval.py` holds the pure half: a segment-prefix matcher where **any**
matching deny wins, then most-specific approve, then an active cooldown, then `NO_DECISION`
(ties break on `(pattern, key)` ascending, so the rule a ledger row NAMES is stable); the reply
grammar (`3 yes` / `always no 4` / `yes all`), which refuses rather than interprets — no path
from a malformed reply to an approval, `always yes all` included; and the 24h → 7d → 30d
suppression ladder, clamped, cleared by one acceptance. `ProactiveConfig` rides all five wiring
points (both switches fail CLOSED, the classifier gate fails OPEN). `triage_rules`
(list/add/revoke) over three `/api/memory/approval-rules` routes carries hit_count +
`created_from_digest` provenance and surfaces unreadable rows instead of dropping them silently.
FE deferred to `PA-5` (§5.2 rules manager), matching the `evals` section's precedent.

### `PA-2` — Triage pipeline: collect → classifier gate → tiered strict-JSON proposals → rank → deliver + Morning-triage template (Session 2)

**Status:** todo

§1.1 Collect; §1.2 Classifier gate; §1.3 Tiered strict-JSON proposals; §1.5 Rank + deliver; template pack (§1 intro / §5.4)

**Done when:** Firing the bundled "Morning triage" WorkflowDef collects inbox + channel + Run-Ledger items into a stable ordinal manifest, the classifier gate drops/surfaces per per-source NL rules (zero-item windows short-circuit before LLM spend), ONE strict-JSON call emits ≤8 tier-clamped proposals honoring the exact-ordinal-id contract, and a materiality-ranked digest is delivered through the notify gate as one normal WorkflowRun

### `PA-3` — Trivial-tier auto-execution + `inbox-op` action provider (Session 3)

**Status:** todo

§1.6 Trivial-tier auto-execution — guardrails; `inbox-op` provider (§1.6 + Plug-in Map); Success Criteria 2/3/4

**Done when:** `inbox-op` implements ActionProvider, is registered via register_action_provider, added to ALLOWED_HOOK_PROVIDERS, and carries a settings-schema manifest; trivial/always-approve proposals auto-execute bounded by the NEW-1 budget floor + max_auto_actions_per_run cap, each emitting a named-rule ledger row with one-click undo, and budget breach demotes remaining proposals to pending with skipped_budget rows; adversarial injection test (criterion 2) passes

### `PA-4` — Decision journal core: `decision` native type + tools + horizon triggers + R18 lesson (Session 4)

**Status:** todo

§2.1 Data model; §2.2 log_decision; §2.3 Horizon-triggered review + outcome capture; §2.4 Lesson distillation via LEARN-R18; §4 tools

**Done when:** `decision` is the 13th NATIVE_TYPES entry riding the Passthrough graph; log_decision/decision_list/decision_resolve tools exist; log_decision creates a searchable/@-pickable knowledge item and mints exactly one one-shot clock trigger with a deterministic `system:decision-journal:<id>`; the decision-review WorkflowDef captures the outcome, sets status=resolved, and writes a `lesson.*` memory row via write_lesson citing expectation-vs-outcome, linked only by soft reference (criterion 5)

**PARTIAL (2026-08-25) — four of five clauses met; the fifth is met in a DIFFERENT PLACE than
the criterion names, deliberately, and needs an owner ruling.** Recon first: `git grep -l
"decision_journal\|DecisionJournal\|log_decision" -- src/ tests/` returned NOTHING on `main`
@712a4233, so this was genuinely unbuilt rather than shipped under another name.

`personalclaw/decisions.py` is the core. `proactive.decision_default_horizon_days` was **already
wired end to end** by `PA-1` (dataclass + `_meta` + `load()` + `asdict` in `to_dict`), so no
config work was needed and none was done.

**MET — the type, registered end to end.** `decision` is the 13th `NATIVE_TYPES` entry
(`knowledge_providers/native`), in `_KNOWLEDGE_TYPES` (`dashboard/handlers/knowledge.py`), and
mapped EXPLICITLY into `graphs._TEXT_TYPES` → `PassthroughGraph`. The explicit map matters:
`graph_for` falls back to `DocumentGraph` for anything it does not know, so an unlisted decision
would have routed through the document reader and *degraded to its raw content* — a result that
looks like Passthrough without being it. Structured fields ride the item's metadata JSON
(`file_metadata["decision"]`), so `_migrate` is untouched and no column was added.

**Deliberately NOT authorable through the generic create.** `decision` is absent from
`_AUTHORABLE_TYPES` for the same reason a media type is, with a different missing half: logging a
decision also mints its review, so an item authored through `POST /api/knowledge/items` would be
a decision that never comes back. The existing refusal message hard-coded "uploading a file to
/ingest", which would have sent the caller to a door that cannot make one — replaced with a
`_CREATION_PATH` map naming `log_decision`.

**MET — the three tools, with real call sites.** `log_decision` / `decision_list` /
`decision_resolve` in `agents/native/builtin_tools.py`, in the `knowledge` category, so they ride
`personalclaw-knowledge-tools`: a decision IS a knowledge item and the journal must not be
removable independently of the library its entries live in. Their `domain`/`grade` enums are read
from `decisions.py` at schema-build time (the `_structural_verbs` idiom) rather than copied, so a
tool cannot advertise a value the module rejects.

**MET — exactly one one-shot at a deterministic id.** `system:decision-journal:<item_id>`, minted
by building the `Trigger` and `store.upsert`ing it (the `selfqa.install` pattern) rather than
through `triggers.tools.create`, which runs `_unique_id(slug_for(...))` and would append `-2` on
every reschedule. Asserted by COUNTING rows over the whole store, not by looking the id up — a
generated slug also satisfies a by-id lookup of the row it just made. `delete_after_run: True`,
`delivery: inbox`, capabilities frozen via `screen.capabilities_for_action`, armed at creation
(an unarmed clock row is never surfaced by `service.due_ids`).

**MET — the R18 lesson and its soft reference.** `resolve_decision` writes the
expectation-vs-outcome lesson through `MemoryService.write_lesson(category="decision")` and stamps
`lesson_memory_key`. The key is **read back out of the memory store** rather than re-derived:
`lesson.<md5-12>` lives in `vector_memory.write_lesson`, and copying it here would be a second
spelling of that contract AND wrong whenever dedup lets an existing longer lesson win. The lesson
is written LAST so an unavailable memory store cannot lose the outcome the user typed — in which
case `lesson_memory_key` stays null and says so. Measured while writing the test: a lesson's
`value_json` is the rule text encoded directly (`'"…"'`), not `{"rule": …}`.

**UNMET — the `decision-review` WorkflowDef does NOT capture the outcome.** It ships
(`workflows/bundled/decision-review/`, strict-validation clean, in the `EXPECTED` ratchet) and
delivers the review card, quoting the stated expectation + confidence back through a `notify`
action with zero model calls. **The capture, the `status=resolved` write and the lesson happen in
`decision_resolve`, not in the workflow.** That is a judgment call, not an omission: the horizon
fires with nobody present, and the only in-workflow mechanisms that could "capture" an outcome
there are a `stage` (a model inventing what happened) or `invoke-agent` (an unattended session
inventing what happened). Either would put a fabricated outcome into the knowledge item and a
fabricated lesson into long-term memory. So the workflow delivers and the user's answer resolves.
**Owner ruling wanted:** either re-word the clause to match this split, or specify the `to-chat`
linked-session mechanism §2.3 gestures at — which does not exist yet and is not this atom's scope.

**UNMET — no frontend.** That is `PA-6` (journal view + calibration strip), and `web/`'s
`knowledgeMeta.ts` type map has no `decision` entry, so a decision currently renders with the
library's fallback icon/label. `calibration()` already computes the strip
(count-honest under n=10, one pass over knowledge.db, no LLM, no new store) so `PA-6` is FE-only.

**Beyond the criterion, because the lifecycle is incoherent without them:** `reschedule_review`
(re-points the one row), `abandon_decision` (retires the reminder, writes NO lesson — an abandoned
decision has no outcome, and distilling one would be a fabricated verdict), the `too_early`
deferral cap (+50% of the original span, at most twice, then stale-pending with no trigger), and
`list_decisions(status="overdue")` derived at read time so a fired-and-deleted one-shot needs no
stored status.

**Gate:** `make lint` clean (black/isort/flake8/mypy, 1013 files). `tests/test_decision_journal.py`
51 passed. Regression set — `test_workflows_bundled` · `test_knowledge_typed_items` ·
`test_native_builtin_tools` · `test_native_tool_categories` · `test_native_builtin_split` ·
`test_config_roundtrip` · `test_workflows_autonomy` · `test_tool_groups` — 603 passed, 1 xfailed,
after updating the two inventory ratchets a new template and three new tools necessarily move
(`test_workflows_bundled.EXPECTED`, `test_native_builtin_split`'s knowledge-provider tool set).
Real-home rail clean on every run.

**Falsified, not asserted.** Two live-line mutations, each grepped back to confirm it applied,
then restored from a file copy: dropping `enqueue=_enrich_in_background` from `_t_log_decision`
reddened `test_log_decision_reaches_the_journal_with_ingestion_wired` (`KeyError: 'enqueue'`), and
dropping `"decision"` from `graphs._TEXT_TYPES` reddened
`test_a_decision_rides_the_passthrough_graph`. So the call site and the graph registration are
both genuinely pinned rather than restated.

### `PA-5` — Triage FE surfaces + as-a-user validation (Session 5a)

**Status:** todo

§5.1 Digest card; §5.2 Triage rules manager; §5.4 Template pack card; Success Criteria 1/3/4/9/10

**Done when:** Digest card renders auto-done+undo, pending proposals with tier badges and one-tap yes/no/always, and the ledger "what your machine did" section with permalinks; the rules-manager settings card shows/revokes rules with the send-capable graduation toggle; the Morning-triage template pack card installs an editable trigger; triage flow validated as-a-user incl. quiet-hours deferral, gateway-restart reply idempotency, and rule revocation (criteria 1/3/4/9/10)

### `PA-6` — Decision Journal view + calibration strip FE + validation (Session 5b)

**Status:** todo

§2.5 Calibration record; §5.3 Decision Journal view; Success Criteria 5/6/7/8

**Done when:** The filtered knowledge Decision-Journal view shows pending (horizon countdown + overdue flag), resolved (expectation-vs-outcome side-by-side + linked lesson chip), and a per-domain calibration strip computed from knowledge.db alone (count-caveat under n=10, no LLM/new store); too_early defers at most twice then shows stale-pending; grep-audit confirms neither store writes the other (criteria 5/6/7/8)

