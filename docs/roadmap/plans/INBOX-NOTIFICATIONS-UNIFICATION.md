# Plan: Inbox/Notifications Unification — One Attention Store, One Configurable Delivery Layer

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18 from the owner-commissioned boundary investigation)
**Created:** 2026-07-18
**Wave:** 1 (S1-3) + 2 (S4-5). **Change class B** (persisted-state changes) — executed as a **maintainer clean break under the pre-1.0 banner**, per the standing owner ruling (see *Change discipline* below). It was previously framed as the first full exercise of LIFECYCLE-DOCTRINE; that framing described a methodology this plan does not need.
**Depends on:** nothing hard — **buildable today** against code that exists. Coordinates with: LEARNING-FLYWHEEL §2.2 (its proposal queue lands AS inbox kind=proposal — one attention surface, not a fourth); PROACTIVE-ASSISTANT (the digest built here is its pulled-forward ambient slice); CHANNEL-EXPANSION (channel DM as a rules target; pairing prompts become `agent_request` items); MOBILE-COMPANION (the `push` target activates there).
**Scope:** end state per the owner's model — **Inbox is THE durable attention store; Notifications is an ephemeral, per-(source, kind)-configurable delivery layer over it.** **Soul guardrail:** `DashboardState.notify()` remains the single delivery choke point (one path per concern) — this plan re-homes *policy and persistence*, never adds a second delivery pipeline. Real-time tool approvals stay session-modal for latency, mirroring into the inbox only when they outlive the prompt. The fail-open philosophy of the existing gate ("a broken settings file must not silence the system") is preserved in the rules engine.

---

## Context (code recon, 2026-07-18)

- **`InboxItem` is channel-message-shaped:** `id = {channel}_{ts}` (a `ts` property literally rsplits the id — new kinds must keep ids `*_{ts}`-shaped), fields channel/sender/draft/classification (NEEDS_REPLY|FYI|NOISE)/confidence/status (**PENDING|SENT|DISMISSED|HANDLED** — close to the target lifecycle; SEEN is missing)/source/can_reply/reply_target/favorited; `from_dict` is tolerant (back-compat friendly — extensions are additive-safe).
- **`notify()` (state.py:1027):** global gate `notification_allowed(kind)` (min-severity/quiet-hours/mute-all; **fail-open by design**) → note dict `{kind, title, body, ts, +meta}` → in-memory log append → broadcast → `_persist_notification`. `unread_count()` derives from unacked log entries — **the second durable unread store, confirmed**.
- **Inbox's own alert config:** `entity_settings/inbox.json` (`alert_keywords`, `alert_on_name_mention`) evaluated at ingestion (`inbox.py::evaluate_alert:270` → `notify_inbox_alert:294` → `notify()`), guarded PUTs in `providers/entity_routes.py`.
- **Skills proposals** (`skills/proposals.py`): file-per-proposal store with `enqueue/list_pending/get/reject/accept(pid, …)` — clean fold-in target (inbox item references pid; actions call accept/reject).
- **notify() emitters (~10 sites):** gateway, `schedule_script.py`, `loop/watchdog.py`, `providers/{entity_routes,registry}.py`, `dashboard/handlers/{hooks,messaging}.py`, `action_providers/{send_message,notify}_provider.py`, `inbox.py`.
- Kinds are stringly-typed at every site; config is global-only — no per-(source, kind) vocabulary exists.

## Design

### Target model

