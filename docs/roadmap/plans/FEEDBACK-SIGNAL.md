# Plan: Feedback Signal — The Thumbs That Actually Teach

**Status:** DONE — S1 (store + routes + SDK + config + producer meta), S2 (thumbs on inbox/loops) and
S3 (thresholds + retire proposals + Settings → AI feedback) all shipped 2026-07-27 and verified on
`main`: `feedback.py` + 5 routes + `sdk/feedback.py` + `FeedbackThumbs.tsx`, with
`check_retire_candidates` called from `inbox_service.py`'s maintenance tick and `FeedbackPanel`
registered in both `SettingsPage` and the bento grid.
🔴 **REMAINING — a shipped control lost its only consumer.** WORKFLOWS-V2 Phase 1 deleted
`workflows.surfacing.eligible_workflows`, which was T3.1's one gated consumer of
`suppressed_producers()`. Verified 2026-08-04: **no runtime path withholds a suppressed producer
today** — the set is captured, thresholded, proposed and displayed, but never enforced. Re-add a gated
consumer at a WF2 slice or in the flywheel skill work. T2.4 (the app-path fixture) and T3.4 (the
plan-56 double-write, which shipped inside AGENT-ROUTING instead) remain unwired by design.
Status corrected 2026-08-04 by code audit. Created 2026-07-26 (roadmap rev 13)

---

## Context (code recon, 2026-07-26)

### What produces AI judgments today (the nameable producers)
- **Inbox classify/draft/digest** (`src/personalclaw/inbox_service.py`): `classify()` (:287) runs `render_use_case_prompt("inbox_classify", …)` → `_parse_classification` → persists `classification`/`confidence` on the item; `draft_reply()` (:321) is use-case `inbox_draft`; `generate_digest()` (:364) is `inbox_digest` (creates a `source="digest"` item). All three are one-shot LLM jobs whose producing artifact IS the bound prompt: `prompt_providers/runtime.py::render_use_case_prompt` resolves via `active_prompt_ref(use_case)` (`providers/prompt_use_cases.py:129`) — so `("prompt", active_prompt_ref("inbox_classify"))` names the exact rebindable artifact. Items persist in `InboxStore` (`inbox.py:196`, `inbox.json`) with `update(item_id, **kwargs)`; `_UPDATABLE_FIELDS` in `dashboard/handlers_inbox.py:21` allowlists PATCHable fields (`favorited` is the P11 precedent for a user-signal field flowing through it). Note: a user *editing then sending* a draft is already an implicit signal — out of scope for v1 (explicit thumbs only), recorded as an open question.
- **Loop judge/findings** (`src/personalclaw/loop/judge.py`): `assess_cycle` (:189) emits a `CycleVerdict` (:31 — done/marginal_value/quality_score/regressed/reasoning); findings persist per-loop under `loops/<id>/findings/` (`loop/store.py:70`; `get_findings`, `redact_finding` :105). Producer name: `("loop_judge", loop.kind)` — per-kind, since each kind carries its own brief/rubric. Findings render in `web/src/pages/loops/LoopsListPage.tsx` (latest-finding rows :195, :278) and the cockpit.
- **Workflow/SOP surfacing** (`src/personalclaw/workflows/surfacing.py`): `best_match` (:160) over `match_text` (keyword 0.7 / cosine 0.62) decides what surfaces per turn. Producer: `("workflow_surfacing", workflow_id)`. Skills surfacing is the sibling (`skills/surfacing.py::surface_skills`, 0.55 threshold; usage counted by `skills/usage.py::SkillUsageStore.record_use(s)` :90/:105 — usage ≠ verdict; feedback adds the verdict axis).
- **Routing suggestions** (plan 56 AGENT-ROUTING, DESIGNED): `agents/routing.py` will emit `RouteCandidate` chips; its suppression store is `entity_settings/agent_routing.json` (dismiss counters + mute — a *frequency* control, not an accuracy record). Producer: `("routing_pair", f"{default_agent}->{candidate}")`. Its SEL `agents.routing_suggest` outcomes (`suggested|accepted|dismissed`) are exactly a feedback record shape — T3.4 double-writes so both plans share one accuracy record.
- **Proposal contents** (`src/personalclaw/skills/proposals.py`): `enqueue/list_pending/accept/reject` — accept/reject is already a verdict on the *synthesizer*; producer `("skill_synthesis", "ladder")`. Plan 42 S4 folds proposals into the inbox as `kind="proposal"` items; this plan's thumbs on proposal cards must not fork that — accept/reject remain the actions; thumbs appear only on *content within* judgment cards elsewhere.

