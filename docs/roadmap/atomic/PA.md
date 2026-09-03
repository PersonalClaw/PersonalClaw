# PROACTIVE-ASSISTANT — atomic plans

**Source plan:** [`PROACTIVE-ASSISTANT`](../plans/PROACTIVE-ASSISTANT.md)  
**Code:** `PA`  
**Source status:** proposed

6 atoms: 6 done. Triage flagship = PA-1 (approval memory + config foundation, independently landable) → PA-2 (5-stage digest pipeline) → PA-3 (inbox-op action provider + budgeted auto-execution) → PA-5 (triage FE + validation). Decision journal = PA-4 (native type + tools + horizon triggers + R18 lesson) → PA-6 (journal view + calibration FE). Cross-plan deps (substrate/guardrails/flywheel/inbox-notif) are already DONE/shipped enough to unblock, but recorded as EXT edges.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PA-1` | ✅ | Approval memory + ProactiveConfig foundation (Session 1) | — | `user.approval.` prefix resolves to an `approval` MemoryKind in `_kind_from_key` and is excluded from `_NON_FACT_KEY_CLAUSE`; deterministic most-specific/deny-wins rule matcher, reply-grammar parser, and 24h/7d/30d suppression cooldowns are unit-tested pure functions; `ProactiveConfig` round-trips through the 5-point wiring (test_config_roundtrip green); `triage_rules` tool lists/adds/revokes rules with provenance |
| `PA-2` | ✅ | Triage pipeline: collect → classifier gate → tiered strict-JSON proposals → rank → deliver + Morning-triage template (Session 2) | `PA-1`, `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:Run Ledger materiality rows (AUTO-R2) + delivery contract + fire→spawn classifier machinery`, `EXT:AUTONOMY-GUARDRAILS:NEW-2 typed structured-output (output_type) for the proposal schema`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:notify gate + notification-kind registry the digest delivers through` | Firing the bundled "Morning triage" WorkflowDef collects inbox + channel + Run-Ledger items into a stable ordinal manifest, the classifier gate drops/surfaces per per-source NL rules (zero-item windows short-circuit before LLM spend), ONE strict-JSON call emits ≤8 tier-clamped proposals honoring the exact-ordinal-id contract, and a materiality-ranked digest is delivered through the notify gate as one normal WorkflowRun |
| `PA-3` | ✅ | Trivial-tier auto-execution + `inbox-op` action provider (Session 3) | `PA-2`, `EXT:AUTONOMY-GUARDRAILS:NEW-1 budget floor (per-run/day token+dollar+action ceilings) consulted before each auto-executed action` | `inbox-op` implements ActionProvider, is registered via register_action_provider, added to ALLOWED_HOOK_PROVIDERS, and carries a settings-schema manifest; trivial/always-approve proposals auto-execute bounded by the NEW-1 budget floor + max_auto_actions_per_run cap, each emitting a named-rule ledger row with one-click undo, and budget breach demotes remaining proposals to pending with skipped_budget rows; adversarial injection test (criterion 2) passes |
| `PA-4` | ✅ | Decision journal core: `decision` native type + tools + horizon triggers + R18 lesson (Session 4) | `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:one-shot clock/at trigger with delete_after_run (commitment-conversion pattern)`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:LEARN-R18 pending→resolved lesson lifecycle (write_lesson)` | `decision` is the 13th NATIVE_TYPES entry riding the Passthrough graph; log_decision/decision_list/decision_resolve tools exist; log_decision creates a searchable/@-pickable knowledge item and mints exactly one one-shot clock trigger with a deterministic `system:decision-journal:<id>`; the decision-review WorkflowDef delivers the horizon card, and `decision_resolve` captures the outcome, sets status=resolved, and writes a `lesson.*` memory row via write_lesson citing expectation-vs-outcome, linked only by soft reference (criterion 5) — the capture is deliberately NOT in-workflow, because the horizon fires with nobody present and an in-workflow stage could only invent an outcome (owner ruling 2026-08-26) |
| `PA-5` | ✅ | Triage FE surfaces + as-a-user validation (Session 5a) | `PA-2`, `PA-3` | Digest card renders auto-done+undo, pending proposals with tier badges and one-tap yes/no/always, and the ledger "what your machine did" section with permalinks; the rules-manager settings card shows/revokes rules with the send-capable graduation toggle; the Morning-triage template pack card installs an editable trigger; triage flow validated as-a-user incl. quiet-hours deferral, gateway-restart reply idempotency, and rule revocation (criteria 1/3/4/9/10) |
| `PA-6` | ✅ | Decision Journal view + calibration strip FE + validation (Session 5b) | `PA-4` | The filtered knowledge Decision-Journal view shows pending (horizon countdown + overdue flag), resolved (expectation-vs-outcome side-by-side + linked lesson chip), and a per-domain calibration strip computed from knowledge.db alone (count-caveat under n=10, no LLM/new store); too_early defers at most twice then shows stale-pending; grep-audit confirms neither store writes the other (criteria 5/6/7/8) |

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

**Status:** done

§1.6 Trivial-tier auto-execution — guardrails; `inbox-op` provider (§1.6 + Plug-in Map); Success Criteria 2/3/4

**Done when:** `inbox-op` implements ActionProvider, is registered via register_action_provider, added to ALLOWED_HOOK_PROVIDERS, and carries a settings-schema manifest; trivial/always-approve proposals auto-execute bounded by the NEW-1 budget floor + max_auto_actions_per_run cap, each emitting a named-rule ledger row with one-click undo, and budget breach demotes remaining proposals to pending with skipped_budget rows; adversarial injection test (criterion 2) passes

### `PA-4` — Decision journal core: `decision` native type + tools + horizon triggers + R18 lesson (Session 4)

**Status:** todo

§2.1 Data model; §2.2 log_decision; §2.3 Horizon-triggered review + outcome capture; §2.4 Lesson distillation via LEARN-R18; §4 tools

**Done when:** `decision` is the 13th NATIVE_TYPES entry riding the Passthrough graph; log_decision/decision_list/decision_resolve tools exist; log_decision creates a searchable/@-pickable knowledge item and mints exactly one one-shot clock trigger with a deterministic `system:decision-journal:<id>`; the decision-review WorkflowDef delivers the horizon card, and `decision_resolve` captures the outcome, sets status=resolved, and writes a `lesson.*` memory row via write_lesson citing expectation-vs-outcome, linked only by soft reference (criterion 5) — the capture is deliberately NOT in-workflow, because the horizon fires with nobody present and an in-workflow stage could only invent an outcome (owner ruling 2026-08-26)

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

**Status:** 🟡 code complete (2026-08-27)

§2.5 Calibration record; §5.3 Decision Journal view; Success Criteria 5/6/7/8

**Done when:** The filtered knowledge Decision-Journal view shows pending (horizon countdown + overdue flag), resolved (expectation-vs-outcome side-by-side + linked lesson chip), and a per-domain calibration strip computed from knowledge.db alone (count-caveat under n=10, no LLM/new store); too_early defers at most twice then shows stale-pending; grep-audit confirms neither store writes the other (criteria 5/6/7/8)

**DISCOVERY — this atom was NOT frontend-only, and `PA-4`'s log was wrong about it.** That log
recorded *"`calibration()` already computes the strip so `PA-6` is FE-only"*. `calibration()` does
compute the strip, but `decision_list`/`decision_resolve` are **chat tools**: `git grep` found the
three tool handlers in `agents/native/builtin_tools.py` and **zero** HTTP routes, so a browser had
no way to read a decision at all. `handlers/learning.py`'s `calibration` is judge-calibration
(`workflows/judge_calibration.calibration_summary`) — a different subject entirely. So one read
route was built: `GET /api/knowledge/decisions`.

**One route, not two, and one payload.** The strip is an aggregate of the rows beside it, so
separate endpoints would let a client render eleven resolved decisions above a rate computed from
ten — two answers to one question, from two fetches that raced. Both reads happen in ONE thread hop
against one store handle. The handler *forwards* `list_decisions`/`calibration` and computes
nothing; `test_the_payload_is_the_owning_modules_own_answer` asserts the payload equals
`calibration(store=…)` and `list_decisions(store=…)` **by value**, which is the assertion a handler
that re-aggregated could not satisfy forever. `CALIBRATION_MIN_N` was named in `decisions.py` (it
was a bare `min_n: int = 10` default) so the threshold the view quotes has exactly one spelling.

**Under `/api/knowledge/` and not `/api/proactive/`,** because §5.3 says a decision IS a knowledge
item and the view is a lens on the library — the path is the IA. The handler is a NEW module
(`handlers/decisions.py`, 111 lines) because `handlers/knowledge.py` is a **shrink-only
watch-band member** at 3666 lines (`structural-baseline.json`), so adding routes there would have
reddened the size ratchet. `config/loader.py` untouched: 5900 → 5900, headroom still exactly 100.

**Three calibration states, and the honesty rule is the substance of the atom.** `'calibrated'`
(some domain at or over the threshold) · `'too-few'` (resolved decisions exist, every domain under
it) · `'no-data'` (nothing resolved carries a calibratable grade). Below the threshold the strip
renders the COUNT and its distance from the threshold and **draws no bar at all** — a 0%-width
track is a lie in the shape of a chart, visually identical to "0% as expected", which sits next to
"flawless". `as_expected_rate` is present in the payload precisely so the view can decline to draw
it. Same rule as `learningMeta.evidenceLabel`'s `ungraded` (ES-7) and `optimize.SCORE_UNSCORED`
(ES-11), and applied twice more: an unknown `outcome_grade` reads `ungraded` and never falls
through to `as_expected` (the tempting default, and the worst one — it turns "nobody said" into the
claim that the user called it right), and a null confidence reads "no stated confidence", never 0%.

**The discrimination leg is not a count.** `expect(new Set(said).size).toBe(3)` over the three
rendered captions, plus a per-state assertion that each names its own condition. A count of states
would pass while two of them rendered identically — which is the actual bug, because the user would
read one sentence for two different truths. The wire has the same leg:
`test_the_three_states_are_three_DIFFERENT_payloads` serializes the strip at 0 / 3 / 10 resolved
and asserts three distinct payloads.

**Two DIFFERENT reds, from live-line mutations grepped back and restored from file copies.**
Collapsing `calibrationState`'s `'too-few'` onto `'calibrated'` → **3 vitest failures** including
the discrimination leg (`expected 'Calibration across 0 domains with at …' to match /too few to
mean much/` — the collapse also produced a nonsense sentence). Neutralising the handler's
`calibration()` read to `{}` → **6 pytest failures** in a different suite
(`two states serialize identically: ['{}', '{}', '{}']`). Disjoint sets, so the FE state machine
and the backend read are each pinned separately.

**🔴 Two defects caught only by driving it, neither visible to any unit test written first.**
(1) A stale-pending decision's horizon is usually in the **future**: each `too_early` deferral
pushes it out by half the original span, so the row goes stale on the deferral COUNT while its date
still sits ahead — measured live as `deferrals=2, stale_pending=True, review_horizon=2026-11-02`.
The label read the sign off `Math.abs` and announced "Review lapsed 67 days ago" for a horizon 67
days away. It now states the fact true in both directions (no reminder is coming) and calls the
date lapsed only when it has lapsed. (2) The resolved row rendered `outcome_grade` verbatim, so the
screen read `as_expected` — a raw wire token in the one place the user is told what their own
judgement was worth.

**Validated as a user** against an isolated `PERSONALCLAW_HOME=./.dev-home` on :10126, driving a
real browser through all three states with real persisted rows: `no-data` ("1 decision still open
and none resolved yet"), `too-few` (3 resolved across 3 domains, **0 bars drawn**, per-domain "1 of
10 decisions — too few to mean much"), `calibrated` (career at n=10, **exactly 1 bar**, the two
under-threshold domains still barless beside it). Both non-counting pending states rendered on one
screen — the stale one and an overdue one. Zero console errors on every pass.

**PARTIAL (2026-08-27) — the in-gateway chat-tool round trip was NOT driven.** `log_decision` /
`decision_resolve` reach the journal only through a chat turn, and the dev home has no model
provider configured, so decisions were created and resolved by calling the same `decisions` module
functions out-of-process. Two consequences, recorded rather than papered over: the *rendering* of
every clause above is validated against real persisted data, but the **tool → journal path is not
re-validated here** (it is `PA-4`'s surface, already `done` with its own tests). And a DISCOVERY
the driver should know: **a running gateway does not see another process's write to
`knowledge.db`** — its cached store handle kept serving `status: pending` for a row the DB had
recorded as `resolved`, so each seeding step needed a gateway restart. Whether that is a snapshot
artifact of the long-lived connection or something a real out-of-process writer (a workflow
subprocess) would hit is **not** established here and is not this atom's scope.