- **Inbox item (extended, additive):** existing fields + `item_kind: message|mention|email|agent_request|proposal|needs_input|digest|system` (default `message` — every existing item valid), `refs: dict` (session/loop/skill-proposal/workflow ids), status gains `SEEN` (lifecycle: PENDING(new) → SEEN → HANDLED|DISMISSED; SENT stays for reply-drafts). Id scheme for non-channel kinds: `{kind}_{uuid8}_{ts}` (keeps the `ts` rsplit contract). **Unread truth = inbox items in PENDING** (post-S5).
- **Kind registry (`src/personalclaw/notification_kinds.py`):** frozen registrations `(source, kind, label, default_mode, default_severity)`; a constants module the ~10 emitters import. Unknown (source, kind) at runtime → warn + treat as `(system, generic)` — **fail-open for delivery**, mirroring the existing gate philosophy.
- **Rules (`entity_settings/notification_rules.json`):** `{"<source>/<kind>": {mode: never|badge|immediate|digest, targets: [dashboard, channel_dm, push], conditions: {keywords: [], name_mention: bool}}}`; guarded PUTs beside the existing entity routes. Evaluation inside `notify()` **after** the global gate: `never` → drop (debug log); `badge` → persist log entry flagged `badge_only` (no toast broadcast); `immediate` → current behavior + per-target dispatch (`channel_dm` via `ChannelDelivery.deliver_notification`, `push` no-op until plan 44); `digest` → append to `~/.personalclaw/digest_queue.jsonl`. Conditions gate mode escalation (e.g., keyword hit upgrades a `badge` rule to `immediate`) — exactly today's inbox-alert semantics, generalized. Corrupt/missing rules file → registry defaults (fail-open).
- **Digest:** a system cron (`notification_digest`, owner-configurable schedule, default 08:00 local) drains the queue → one `digest` inbox item (grouped by source/kind, counts + top lines) → delivered per the digest rule's targets. This is the **morning digest** (PROACTIVE-ASSISTANT's ambient slice).
- **Fold-ins:** skills proposals surface as `proposal` items (created at `enqueue`, resolved by accept/reject actions); loop needs-input/autopause emit `needs_input` items + immediate rule; channel pairing requests (plan 40) emit `agent_request`. LEARNING-FLYWHEEL's queue registers as `proposal` from birth (coordination note in that plan's steps).
- **Demotion (S5):** notification log becomes a delivery audit (acked/unread semantics removed); `unread_count()` re-derives from inbox; dashboard badge + notifications panel read the new truth; old alert fields in `inbox.json` are removed in the same change that stops reading them.

### Change discipline (maintainer clean break — supersedes the earlier gate/migration framing)