### The seams this plan builds on (verified)
- **Storage conventions** (INTEGRATION-ARCHITECTURE §2.4): `config_dir()`, `atomic_write` (`atomic_write.py:29`), append-only JSONL trimmed at 2× cap (the `notifications.jsonl` / SEL pattern — `sel.py:12`). New JSONL must be added to `portability.py`'s export tree (:125 — the LEARN plan's recon caught this same gotcha).
- **Entity settings**: `providers/entity_routes.py::_load_entity_settings/_save_entity_settings` (:31/:42) — where threshold-policy user preferences live, per §2.1 (not config.json).
- **SEL** (§2.3): `sel().log_api_access(caller, operation, outcome, …)` (`sel.py:254`) — feedback actions are security-relevant (they change what surfaces).
- **App boundary**: apps import core only via `personalclaw.sdk.*` (`sdk/__init__.py`); app-scoped tokens set `request["app"]` (`apps/permissions.py`) and the permission middleware 403s undeclared paths — so the app path to `record_feedback` is one declared API route, with `source_app` stamped server-side from `request["app"]` (never client-claimed).
- **WS + UI**: `state.broadcast_ws` (`dashboard/state.py:1644`); inbox rows/detail render classification + draft in `web/src/pages/inbox/InboxPage.tsx` (:202-220) and `InboxDetail.tsx` (verdict block :91); `MessageActions.tsx` deliberately has "no decorative thumbs" (:8) — **chat messages are NOT a target surface** (chat replies aren't discrete judgments; the after-turn-review path owns that signal).
- **Gap, precisely:** there is no feedback primitive anywhere — no store, no SDK call, no per-producer accuracy math, no wrong-producer consequence. Verdict-shaped signals exist in fragments (proposal accept/reject, routing dismiss, `favorited`) with no common record or attribution key.

## Design

Three layers, strictly separated so each stays cheap and auditable:

- **Layer 1 — CAPTURE (S1).** A `FeedbackRecord` (target kind+id, verdict, optional reason, a snapshot of the judgment as shown, and a **provenance pointer to the producing artifact** — the bound prompt ref, skill slug, workflow id, routing pair, or judge kind) appended to `~/.personalclaw/feedback.jsonl` (0600, atomic, 2×-cap trim). One idempotent write API — `record_feedback(...)` — used identically by core surfaces (direct call) and apps (the `/api/feedback` route through the scoped-token path, `source_app` stamped server-side). Re-thumbing the same target supersedes (last-verdict-wins by `(target_kind, target_id)`; the old record stays in the JSONL for audit, the index tracks current). **Deterministic, no LLM, no network.** 👍 records verdict `up` and STOPS — silent-positive: it exists only so accuracy has a denominator. 👎 optionally carries a short free-text "why" (≤500 chars, stored verbatim, `redact()`-ed on any render since it's user text destined for future prompts).
- **Layer 2 — ATTRIBUTION (S1 backend, S3 API).** Per-producer rolling accuracy is a **pure GROUP BY** over records: `accuracy = ups / (ups + downs)` per `(producer_kind, producer_id)` over a rolling window (default 90 days, min-N 5 before any number is shown or acted on). No new math, no scores stored — recomputed from the JSONL through a small in-process cache (invalidated on write). Producer naming is the load-bearing design decision, fixed in C1's closed vocabulary: `prompt:<use_case_ref>`, `loop_judge:<kind>`, `workflow_surfacing:<id>`, `skill_synthesis:ladder`, `routing_pair:<from>-><to>`, `app:<name>:<producer>` (apps namespace their own).
- **Layer 3 — LEARNING, deterministic arm ONLY (S3).** Threshold policies over per-producer accuracy — pure counting: (a) a producer below `retire_threshold` (default 0.4) with ≥ `min_n` (default 5) verdicts **stops surfacing** where a surfacing gate exists (workflow/skill surfacing consults a suppression check) and (b) emits a one-time **"retire this rule?" proposal** (propose-don't-write: pre-plan-42 a `notify(kind="feedback_retire")`; post-42 an `emit_attention_item(kind="proposal")` — the emit site is one function, swapped when 42 lands). The user accepts (producer muted until edited) or dismisses (threshold check snoozes 30 days for that producer). Producers with no surfacing gate (inbox prompts, the judge) get the proposal only — "your inbox classifier is wrong 4 times out of 6 — review its prompt binding?" with a deep link to Settings → Prompts.
- **Layer 3 — interpretive arm: NOT THIS PLAN.** The periodic background model call that reads 👎 reasons and drafts lesson/prompt-amendment proposals into the proposals queue is LEARNING-FLYWHEEL work (its §2.2 proposal queue + decision memory is exactly the landing zone). This plan's store is designed to feed it — `reason` is stored verbatim + fenced-on-render, records carry the producer pointer — and this plan contributes **one task suggestion to that plan**: *"Flywheel step: a `feedback_digestor` detector that batches ≥N 👎-with-reason records per producer and enqueues a prompt-amendment proposal (fenced excerpts, standard queue, standard decision memory)."* A coordination note is added to WORKFLOWS-V2-LEARNING-FLYWHEEL §3.2's detector list; nothing interpretive ships here.
- **The FE affordance (S2).** A small, quiet thumbs pair (👍/👎) on qualifying cards — non-modal, hover/focus-revealed on desktop, always-visible compact on touch: inbox item verdict block (classification), the draft-reply block (draft), digest items, loop finding rows, and (when plan 56 ships) the routing chip's dismiss double-writes. 👎 opens a one-line optional "why?" popover (skippable — Enter or click-away records without a reason). State is reflected (a filled thumb) and reversible. **Quality/analytics surfaces belong elsewhere**: LEARNING-VISIBILITY owns the "is it learning?" panels; EVALUATION-SUBSTRATE owns field-metric studies. This plan ships only the capture affordance + a minimal per-producer accuracy table under Settings → AI (honest counts, min-N gated).

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — FeedbackRecord + store (`src/personalclaw/feedback.py`, new)
```python
@dataclass(frozen=True)
class FeedbackRecord:
    id: str                 # fb_<uuid8>
    created_at: float
    target_kind: str        # "inbox_classification" | "inbox_draft" | "inbox_digest"
                            # | "loop_finding" | "routing_suggestion" | "proposal_content"
                            # | "app_judgment" (apps) — closed, append-only vocabulary
    target_id: str          # the judged thing (inbox item id, finding path, suggestion id…)
    verdict: str            # "up" | "down"
    reason: str = ""        # 👎 only; ≤500 chars; stored verbatim, redact()-ed on render
    snapshot: dict = field(default_factory=dict)   # the judgment AS SHOWN (e.g. {"classification":
                            # "needs_reply", "confidence": "high"}) — so accuracy survives later edits
    producer_kind: str = "" # "prompt" | "loop_judge" | "workflow_surfacing"
                            # | "skill_synthesis" | "routing_pair" | "app"
    producer_id: str = ""   # active_prompt_ref(use_case) / loop kind / workflow id / "a->b" / app producer
    source_app: str = ""    # stamped server-side from request["app"]; "" = core
    session_key: str = ""   # optional provenance

def record_feedback(*, target_kind, target_id, verdict, reason="", snapshot=None,
                    producer_kind, producer_id, source_app="", session_key="") -> FeedbackRecord: ...
    # append to feedback.jsonl (atomic, 0600, 2x-cap trim per §2.4); supersede-by-target in the
    # in-memory index; SEL log; broadcast_ws("feedback_recorded", …). Never raises to callers
    # (log + return best-effort) — feedback must never break the surface hosting it.
def current_verdict(target_kind, target_id) -> FeedbackRecord | None: ...
def producer_stats(*, window_days=90) -> dict[tuple[str, str], dict]: ...
    # {(producer_kind, producer_id): {"ups": int, "downs": int, "n": int, "accuracy": float}}
    # pure GROUP BY over non-superseded records in window; cached, invalidated on write
```
Storage: `~/.personalclaw/feedback.jsonl` (append-only; superseded records kept for audit) + added to `portability.py`'s export tree. Tolerant reads: corrupt/missing lines skipped with a warning (fail OPEN — availability surface per §2.7).

### C2 — Deterministic thresholds (`feedback.py` + `entity_settings/feedback.json`)
```python
def suppressed_producers(*, threshold=None, min_n=None) -> set[tuple[str, str]]: ...
    # producers with accuracy < threshold and n >= min_n, minus snoozed/user-cleared —
    # consulted by workflow/skill surfacing (one `in suppressed_producers()` check at the
    # existing eligibility filters); everything else gets the proposal only
def check_retire_candidates() -> list[dict]: ...
    # ran on the existing inbox-service maintenance tick (inbox_service.py:_MAINTENANCE_EVERY_SECS
    # — no new loop); emits ONE retire proposal per producer per crossing (dedup by producer
    # in feedback_state.json); pre-42: notify("feedback_retire", …); post-42: emit_attention_item
```
```json
// entity_settings/feedback.json (per §2.1 — user preference, not operator config)
{"snoozed": {"prompt:native/inbox_classify": 1753500000.0},
 "cleared": ["workflow_surfacing:wf_abc"], "retire_proposed": ["loop_judge:code"]}
```

### C3 — Routes + SDK + WS (§2.2 error envelope; SDK export is Tier-S per §2.8)
```python
POST /api/feedback            {target_kind, target_id, verdict, reason?, snapshot?,
                               producer_kind, producer_id}   # source_app stamped server-side
GET  /api/feedback/target/{kind}/{id}                        # current verdict (FE hydration)
GET  /api/feedback/producers?window_days=90                  # producer_stats (min-N filtered)
POST /api/feedback/producers/snooze   {producer_kind, producer_id}   # 30-day snooze
POST /api/feedback/producers/clear    {producer_kind, producer_id}   # un-suppress after edit
# WS: {"type": "feedback_recorded", "data": {"target_kind", "target_id", "verdict"}}
# SDK (new src/personalclaw/sdk/feedback.py — thin re-export of record_feedback + FeedbackRecord;
#   app producer names are forced to ("app", f"{app_name}:{producer}") in the route handler)
```

### C4 — Config (5-point wiring per §2.1; `tests/test_config_roundtrip.py` covers)
```python
@dataclass
class FeedbackConfig:
    enabled: bool = True            # kill-switch — thumbs never render, route 404s
    retire_threshold: float = 0.4   # accuracy below this ⇒ suppress/propose
    min_n: int = 5                  # verdicts before accuracy is shown or acted on
    window_days: int = 90           # rolling attribution window
# _EDITABLE_CONFIG: "feedback.enabled" {"type": "bool"},
#   "feedback.retire_threshold" {"type": "float", "min": 0.1, "max": 0.9},
#   "feedback.min_n" {"type": "int", "min": 3, "max": 50},
#   "feedback.window_days" {"type": "int", "min": 7, "max": 365}
```

### Integration points
- **Calls:** `atomic_write` (§3.1), `sel().log_api_access` (§2.3 — `operation="feedback.record"`, outcome=verdict), `state.broadcast_ws` (`state.py:1644`), `_load_entity_settings/_save_entity_settings` (`entity_routes.py:31/42`), `notify()` pre-42 / `emit_attention_item(kind="proposal")` post-42 (one swap site), `active_prompt_ref` (`prompt_use_cases.py:129` — producer naming at the inbox call sites), `redact()` (`security.py:686` — reasons on render).
- **Called by:** inbox handlers (thumbs on classification/draft/digest), loops UI (finding rows), plan 56's chip actions (double-write), app bundles via `sdk/feedback` + `/api/feedback`; `workflows/surfacing.py` + `skills/surfacing.py` consult `suppressed_producers()`; LEARNING-FLYWHEEL's future `feedback_digestor` reads the JSONL (read-only consumer); EVALUATION-SUBSTRATE reads `producer_stats` (read-only consumer).
- **Storage owned:** `feedback.jsonl`, `entity_settings/feedback.json` (+ `portability.py` export entry). Class B: plain clean break under the pre-1.0 banner.
- **Zero telemetry:** records never leave the instance; SEL is the only audit trail.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Store + SDK + SEL (Layer 1 + attribution math)

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `feedback.py`: `FeedbackRecord`, JSONL store (atomic, 0600, 2×-cap trim, tolerant reads), supersede-by-target index, `record_feedback` (never-raises, SEL log, WS broadcast), `current_verdict`, `producer_stats` GROUP BY + cache | `src/personalclaw/feedback.py` (new), `src/personalclaw/portability.py` (export entry) | round-trip test: record → supersede → stats reflect only current verdicts; corrupt line skipped with warning; trim at 2× cap verified |
| T1.2 | Routes: `POST /api/feedback` (+target/producers GETs, snooze/clear) with §2.2 envelopes; `source_app` stamped from `request["app"]`; producer names forced to `app:` namespace for app callers; `feedback.enabled` kill-switch | `src/personalclaw/dashboard/handlers/feedback.py` (new), `dashboard/server.py` (mount) | curl round-trip; an app-scoped token gets its producer forcibly namespaced (test); disabled config → 404 |
| T1.3 | `sdk/feedback.py` re-export (`record_feedback`, `FeedbackRecord`) + boundary-lint green; document in the SDK docstring that this is Tier-S | `src/personalclaw/sdk/feedback.py` (new) | `tests/test_apps_import_boundary.py` green; an app can import and call it |
| T1.4 | `FeedbackConfig` 5-point wiring + `_EDITABLE_CONFIG` entries | `config/loader.py`, `dashboard/handlers/core.py` | `test_config_roundtrip.py` green |
| T1.5 | Wire producer naming at the core call sites: inbox classify/draft/digest handlers pass `("prompt", active_prompt_ref("inbox_classify"/"inbox_draft"/"inbox_digest"))`; loop findings pass `("loop_judge", loop.kind)` — carried in the card payloads the FE already receives (additive meta, no event-shape change) | `dashboard/handlers_inbox.py`, loop view assembly (`loop/store.py:634` view path) | each qualifying card's API payload carries `{producer_kind, producer_id}`; snapshot fields present |
| V1 | Validation: seed records via curl for 3 producer kinds; `GET /api/feedback/producers` returns correct GROUP BY; SEL entries present; JSONL human-readable in the dev home | — | holds |

### Session 2 — FE affordance on core surfaces + app SDK path

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `FeedbackThumbs` primitive (ui/): quiet 👍/👎 pair, hover/focus-reveal desktop + compact touch, filled-state reflection, reversible, 👎 opens optional one-line "why" popover (skippable); `prefers-reduced-motion` honored; token-lint clean | `web/src/ui/FeedbackThumbs.tsx` (new + `.doc.ts`), `web/src/lib/api.ts` (3 calls) | component doc renders; thumb → POST → filled state → re-thumb supersedes; popover skippable |
| T2.2 | Mount on inbox surfaces: classification verdict block (`InboxDetail.tsx` :91 region), draft-reply block, digest rows (`InboxPage.tsx` row region) — each passing its card's producer meta + snapshot | `web/src/pages/inbox/InboxDetail.tsx`, `InboxPage.tsx` | thumbs on all 3 inbox judgment types; verdict hydrates on reopen (`GET /api/feedback/target/…`) |
| T2.3 | Mount on loop finding rows (`LoopsListPage.tsx` latest-finding rows + peek panel) with `("loop_judge", kind)` producers | `web/src/pages/loops/LoopsListPage.tsx` | a finding can be thumbed from list + peek; state consistent between the two |
| T2.4 | App path validation: a fixture app declares `/api/feedback` in `permissions.api`, records via `sdk/feedback` and via the route; undeclared app 403s | fixture app under `tests/`, permission tests | both paths land records with `source_app` set; 403 test green |
| V2 | Validation (as a user, dev home): classify an inbox item → 👎 with reason → reopen shows filled thumb → JSONL + SEL inspected; a11y pass (keyboard reach, focus ring, labels) | — | holds |

### Session 3 — Deterministic thresholds + retire proposals + accuracy API/table

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | `suppressed_producers()` + `entity_settings/feedback.json` (snoozed/cleared/retire_proposed; tolerant reads, fail-open); consult it in `workflows/surfacing.py` + `skills/surfacing.py` eligibility (one membership check each — no scoring change) | `feedback.py`, `workflows/surfacing.py`, `skills/surfacing.py` | a below-threshold workflow stops surfacing (fixture: 1 up / 5 downs); clearing restores it; corrupt settings file suppresses nothing (warn log) |
| T3.2 | `check_retire_candidates()` on the inbox-service maintenance tick: one-time proposal per crossing (dedup via `retire_proposed`), `notify("feedback_retire", …)` with the counts + a deep link (Settings → Prompts for prompt producers; the workflow/skill page otherwise); post-42 swap note left at the emit site | `feedback.py`, `inbox_service.py` (`run_maintenance` call), notification meta | crossing the threshold emits exactly one notification; re-tick no-ops; snooze suppresses for 30 days |
| T3.3 | Per-producer accuracy table under Settings → AI: producer, n, ups/downs, accuracy (min-N gated — below min_n shows "collecting"), suppressed badge, snooze/clear actions; honest counts only, no invented metrics | `web/src/pages/settings/` (new panel section), `api.ts` | table reflects fixture data; actions round-trip; nothing shown below min-N |
| T3.4 | Plan-56 double-write: routing chip Route→`("routing_pair", pair, verdict="up")`, dismiss→`"down"` (guarded — no-op if plan 56 unshipped; wired at its dismiss/accept handlers when present) + coordination note added to AGENT-ROUTING §Risks | `agents/routing.py` (if landed) or a coordination note in both plans | when both plans are live, routing accuracy appears in the producers API with zero extra UI |
| V3 | Validation: drive a producer below threshold as a user → proposal notification → snooze → clear cycle; verify the flywheel handoff shape (`reason` fields readable, producers stable) against LEARNING-FLYWHEEL §2.2's queue expectations | — | holds |

## Owner tasks (real world)
1. **Ratify the silent-positive rule in the UI copy** — 👍 must not imply "I'll learn from this"; proposed microcopy: tooltip "Mark accurate" / "Mark wrong (tell me why)".
2. **Tune `retire_threshold`/`min_n` after two weeks of dogfooding** on your real inbox volume — 0.4/5 is the proposed floor; your real 👎 rate decides.
3. **Approve the LEARNING-FLYWHEEL task suggestion** (the `feedback_digestor` detector) into that plan's §3.2 detector list — one line there, owned there.
4. **Decide whether digest items deserve thumbs at all** — a digest is a summary, not a judgment; included in v1 because its usefulness IS a judgment call, but cut is cheap if it reads as noise.

## Risks & open questions
- **Sparse-signal producers** — a personal instance may never reach min_n for most producers; the design degrades gracefully (no number shown, no action taken), but the retire arm may be near-dormant. Acceptable: the capture layer still feeds the flywheel's interpretive arm later.
- **Producer identity churn** — rebinding a prompt (Settings → Prompts) changes `active_prompt_ref`, resetting that producer's history. Correct behavior (a new prompt IS a new producer) but worth a UI note on the accuracy table ("history restarts when you rebind").
- **Thumbs fatigue / surface creep** — the affordance must stay quiet; the hard rule is *judgment outputs only* (never chat messages — `MessageActions.tsx`'s "no decorative thumbs" stance stands). Any new surface must name its producer in C1's closed vocabulary first.
- **Open:** should an *edited-then-sent* draft auto-record an implicit 👎-with-diff? Deferred — implicit signals belong to the flywheel's capture-hygiene machinery (LEARN-R5), not this plan; DISCOVERY-file if S2 dogfooding screams for it.
- **Open:** proposal-content thumbs vs accept/reject double-signal — v1 keeps thumbs OFF plan-42 proposal cards (accept/reject already is the verdict; T3.4-style double-write can map them later).

## Execution log

- [2026-07-27][S1] DONE: store + SDK + routes + config + producer meta (T1.1-T1.5). **T1.1** `src/personalclaw/feedback.py`: frozen `FeedbackRecord`, append-only `feedback.jsonl` (0600 on create, atomic trim to newest 5000 at 2×, tolerant reads — corrupt line skipped with warning), supersede-by-target in-process index (old records kept for audit), `record_feedback` (never-raises; SEL `feedback.record` with verdict as outcome; WS `feedback_recorded` — broadcast takes the handler's `state` explicitly, None skips), `current_verdict`, `producer_stats` pure GROUP BY over current verdicts in the rolling window. Portability: exported in the core-files list + restore-if-absent on import (merging two instances' verdict streams isn't meaningful — deliberate deviation from notifications' merge). **T1.2** `dashboard/handlers/feedback.py` + `server.py` mount: POST /api/feedback, target/producers GETs, snooze/clear POSTs, §2.2 envelopes, `feedback.enabled` kill-switch 404s all routes; app callers get `source_app` stamped from `request["app"]` AND the producer forced to `("app", "<app>:<producer>")`. **T1.3** `sdk/feedback.py` Tier-S re-export; boundary-lint green. **T1.4** `FeedbackConfig` (enabled/retire_threshold/min_n/window_days) through all 5 wiring points + 4 `_EDITABLE_CONFIG` entries. **T1.5** producer meta additive on payloads: inbox `_redact_item` attaches `feedback_producers` per judgment field (`("prompt", active_prompt_ref("inbox_classify"/"inbox_draft"/"inbox_digest"))`; digest keyed off `source == "digest"`); loop redacted views (detail + list) attach `feedback_producer = ("loop_judge", loop.kind)`.
- [2026-07-27][S2] DONE: FE affordance (T2.1-T2.3). **T2.1** `web/src/ui/FeedbackThumbs.tsx` (+`.doc.ts`, counted in ui-docs: 72 components): quiet pair, aria-pressed + filled-state reflection, hydrates from GET target on mount, optimistic + reversible, 👎 opens a skippable one-line "why" popover (Enter records with reason, click-away records without, Esc cancels), hides entirely when the kill-switch 404s. `api.ts`: 5 typed calls + the closed `FeedbackTargetKind` vocabulary. **T2.2** mounted on InboxDetail's verdict block (classification vs digest keyed off `source`) + the drafted-reply Section header (only once a draft exists); snapshots carry the judgment as shown. **T2.3** mounted on the LoopPeek latest-finding header, target `"{loop_id}:{cycle}"`, producer from the view's `feedback_producer` (flows through `loopToGoalLoop`'s spread). Microcopy is the owner's ratified silent-positive pair: "Mark accurate" / "Mark wrong (tell me why)".
- [2026-07-27][S2] DEVIATION (scope trim, consistent with the plan's hard rule): T2.2's "digest rows on InboxPage" was folded into InboxDetail (digest items open in the same detail panel; the list row is not a judgment SURFACE — the judgment renders in detail). One mount point per judgment, no list-row noise.
- [2026-07-27][S3] DONE: deterministic thresholds + retire + table (T3.1-T3.4). **T3.1** `suppressed_producers()` (fail-open everywhere: config fault, settings corrupt, stats error → suppress NOTHING) + `entity_settings/feedback.json` (snoozed/cleared/retire_proposed); consulted as one membership check in `workflows/surfacing.py::eligible_workflows`. **T3.2** `check_retire_candidates()` rides `InboxService.run_maintenance` (the existing 6h tick, no new loop): one-time proposal per crossing (dedup via retire_proposed; snooze resets the dedup so a lapsed snooze can re-propose), `notify("feedback_retire", …)` with counts + deep link (prompts → #/settings/prompts), post-42 swap note at the emit site; proposal-only producers (prompt/loop_judge — no surfacing gate) covered by `_proposal_only_candidates`. **T3.3** Settings → AI feedback: new `FeedbackPanel` (per-producer rows, honest counts, min-N "collecting" gate, accuracy tone chips, suppressed badge, Snooze/Clear) registered in BOTH `SUBPAGES` AND the `settingsWidgets.tsx` bento grid (the recurring miss — not missed this time). **T3.4** plan 56 unshipped (`agents/routing.py` absent) → coordination note added to AGENT-ROUTING §Risks specifying the exact double-write calls.
- [2026-07-27][S3] DEVIATION: `skills/surfacing.py` was NOT wired to `suppressed_producers()` — recon showed skills have no per-skill producer identity in the C1 vocabulary yet (`skill_synthesis:ladder` names the SYNTHESIZER, not individual skills; per-skill verdicts have no capture surface in v1). Consulting suppression there would be dead code. Wire it when a skill-level target kind exists (flywheel territory). Workflow surfacing IS wired (it has the `workflow_surfacing:<id>` producer + a real gate).
- [2026-07-27][S1-3] Tests: `tests/test_feedback.py` (20 — capture round-trip/supersede/reason rules/corrupt-line/trim/0600, GROUP BY + window + per-producer separation, thresholds incl. fail-open + one-time retire + snooze-reset + kill-switch, workflow surfacing suppression round-trip), `tests/test_feedback_routes.py` (8 — record/hydrate, envelope 400s, app-namespace forcing, kill-switch 404s, min-N gating, suppressed flag, snooze/clear round-trip). Reference regenerated (+5 routes). Gate: `make lint` green (506 files), `make test` 8077 passed / 28 skipped / 13 xfailed, web typecheck + vitest (238) + build green.
- [2026-07-27][S3] Validated as-a-user on an isolated gateway (:10014, fresh dev home): seeded 3 producer kinds via the real API — GROUP BY correct (5-down prompt → accuracy 0 + suppressed; sub-min-N rows "collecting"); JSONL human-readable, mode 0600; the retire check fired LIVE on the maintenance path (a real `feedback_retire` notification appeared in the bell + notifications.jsonl); Settings → AI feedback rendered all three sources with honest counts; the Snooze button cleared the suppressed badge live and persisted (`entity_settings/feedback.json` snoozed entry + retire_proposed reset). Thumbs-on-inbox-card flow untestable without a bound model (no classified items in the sandbox) — the affordance + hydration path is unit/route-locked instead; dogfooding on the real instance is Owner task 2.