This plan changes persisted state, so it was originally written in the migration-backed
form a *contributor* would use: a `inbox_unification` gate, a dual-path `notify()`, and two
`lifecycle/migrations/m_*.py` files. **That is not how the maintainer executes it, and the
gate/dual-path half is not a prerequisite for the feature.** Per the standing owner ruling
recorded in [AGENTS.md](../../../AGENTS.md) and
[CONTRIBUTING.md](../../../CONTRIBUTING.md#breaking-changes), during 0.x the maintainer
lands backward-incompatible clean breaks under the README's pre-1.0 banner. So:

- **No gate.** The rules engine *is* the delivery path — there is no gate-OFF legacy branch
  to keep byte-identical, and no cleanup session to remove one. Replacement and deletion
  land in the same change.
- **No `lifecycle/` machinery.** That package does not exist and must not be hand-rolled
  (an explicit rejection in both contributor docs). The two migrations become **idempotent
  backfills keyed on data inspection** — the same shape the tags-table change used, and the
  same pattern `_init_schema`'s `IF NOT EXISTS` already establishes:
  - inbox alert fields → rules conditions: if `notification_rules.json` is absent and
    `inbox.json` still carries `alert_keywords`/`alert_on_name_mention`, project them into
    the channel message/mention rules, then delete the old fields and their PUT guards.
  - pending skill proposals → inbox items: for each pending proposal without an item
    referencing its `pid`, create one. Idempotent by `pid`; re-running is a no-op.
- **One accepted state break, named up front.** S5 makes inbox `PENDING` the single unread
  truth and strips acked/unread semantics from the notification log, so an existing
  install's **unread badge resets once** on upgrade. That is the banner's job to cover:
  CHANGELOG entry + `personalclaw snapshot` in the release notes. It does not earn a
  migration.

A contributor picking this plan up instead should read the
[breaking-changes section](../../../CONTRIBUTING.md#breaking-changes) and surface the state
change rather than executing it.

## Contracts & Interfaces (this plan OWNS the attention-path contracts every consumer references — [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md) §1.3 landmine #1)

### C1 — Kind registry (`src/personalclaw/notification_kinds.py`, new)

```python
@dataclass(frozen=True)
class NotificationKind:
    source: str                 # emitter domain: "chat","loop","cron","inbox","skills","system","channel","learning"
    kind: str                   # "message","mention","email","agent_request","proposal","needs_input","digest","system"
    label: str                  # human label for the rules UI
    default_mode: Literal["never","badge","immediate","digest"]
    default_severity: int       # 1..3 (3 bypasses quiet hours, matching the existing gate)

def register(k: NotificationKind) -> None: ...   # raises on duplicate (source,kind)
def all_kinds() -> list[NotificationKind]: ...
def resolve_kind(source: str, kind: str) -> NotificationKind: ...  # unknown → synthetic ("system","generic") + warn (fail-OPEN, §2.7)
```

Every one of the ~10 `notify()` emitters passes a registered `(source, kind)`. The inventory of current kinds is built in T1.1 (grep + Execution-log table) so none is missed.

### C2 — Rules store `~/.personalclaw/entity_settings/notification_rules.json` (schema)

```jsonc
{
  "rules": {
    "<source>/<kind>": {
      "mode": "never|badge|immediate|digest",
      "targets": ["dashboard","channel_dm","push","native"],   // push→plan44, native→plan45 (no-op until then)
      "conditions": { "keywords": ["deploy"], "name_mention": true }  // escalate badge→immediate on match
    }
  },
  "digest": { "schedule": "0 8 * * *", "timezone": "local" },
  "global": { "min_severity": 1, "quiet_hours": {"start":"22:00","end":"07:00"}, "mute_all": false }
}
```
Corrupt/missing → registry defaults + warn (**fail-open**, §2.7). Guarded PUTs beside `providers/entity_routes.py` patterns.

### C3 — Evaluation (inside `notify()`, BEHIND gate `inbox_unification`; gate OFF = byte-identical legacy path)

```
notify(kind_or_(source,kind), title, body, meta):
  if not gate_enabled("inbox_unification"): <legacy path, unchanged>; return
  if not global_gate_allows(severity): drop(debug); return           # existing min_sev/quiet/mute
  rule = resolve_rule(source, kind)                                    # registry default if unset
  if conditions_match(rule, title, body): rule = escalate(rule)       # keyword/mention → immediate
  match rule.mode:
    never    -> drop(debug)
    badge    -> persist note flagged badge_only (no toast broadcast)
    immediate-> broadcast + per-target dispatch (dashboard=existing; channel_dm=ChannelDelivery.deliver_notification; push/native=plan44/45)
    digest   -> append ~/.personalclaw/digest_queue.jsonl (trim 2×)
```

### C4 — Extended `InboxItem` (additive to `inbox.py:60`; tolerant from_dict preserves old items)

```python
# NEW fields (defaults keep every existing item valid):
item_kind: str = "message"        # NotificationKind.kind values
refs: dict = field(default_factory=dict)   # {"session":..,"loop":..,"skill_proposal":pid,"workflow":..}
# ItemStatus gains SEEN: PENDING(new) → SEEN → HANDLED|DISMISSED  (SENT stays for reply-drafts)
# id for non-channel kinds: f"{kind}_{uuid4().hex[:8]}_{ts}"  — KEEPS the {..}_{ts} rsplit contract (§3.6)
```

### C5 — `emit_attention_item()` (the single helper that keeps inbox + notification in sync)

```python
def emit_attention_item(*, source: str, kind: str, title: str, body: str,
                        refs: dict | None = None, severity: int = 1,
                        dedup_key: str | None = None) -> str:
    """Create a PENDING InboxItem AND route ONE notification through notify().
    Returns the inbox item id. The ONLY correct way to raise a standing agent
    request — callers never call notify() and inbox-add separately (double-fire risk, §Risks)."""
```

### Integration points
- **Called by:** every `notify()` emitter (~10 sites, T1.2 migrates them to typed kinds); loop watchdog + gateway autopause → `emit_attention_item(kind="needs_input")`; skills `enqueue()` → `emit_attention_item(kind="proposal")` (T4.1); channel trust (plan 40) → `kind="agent_request"`; LEARNING-FLYWHEEL queue registers as `kind="proposal"`.
- **Calls:** `DashboardState.notify` (unchanged choke point, §3.4), `gate_enabled`, `resolve_kind`, `ChannelDelivery.deliver_notification` (channel_dm target), `skills/proposals.accept/reject` (T4.1), plan-31 migration framework.
- **Consumed by:** 44 (push target), 45 (native target), 46 (proposal/attribution), 21 (digest is its ambient slice), 40 (channel_dm + agent_request).
- **Storage owned:** `notification_rules.json`, `digest_queue.jsonl`; **migrates** `inbox.json` alert fields (migration `m_*_inbox_alert_fields_to_rules`) and seeds proposal items (`m_*_pending_skill_proposals_to_inbox`).
- **Gate:** `inbox_unification` (class B).

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Kind registry + rules engine (the rules path replaces the ad-hoc one)

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `notification_kinds.py`: registry dataclass + registrations covering every existing `notify()` call site's kind (enumerate by grep; record the inventory in the Execution log); constants exported | create `src/personalclaw/notification_kinds.py`, tests | every current emitter's kind string has a registration; duplicate registration raises |
| T1.2 | Migrate the ~10 emitters to typed constants (mechanical; zero behavior change — same strings flow through) | the 10 listed modules | grep finds no bare-string kinds at call sites; suite green |
| T1.3 | Rules store + evaluation: load/validate `notification_rules.json` (guarded PUT routes beside `entity_routes.py` patterns), `resolve_rule(source, kind) -> Rule` with registry defaults + corrupt-file fail-open; evaluation wired into `notify()` **as the delivery path** (no gate — the pre-rules branch is deleted in this change, per *Change discipline*) | `src/personalclaw/notification_rules.py`, `providers/entity_routes.py`, `dashboard/state.py` | never/badge/immediate/digest each behave per Design; **a default/absent rules file reproduces today's user-visible behavior** for every registered kind (that equivalence is the regression test, replacing the gate-OFF comparison); no second delivery branch remains in `notify()` |
| T1.4 | Digest queue writer (append-only JSONL, trim at 2× cap) | `notification_rules.py` | queue writes covered by test; trim proven at the boundary |
| V1 | Validation: with no rules file, drive chat/cron/loop notifications as a user and confirm they behave as before; then set a `never` rule and a `digest` rule and observe the drop + the queue append | — | both states verified; ledger written |

### Session 2 — Inbox as the attention store

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `InboxItem` extension: `item_kind` (default message), `refs`, `SEEN` status, id helper for non-channel kinds (`{kind}_{uuid8}_{ts}`); `from_dict` tolerance test for old items | `src/personalclaw/inbox.py`, tests | old fixture items load; new kinds round-trip; `ts` property holds for both id shapes |
| T2.2 | Emit-side helpers: `emit_attention_item(kind, source, title, body, refs, notify_rule=...)` — creates the inbox item AND routes one notification through `notify()` (single choke point preserved); wired for `needs_input` (loop watchdog + gateway autopause sites) | `inbox.py`, `loop/watchdog.py`, gateway autopause site | a loop needs-input produces exactly one PENDING `needs_input` item + one immediate notification (not two of either); the previous notify-only path at those sites is gone |
| T2.3 | Inbox service/API: list/filter by `item_kind`, mark-SEEN on view, handled/dismissed transitions for non-message kinds (no draft/reply machinery for them) | `inbox_service.py`, `dashboard/handlers/` inbox routes | API filter returns kinds; transitions persist; message-kind behavior untouched |
| T2.4 | Frontend inbox: kind filter chips + kind-specific row rendering (needs_input rows deep-link their loop/session via `refs`) | `web/src/pages/inbox/` components | chips filter; deep links navigate (URL-state doctrine respected) |
| V2 | Validation: run a loop to a checkpoint → needs_input appears in inbox, deep-links to the loop, resolves to HANDLED on answer; message items unaffected | — | holds |

### Session 3 — Settings unification (frontend) + the alert-fields backfill

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Notifications settings page: global gate section (existing controls) + rules matrix (source×kind grid: mode selector, targets, conditions editor) + digest schedule field; served from the rules store | `web/src/pages/settings/` (new panel beside MemoryPanel pattern), rules PUT routes | matrix edits persist and take effect on next notify() (no restart) |
| T3.2 | Idempotent backfill (**not** a `lifecycle/` migration — see *Change discipline*): if `notification_rules.json` is absent and `inbox.json` still carries `alert_keywords`/`alert_on_name_mention`, project them onto the channel message/mention rules; then delete those fields, their PUT guards, and the inbox settings page's alert section (which points at Notifications instead) | `notification_rules.py`, `providers/entity_routes.py`, inbox settings component | hostile fixture (keywords set / empty / missing file / malformed JSON) → conditions reappear or defaults apply, never a crash; **re-running is a no-op**; old fields and their guards are gone in the same change; a PUT to a removed field 400s |
| T3.3 | Docs: `docs/architecture/inbox-channels.md` Notifications section rewritten to the rules model (legacy paragraphs deleted, not marked); configuration reference updated | the two docs | docs match shipped behavior; no paragraph describes the removed alert fields |
| V3 | Validation: as a user — configure a keyword condition in the new UI, send a matching channel message (echo transport), observe the escalated immediate notification; non-matching stays badge | — | holds |

### Session 4 — Fold the proposal surfaces (Wave 2)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Skills proposals → inbox: `enqueue()` also emits a `proposal` item (refs.pid); accept/reject actions on the inbox row call `skills/proposals.accept/reject`; item resolves HANDLED/DISMISSED accordingly | `skills/proposals.py`, inbox handlers, inbox frontend row actions | proposing → item appears; approve from inbox installs the skill (existing accept path); reject dismisses |
| T4.2 | Idempotent backfill (**not** a `lifecycle/` migration): every pending proposal without an inbox item referencing its `pid` gains one | `skills/proposals.py` or inbox service, tests | fixture with 3 pending proposals gains exactly 3 items; re-run no-op; a proposal already carrying an item is untouched |
| T4.3 | Skills page's approval tab becomes a filtered-inbox embed (component reuse — one surface, no second approval UI to maintain) — or, if embed friction is high, links to the filtered inbox (record choice as DEVIATION/decision) | `web/src/pages/skills/` | one code path renders proposals everywhere |
| T4.4 | Session-modal approvals: when a tool-approval prompt outlives its session view (unanswered > TTL or session backgrounded), mirror an `agent_request` item (refs.session, deep-link to the approval); answering either surface resolves both | approval prompt machinery (`chat_runner`/approval path — locate `resume`/`approve` route), inbox wiring | timed-out approval appears in inbox; answering from inbox unblocks the session |
| V4 | Validation: full proposal round-trip from inbox; a backgrounded approval recovered via inbox on a phone-sized viewport | — | holds |

### Session 5 — Digest + demotion (Wave 2)

| ID | Task | Files | Done when |
|---|---|---|---|
| T5.1 | Digest cron: system cron registration (respects `--no-crons`), drain queue → grouped `digest` inbox item + delivery per rule targets (dashboard + channel_dm via existing `deliver_notification`/`deliver_cron_result` precedent — record which fits) | `notification_rules.py` digest builder, cron registration site (`schedule.py` patterns) | scheduled run produces one digest item with correct grouping; empty queue → no item |
| T5.2 | Demotion: `unread_count()` derives from inbox PENDING; notification log loses acked/unread semantics (becomes delivery audit; panel renders read-only history); badge + panel wired to inbox truth | `dashboard/state.py`, notifications panel component | badge counts inbox PENDING only; audit panel shows deliveries; no unread semantics remain on the log |
| T5.3 | CHANGELOG entry naming the one accepted state break — the unread badge resets once because acked/unread semantics leave the notification log — with `personalclaw snapshot` advised in the release notes | `CHANGELOG.md` | entry present and specific about what a user observes; the old unread derivation is gone from `state.py` (deleted in T5.2, verified by grep) |
| V5 | Validation: 24h dogfood on the owner's real instance (owner task 2) — digest arrives on schedule with the day's badge-mode items; unread badge tracks inbox exactly; SEL/audit review shows sane delivery history | — | owner-confirmed; ledger written |

## Owner tasks (real world)

1. **Design review of the rules matrix UX** (S3 — 30 min): the source×kind grid is the plan's main UI bet; approve or redirect before frontend build.
2. **24h dogfood** of the completed system on your real instance (V5) — the digest schedule, rule defaults, and badge behavior are taste calls only real use validates.
3. Decide the **default digest time** (08:00 local proposed) and which kinds default to `digest` vs `badge` (proposal: mentions=immediate, messages=badge, proposals=digest, needs_input=immediate, system=badge).

## Risks & open questions

- **Risk — double-notification during dual-path:** with the gate ON, an emitter migrated in S2 must not ALSO fire its legacy notification; the emit-helper owns both halves — conformance asserted per migrated site (test per emitter).
- **Risk — inbox page becomes a junk drawer:** kind chips + sane defaults (above) are the mitigation; PROACTIVE-ASSISTANT owns real triage intelligence later.
- **Open:** whether `digest` items themselves notify `immediate` on the dashboard (proposal: yes, once, at digest delivery — it IS the morning knock).

## Amendment (2026-07-26 — sibling-platform gap analysis, owner greenlight)

**Second-opinion verification gate ("don't cry wolf").** Sibling-platform evidence: AI-produced attention items (alerts, findings, proposals) train the user to ignore the inbox once even a small fraction are wrong. This adds an opt-in, per-(source,kind) skeptic pass: before an AI-produced item surfaces, a cheap background model call is prompted to REFUTE the item's claim; refuted items land in a reviewable FILTERED state (never silently deleted), non-refuted items surface normally. Channel-message kinds are exempt by design (they are the user's real mail); only kinds registered as verifiable may opt in, and real-time blocking kinds (`needs_input`, `agent_request`) never do. Deterministic fallback is fail-OPEN per §2.7: no bound model, resolution error, timeout, budget exhaustion, or unparseable verdict → the item surfaces with honest `verify: skipped` provenance — a broken verifier must never silence the system.

### Contract-level design

- **C1 gains one field:** `NotificationKind.verifiable: bool = False` — registered `True` only for AI-claim kinds (`proposal`, `digest`-feeding app alert kinds); the rules PUT rejects `verify: true` on a non-verifiable kind.
- **C2 rule schema gains one field** (additive): `"<source>/<kind>": {mode, targets, conditions, "verify": false}` — the T3.1 rules matrix renders the toggle only for verifiable kinds.
- **Verifier core** — new `src/personalclaw/notification_verify.py`:

```python
@dataclass(frozen=True)
class VerifyVerdict:
    status: Literal["refuted", "upheld", "skipped"]
    reason: str        # the model's one-line refutation, or the skip reason
    model_ref: str     # "provider:model" that judged; "" when skipped

async def verify_attention_item(*, source: str, kind: str, title: str, body: str) -> VerifyVerdict: ...
```

  Implementation: `one_shot_completion(use_case="background")` (`llm_helpers.py:277` — the reasoning axis, never chat/code_tools), prompted to refute; ONLY an explicit structured `REFUTED: <reason>` verdict filters — every other outcome (upheld, parse failure, `ProviderResolutionError`, timeout) is upheld/skipped. Spend meters through the ModelCallGuard at the bridge seam like every one-shot call; a day-budget breach skips verification for the rest of the day (logged, honest).
- **Hook point:** inside `emit_attention_item()` (C5) — the single helper — between item construction and `notify()`. Refuted: the `InboxItem` persists with new `ItemStatus.FILTERED` (additive to `inbox.py:42`; tolerant `from_dict` unaffected) and `refs["verify"] = {status, reason, model_ref}`; no notification fires. Upheld/skipped: normal path, provenance still recorded. The raw channel-ingestion path (`inbox.py`/`inbox_service.py::_ingest`) is untouched — messages never route through the gate.
- **Review surface:** the T2.4 kind-chip row gains a "Filtered" chip; a Restore action flips FILTERED→PENDING and fires the withheld notification exactly once through `notify()` (the unchanged choke point). Filtered items obey standard retention pruning. Per-verdict SEL: none (not security-relevant); `verify` rule edits ride the existing rules-PUT audit.

### Session placement

New **Session 6** (session count 5→6, honest): needs C1/C2/C5 (S1-2) and the rules UI (S3); Wave 2, after S4-5.

| ID | Task | Files | Done when |
|---|---|---|---|
| T6.1 | `verifiable` on the kind registry + `verify` rule field + `notification_verify.py` (REFUTED-only filtering; fail-open on every failure path; ModelCallGuard-metered) | `notification_kinds.py`, `notification_rules.py`, new `notification_verify.py`, tests | refuted fixture → FILTERED + no notify; no-model fixture → surfaces with `verify: skipped`; `verify: true` on a non-verifiable kind → rules PUT 400 |
| T6.2 | `ItemStatus.FILTERED` + emit-helper wiring + Filtered chip + Restore (FILTERED→PENDING, withheld notification fires once) | `inbox.py`, inbox handlers, `web/src/pages/inbox/` | restore round-trips; old fixture items still load; no double-notification on restore |
| V6 | Validation: opt a `proposal` rule into verify; drive one true + one planted-false proposal — false lands Filtered with a readable refutation, true surfaces; unbind the reasoning model → both surface with skipped provenance | — | holds; Execution log written |

## Amendment (2026-07-26 — gap analysis round 2, owner decisions)

**The Proposals contract (owner-approved primitive).** This plan already types `proposal` (C1/C4) and folds the skills queue in at S4 (T4.1) with LEARNING-FLYWHEEL's queue registering as `proposal` from birth — this amendment does NOT re-add the kind. What is missing is a CONTRACT: today a proposal item is just an InboxItem with `refs.pid`, its resolution hard-wired to `skills/proposals.accept/reject`. Every upcoming producer (auto-learned skills, SESSION-MANAGEMENT org suggestions, FEEDBACK-SIGNAL retire-proposals (plan 58), AUTONOMY-GUARDRAILS' round-2 earned-autonomy promotion offers, email/reply drafts) would otherwise hard-wire its own resolution path. This contractifies the payload + the apply mechanics + adds the app emission path.

### Contract-level design

- **C6 — Proposal payload** (typed, carried in `refs["proposal"]` on a `kind=proposal` InboxItem — additive, tolerant `from_dict` unaffected):
```python
@dataclass(frozen=True)
class Proposal:
    title: str
    preview: str            # rendered body — markdown or unified diff
    preview_kind: Literal["text", "diff"]
    provenance: str         # who/what produced it: "skills", "learning", "app:<name>", "session_org"
    expires_at: str | None  # ISO; expiry → auto-DISMISSED with an audit line
    editable: bool          # approve / edit-then-approve / dismiss; edit re-posts edited payload into apply
    apply: dict             # THE APPLY CONTRACT — exactly one of:
                            #  {"action": {provider, config}}          — action-provider invocation (registry dispatch)
                            #  {"workflow": {ref | inline}}            — a workflow run
                            #  {"skill_promotion": {pid}}              — skills/proposals.accept (T4.1 path, now one case of the contract)
                            #  {"app_callback": {app, route}}          — POST to the emitting app's declared route, its scoped token identity
```
  Approval executes `apply` through the EXISTING dispatchers (action registry / workflow engine / skills accept / app-permission-gated app route) — the proposals surface owns none of its own execution. Apply outcomes write back to the item (`HANDLED` + result ref, or a typed failure kept `PENDING` with the error).
- **App emission path:** apps declare proposal kinds in `app.json` (`permissions.proposals: [{kind_suffix, label}]`, registered as `("app:<name>", "proposal:<suffix>")` in the C1 kind registry at enable-time) and POST `/api/inbox/proposals` with their scoped token (the `request["app"]` identity from `apps/permissions.py`); undeclared kind or an `apply.app_callback` targeting another app → 403. App-emitted proposals are `verifiable=True` by default (they are AI/app claims — the round-1 skeptic gate applies).
- **Proposals view + safe batching:** the T2.4 inbox gains a Proposals lens; batch-approve is offered ONLY across same-`(provenance, kind)` groups (never a mixed sweep); each batch apply is N individual applies with per-item outcomes.
- **SDK surface** (`sdk` proposal-post helper) is an **APP-PLATFORM-EVOLUTION coordination line** — the HTTP contract lands here; the ergonomic wrapper lands there.

### Session placement

Extends **Session 4** (which already owns the proposal fold-in): T4.1 becomes the first `apply.skill_promotion` consumer of C6 rather than a bespoke wiring. The app path + batching = new **Session 7** (after S4-6; count 6 → 7, honest). Round 1's Session 6 (verification gate) is untouched; C6 composes with it (`verifiable` proposals pass the skeptic before surfacing).

| ID | Task | Files | Done when |
|---|---|---|---|
| T7.1 | C6 Proposal dataclass + apply dispatcher (four apply cases through existing dispatchers; typed failure keeps item PENDING); T4.1's skill path re-expressed as `skill_promotion` | `inbox.py` or new `proposals_contract.py`, inbox handlers, tests | each apply case round-trips on fixtures; a failing apply surfaces the error on the item; skills accept path behavior unchanged |
| T7.2 | App emission: `permissions.proposals` manifest field + kind registration at enable-time + `POST /api/inbox/proposals` (scoped-token identity, 403 on undeclared kind/foreign callback) | `apps/manifest.py`, `apps/permissions.py`, inbox handlers | fixture app posts a proposal that renders + applies via its callback; undeclared kind rejected; SEL entry per app emission |
| T7.3 | Proposals lens + same-(provenance,kind) batch-approve with per-item outcomes; edit-then-approve for `editable` payloads | `web/src/pages/inbox/` | batch across mixed kinds impossible in the UI; edited payload is what apply receives; per-item results visible |
