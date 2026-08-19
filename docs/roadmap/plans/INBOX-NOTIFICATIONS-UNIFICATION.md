# INBOX-NOTIFICATIONS-UNIFICATION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/INU.md`](../atomic/INU.md) as 8 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Inbox/Notifications Unification — One Attention Store, One Configurable Delivery Layer

**Status:** IN PROGRESS — Sessions 1-5 shipped 2026-07-30 (PRs #111-#115): the typed kind registry
(`notification_kinds.py`, 20 pairs, 26 emitters migrated, imported by 12 non-test modules), the rules
store + evaluation as THE `notify()` delivery path, the inbox as the attention store
(`item_kind`/`refs`/`SEEN`/`emit_attention_item` — now called from 7 distinct modules), the settings
rules matrix + alert-fields backfill, and the daily digest + unread-badge demotion.
**REMAINING:** the rev-12 verification-gate session (S6 — `notification_verify.py`,
`NotificationKind.verifiable`, `ItemStatus.FILTERED`, all absent) and the rev-13 Proposals-contract
session (S7 — C6 payload/apply dispatcher, app emission path, proposals lens, `permissions.proposals`).
Note the Execution log is missing its Session 2 entry although `dfca975` (#112) shipped it — a log
gap, not a code gap. Status corrected 2026-08-04 by code audit (this line had read DESIGNED).
Deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18 from the owner-commissioned boundary
investigation)

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

## Contracts & Interfaces (this plan OWNS the attention-path contracts every consumer references — [AGENTS.md](../../../AGENTS.md) §1.3 landmine #1)

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

### C3 — Evaluation (inside `notify()` — THE delivery path; no gate, per *Change discipline*)

The equivalence the dropped gate would have proven is instead a **regression test on default
behavior**: with no `notification_rules.json` present, every registered kind must deliver
exactly as it does today.

```
notify(kind_or_(source,kind), title, body, meta):
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
- **Calls:** `DashboardState.notify` (unchanged choke point, §3.4), `resolve_kind`, `ChannelDelivery.deliver_notification` (channel_dm target), `skills/proposals.accept/reject` (T4.1). (No `gate_enabled`, no migration framework — see *Change discipline*.)
- **Consumed by:** 44 (push target), 45 (native target), 46 (proposal/attribution), 21 (digest is its ambient slice), 40 (channel_dm + agent_request).
- **Storage owned:** `notification_rules.json`, `digest_queue.jsonl`; **migrates** `inbox.json` alert fields (migration `m_*_inbox_alert_fields_to_rules`) and seeds proposal items (`m_*_pending_skill_proposals_to_inbox`).
- **Gate:** `inbox_unification` (class B).

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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

---

## Execution log

- 2026-07-30 — **DONE (Session 1: T1.1–T1.4).** Typed kind registry
  (`notification_kinds.py`, 20 pairs), all 26 emitters migrated to named constants, rules
  store + evaluation (`notification_rules.py`) wired into `notify()` as THE delivery path,
  guarded `GET/PUT /api/notifications/rules`, and the digest queue writer.

  **DEVIATION (methodology re-scope, not premise mismatch).** The session's tasks were
  written in contributor form: T1.3 asked for evaluation "behind the gate (gate OFF → exact
  legacy path, byte-identical notes)", T1.4 for a `lifecycle/gates.py` registration, and S5's
  T5.3 for a gate flip + legacy-branch deletion. `src/personalclaw/lifecycle/` does not
  exist, and per the owner's standing ruling (workspace `AGENTS.md` "You are working as the
  OWNER", `CONTRIBUTING.md#breaking-changes`) maintainer class-B work is a clean break under
  the pre-1.0 banner. So: **no gate, no dual path, no cleanup session** — the rules engine
  replaced the ad-hoc branch in one change. The equivalence the gate-OFF task would have
  proven is now a regression test on DEFAULT behavior
  (`test_no_rules_file_delivers_exactly_like_before`). The plan header, the
  Lifecycle-artifacts section (now *Change discipline*), C3, and five task rows were
  rewritten to match; the correction was traced to its source and fixed across the
  roadmap + workspace guidance docs.

  **T1.1 inventory (26 call sites, 11 flat kinds).** Built by AST walk, not grep — **two
  sites pass a DYNAMIC kind** a grep inventory misses (`loop/watchdog.py:159` via
  `_NOTIFY_EVENTS`, `action_providers/notify_provider.py:59` via config) and a **third**
  (`watchdog.py:222`) passes a literal `"info"` that my first pass mis-bucketed as dynamic —
  found by the T1.2 drift test. Sites: gateway ×15 (cron ×5, heartbeat ×5, subagent ×4,
  warning), watchdog ×2, feedback, inbox, denylist, hooks, messaging, app_routes,
  send_message, notify_provider.

  **Three behavior changes caught by the new tests before they shipped** — all the same
  class, *a refactor silently changing what reaches the user with no setting they touched*:
  (1) `badge` defaults on heartbeat / loop-progress / signal-retirement would have STOPPED
  delivering three kinds (read as "notifications broke"); (2) ranking cron **failures** as
  warning would have STARTED delivering them to anyone with `min_severity: warning`, since
  the old gate ranked that flat string info; (3) same for `app.route.drift`. Every
  `default_mode` is now `immediate` and every reachable pair keeps its historical severity,
  both pinned by tests (`test_every_default_mode_is_immediate`,
  `test_reachable_pairs_preserve_their_old_severity_exactly`). `badge` is opt-in per row.

  **Conditions preserve the surface they generalize.** Keyword/name-mention semantics are
  lifted verbatim from `inbox.evaluate_alert` (case-insensitive substring; whole-word name
  parts ≥3 chars), and `test_conditions_match_agrees_with_inbox_evaluate_alert` drives the
  REAL `evaluate_alert` and the new `Conditions.matches` over the same inputs so S3's
  backfill cannot change the meaning of alert config users already have. Escalation is
  capped at `immediate` and deliberately does NOT add targets — a keyword hit means "show me
  now", not "also DM me off this machine".

  **DISCOVERY (pre-existing, not fixed here):** the SPA's display map
  (`web/src/pages/notifications/notificationMeta.ts`) has rows for `schedule` and `loop`
  that **no backend emitter passes**. Mapped to their nearest real registration so
  notifications persisted by an older build still resolve, and pinned by
  `test_frontend_display_map_kinds_all_resolve`.

  **Validated as a user** on an isolated dev home (port 10741, never the owner's :10000):
  `GET /api/notifications/rules` returned a row for all 20 registered kinds with defaults;
  PUT persisted `never` to disk and the read path honored it; unknown kind / bad mode /
  unknown target / malformed conditions / bad cron all 400'd; a valid digest schedule
  round-tripped. Then drove `notify()` against the real store: **never** dropped (0 logged,
  0 broadcast), **immediate** delivered (1/1), **badge** logged without a toast (1/0,
  `badge_only: true`), **digest** queued instead of logging (0/0, entry in the queue), and a
  keyword hit escalated badge→immediate (`escalated_by: "keyword: urgent"`). Confirmed the
  global gate still wins: `mute_all` beat both an `immediate` and a `digest` rule (nothing
  delivered, nothing queued), and a corrupt rules file failed **open** (delivered). **0
  gateway tracebacks.**

  **Gates:** `make lint` clean (mypy 553 files) · `make test` **9249 passed, 0 failed**.
  Tests: `test_notification_kinds.py` (45), `test_notification_rules.py` (60), +14 route
  cases in `test_entity_settings_routes.py` (37 in file).

- 2026-07-30 — **DONE (Session 3: T3.1–T3.3).** The notifications settings page gains the
  per-(source, kind) rules matrix + digest schedule; the inbox's own alert fields are
  retired and backfilled into rule conditions; docs rewritten.

  **T3.1 — the matrix.** `NotificationRulesMatrix` renders one row per REGISTERED kind
  (the registry is the row list, so an uncustomized kind still appears with its default
  rather than being invisible until edited), grouped **by source** because "quieten
  everything from heartbeat" is the common ask and shouldn't require finding four rows. Mode
  via the canonical `SegPills`; targets/conditions behind a per-row disclosure so the common
  case stays one line. A `reset` control appears **only** when the user has actually diverged
  from the default — a "default" tag on every untouched row is noise on the common case.
  `push`/`native` targets are labelled "(mobile app required)" rather than hidden: the
  setting persists for when those plans land, but the label doesn't promise delivery today.

  **T3.2 — the backfill (DEVIATION: not a `lifecycle/` migration).** Per the standing owner
  ruling this is an **idempotent backfill keyed on data inspection** — it runs only when
  `notification_rules.json` is ABSENT and `inbox.json` still carries the legacy fields, and
  writing the rules file is itself the marker that it has run. That last property is
  load-bearing: without it, a user who deliberately CLEARED their keywords would have them
  resurrected on the next read, silently undoing a deliberate choice. Verified live.
  Projected onto **both** `inbox/alert` and `agent/message` — an alert was about the message
  arriving, and narrowing to one kind would quietly reduce coverage.

  **The clean break, in one change.** `alert_keywords`/`alert_on_name_mention` are gone from
  `INBOX_DEFAULTS` (so a PUT naming them is now dropped rather than persisted into a store
  nothing reads), `evaluate_alert()` lost its `settings` parameter entirely rather than
  keeping it and ignoring it (a caller still passing retired fields would silently get no
  alerts — exactly the failure a clean break should make impossible), and the now-dead
  `load_inbox_settings()` read + `re` import in `inbox.py` were deleted. Frontend: the alert
  controls were removed from **both** inbox settings panels and the settings bento widget
  (which now surfaces retention, what the inbox still owns) and replaced with a pointer to
  the rules matrix.

  **DISCOVERY — a test that had become circular.** `test_conditions_match_agrees_with_inbox_
  evaluate_alert` compared the engine to `evaluate_alert`, which now DELEGATES to it — so it
  was asserting the engine agrees with itself. Rewritten to pin the semantics against a
  **verbatim copy of the retired pre-S3 implementation** kept in the test as an oracle, with
  11 cases. That is what actually protects a user whose keywords were backfilled.

  **The primitive-adoption ratchet caught two raw `<button>`s and two raw inputs** in the new
  matrix. Fixed by using `Button`/`Checkbox`/`TextInput` (which already supports `mono` for
  the cron field) — **not** by raising the baseline, which was the tempting shortcut.

  **Validated as a user** on an isolated dev home (port 10743, never :10000) seeded as a
  **PRE-S3 install**: legacy `inbox.json` with two keywords + name-mention on, and no rules
  file. On first read the backfill produced both rules with the migrated conditions; `GET
  /api/inbox/settings` no longer surfaces the retired fields; a PUT naming them dropped them
  while still applying `retention_days`; clearing the keywords through the API did **not**
  resurrect them on re-read; and `evaluate_alert` fired for a keyword, for a name mention,
  and stayed silent otherwise. In a real browser: the matrix rendered grouped by source with
  `Notify` selected everywhere (the behavior-preserving default), the Inbox bento card showed
  retention instead of alert keywords, and clicking **Badge** on Heartbeat persisted
  `heartbeat/status: {mode: badge}` to disk while the migrated `inbox/alert` conditions
  survived untouched. **0 gateway tracebacks.**

  **Gates:** `make lint` clean (mypy 553 files) · `make test` **9327 passed, 0 failed** ·
  web typecheck + **302** vitest + build + render smoke green.
  Tests: +19 backfill/oracle cases in `test_notification_rules.py` (79 in file), +3 in
  `test_entity_settings_routes.py` (40), `test_inbox.py` alert tests rewritten against the
  rule, `test_inbox_service.py` helper now writes a real rule.

- 2026-07-30 — **DONE (Session 4: T4.1–T4.4).** Skill proposals and outlived tool approvals
  become durable inbox items, answerable in place.

  **T4.1 — proposals surface and resolve.** `enqueue()` raises a `proposal` item deduped by
  proposal id; the inbox detail panel loads the FULL proposal (the list summary truncates the
  procedure at 280 chars — approving a body you can't read isn't a review) and runs the same
  accept/reject endpoints the skills page uses, so there is **one** installation path rather
  than a second that could drift.

  **The ordering trap worth naming:** `accept()` calls `reject()` internally to clear the
  queue entry, so the naive wiring leaves an *installed* skill's row reading "dismissed" —
  the item is the only record of which answer the user gave. Resolution therefore runs
  DISMISSED in `reject()` and is corrected to HANDLED after, and `_resolve_inbox_item` accepts
  a terminal→terminal correction while never moving an item backwards into an open state.
  Tested in both directions.

  **T4.2 — DEVIATION: idempotent backfill, not a `lifecycle/` migration.** Idempotent **by
  pid** and keyed on data inspection: any item referencing the pid counts as "has one",
  **including a resolved one**, so a proposal the user already answered is never re-raised —
  re-asking a decided question is the worst failure available here. Runs from `list_pending()`,
  the read path both the skills page and the API use, so the first look after an upgrade is
  already correct.

  **T4.3 — DECISION (the plan offered embed-or-link):** kept `SkillProposals` as the editing
  surface and **cross-linked** rather than embedding a filtered inbox. The skills page owns
  the edit-then-approve flow; duplicating that into the inbox would be the second approval UI
  the task explicitly warns against. The inbox answers "yes/no" and offers "Edit first" →
  Skills. Both surfaces call the same endpoints and answering either resolves the other.

  **T4.4 — approval mirroring, with a grace period.** An approval prompt is session-modal for
  latency, and `chat_runner` waits up to **7200s** on it — so a prompt the user walked away
  from is a standing request they cannot see. It is now mirrored as an `agent_request` item
  only after `_APPROVAL_MIRROR_GRACE_SECS` (90s), so approving promptly leaves no litter.
  **`asyncio.shield` is load-bearing here:** without it `wait_for`'s timeout would CANCEL the
  approval future — the mechanism meant to surface the prompt would destroy it, and the second
  wait would hang on a dead future. There is a test asserting the shield protects it.
  Answering in the session resolves the mirror (approved → HANDLED, otherwise DISMISSED).

  **Validated as a user** on an isolated dev home (port 10744, never :10000) seeded as a
  **PRE-S4 install**: a proposal in its own store with **no** inbox item. `GET
  /api/skills/proposals` triggered the backfill and the item appeared; accepting over HTTP
  marked it **handled**; a fresh enqueue→reject marked its item **dismissed**. In a real
  browser the row rendered with the full procedure, collapsed provenance, and Install /
  Reject / Edit first — and clicking **Install skill** in the inbox wrote the real skill to
  `skills/auto/` and flipped the item to handled. Three items ended recording three distinct
  answers. **0 gateway tracebacks.**

  *(A false alarm worth recording: the first live check showed the backfilled item missing
  over HTTP while present on disk — a stale in-memory store in a gateway that had been
  running before the seed, not a bug. Confirmed by restart.)*

  **Gates:** `make lint` clean (mypy 553 files) · `make test` **9359 passed, 0 failed** ·
  web typecheck + 302 vitest + build + render smoke green.
  Tests: `tests/test_inbox_proposals.py`, 32 cases.

- 2026-07-30 — **DONE (Session 5: T5.1–T5.3). The plan is COMPLETE (S1–S5).**

  **T5.1 — the digest.** `build_digest_body` groups by kind (the point of a digest is that "9
  heartbeats" is ONE fact, not nine), newest-first within a group, capped at
  `DIGEST_LINES_PER_GROUP` with a remainder count so a busy day can't produce an unbounded
  summary. `run_digest` drains **before** writing: a write failure loses one digest body
  rather than leaving entries that get re-digested tomorrow *and* re-notified. An empty queue
  produces **nothing** — a daily "you have no notifications" item would be a reminder that
  nothing happened. Shipped as a deterministic action provider (`notification-digest`), not an
  agent turn: a digest is a grouping of things that already happened, so a model would add
  latency, cost, and a chance of inventing detail for a summary whose whole value is accuracy.
  Registered as a `silent` system cron — the digest's OUTPUT is an inbox item, so a cron-result
  toast would be a notification about your notifications.

  **T5.2 — the demotion.** `unread_count()` now counts inbox items in PENDING. The two stores
  had become two answers to one question: the log tracked "was a toast acknowledged", the inbox
  tracks "is this dealt with" — so handling a request in the inbox still left a badge lit, and
  dismissing a toast cleared the badge for work that was still outstanding. Counts PENDING
  only, not SEEN: the badge means "new since you last looked". Fails to 0 rather than raising —
  a badge is chrome and must not take down the sessions payload.

  **T5.3 — the accepted state break, in the CHANGELOG.** The badge resets once on upgrade.
  Written for a user, not a developer: what changes, that nothing is lost, why it's more honest
  afterwards, and `personalclaw snapshot` per the banner.

  **THREE integration gaps the full suite caught that unit tests could not:**
  1. `notification-digest` was registered as an action provider but absent from
     `ALLOWED_HOOK_PROVIDERS`, so **trigger validation would have refused to dispatch it** —
     the cron would look healthy and produce no digest. (`test_native_hook_providers` exists
     precisely to catch a registered-but-unroutable provider.)
  2. Two harness tests cascaded from the same gap.
  3. **A bug my own fakes hid.** `reconcile_digest_cron` read `getattr(job, "cron_expr")`, but
     the real `ScheduleJob` stores it at `job.schedule.cron_expr` (a nested
     `ScheduleDefinition`) — a flat read always yields None, so the reconcile would have
     "converged" the schedule on **every startup**, churning the job file forever. My
     `_FakeJob` had invented the flat attribute, so the fake agreed with the bug. Fixed the
     code, rebuilt the fake on the real dataclass, and added
     `test_fake_job_matches_the_real_schedule_shape` so a future shape change fails loudly
     instead of silently re-hiding this.

  **Validated as a user** on an isolated dev home (port 10745, never :10000). Set `digest` mode
  on two kinds and fired notifications through the **real** `notify()`: 7 queued, 1 badge-only
  persisted, 0 toasts broadcast. `run_digest` produced one item titled "Digest — 7
  notifications" with the grouped body (`**Scheduled job result** — 2` / `**Heartbeat** — 5` +
  "…and 2 more"), drained the queue, and a second run returned "" with nothing created.
  Against the **real** `ScheduleService`: the cron registered as `kind=cron cron=0 8 * * *
  silent=True provider=notification-digest`, three reconciles with an unchanged schedule were a
  no-op, and editing the schedule in the rules store converged the job to `30 6 * * 1-5`. The
  badge: 1 PENDING item → `unread_count() == 1`; **20 delivered toasts left it at 1**; handling
  the item took it to 0. **0 gateway tracebacks.**

  **Gates:** `make lint` clean (mypy 554 files) · `make test` **9387 passed, 0 failed**.
  Tests: +25 digest/cron cases in `test_notification_rules.py` (102 in file), `TestUnreadDerived`
  rewritten to the inbox contract (`test_dashboard.py`, 27 in file — its old isolation patched
  only `state.config_dir`, so my inbox read hit the DEVELOPER'S real inbox and returned 39;
  both `config_dir` seams are now patched).

- **[2026-08-11][INU-8] ATOM CREATED** — inbox provider-seam resolution. Authored (not implemented) after
  CE-8 and EIAT-2 both dead-ended on the same seam and no atom owned the fix; CE-8's own log states it
  "belongs to the INBOX/provider-seam contract owner", which is this plan.
  **Evidence gathered against `main` a2e874a8, measured not inferred:** `PROVIDER_TYPES`
  (`apps/manifest.py:613`) declares 18 types; `providers/registry.py` registers 14 handlers; the four
  declarable-but-unhandled types are `agent`, `inbox`, `notification`, `skills` — each installs clean
  and then does nothing (the #47 rule). The guard that exists to prevent this,
  `TestProviderTypesMatchHandlers`, is documented as asserting EQUALITY but asserts only
  `handlers - PROVIDER_TYPES`; it is structurally incapable of catching a declarable type with no
  handler, which is why four of them accumulated unnoticed. `inbox_providers.get_default_provider`
  already documents the dead end in its own `SEAM LIMIT` paragraph: resolution reads only the
  `personalclaw.message_source_providers` entry-point group, an app can never contribute an entry point
  (installs never make the app an installed distribution), and `discover_providers` binds a module-level
  CLASS while a manifest declares a FACTORY — so both halves must change.
  **Two consumers unblocked on completion,** with app-side work already written and inert: CE-8 part 1
  (Slack inbox source, recorded BLOCKED-E1 twice by independent passes) and EIAT-2 (`mail-inbox`
  declares the identical `{"type":"inbox","implementation":"…:create_provider"}` shape).
  **Deliberately left to the implementing tick:** whether `agent`/`notification`/`skills` get handlers,
  get removed from `PROVIDER_TYPES`, or get allowlisted with the real mechanism named — the atom
  requires each to be resolved and forbids leaving any silently declarable-and-dead. Early reading is
  that `skills` is vestigial (apps seed skills via `apps/skill_seed.py`), but the implementer must
  re-confirm and must check no shipped app declares a type before removing it.
  Deps `[]` — every file it touches is already shipped, so it is READY now.

- **DONE `INU-8`** (inbox provider-seam resolution) — app-contributed inbox sources now resolve, and the
  #47 guard can finally see the class it was written for.
  **The atom's own premise was wrong, and the correction is the durable lesson.** It claimed "18
  declarable types vs 14 handlers → `agent`/`inbox`/`notification`/`skills` unhandled." That came from
  measuring the gap with the guard's OWN single-line regex, which silently skips multi-line
  `register_type_handler(` calls — and the skipped ones were exactly those types. Multiline-aware, all
  18 had a registration. Do not measure a gap with the instrument you suspect of under-reporting.
  **The real defect was subtler and worse-hiding:** `inbox` mapped to `EntitySeamHandler`, whose
  `create()` RUNS the manifest factory while `register()` is a deliberate no-op — so an app's provider
  was built at enable-time and thrown away, and `get_default_provider` still read only the entry-point
  group. Declarable → installs clean → silently dead: same #47 class via a different mechanism.
  `KnowledgeTypeHandler` (WS-1) and `ChannelTypeHandler` are the precedent for graduating a type off the
  seam no-op once a registry has a real consumer, so the atom's design steps 2/3/4/6 applied unchanged.
  **Shipped:** `inbox_providers/registry.py` (instances keyed by `source_name` — the manifest factory
  already ran and may close over app config, so it cannot be re-instantiated; the lambda-wrapper
  "normalise to a class" alternative is rejected in the docstring with the reason); `InboxTypeHandler`
  on the standard `load_factory` + `ProviderSettings` path; `get_default_provider` precedence
  app-instance → entry-point `cls()` → native → filesystem, with the stale `SEAM LIMIT` paragraph
  replaced by the real contract; real deregistration.
  **`agent`/`notification`/`skills` KEPT — the atom's "vestigial → remove" hypothesis was refuted by
  census:** `native-skills` declares `skills`, `native-agents` declares `agent`, `filesystem-inbox`
  declares `inbox`; removing any would have broken shipped native apps. The guard now enforces that each
  seam registration carries a non-empty `source_of_truth`, so the allowlist lives in code at the
  registration rather than a drifting test-side list.
  **Not inert:** bundled `filesystem-inbox` declares `type: inbox`, so the gateway's
  `get_default_provider("filesystem")` now resolves the app-registered instance today (verified
  `FilesystemSourceProvider` carries zero instance state, so behaviour is equivalent; falls back to the
  entry-point class when disabled). Pinned by a test.
  **Verified by removal, not assertion:** temporarily restoring the `inbox` → `EntitySeamHandler`
  registration turned the e2e suite red (`3 failed, 4 passed`, failing at `assert [] ==
  ['fixture-inbox']`), then the file was restored byte-identically.
  **Gate (re-run independently by the driving session):** `make lint` clean (mypy 798 files);
  inbox/app-manifest/entity-seam/agent-reference/inert-surface **94 passed**; `-k "inbox or provider or
  manifest"` **942 passed / 4 skipped / 1 xfailed**; `reference/providers.md` regenerated with **no
  drift**. No test or baseline weakened.
  **Hand-off:** CE-8 part 1 and EIAT-2 are now unblocked — their app-side adapters were already written
  and inert, waiting on exactly this seam.
  **Residual, deliberately out of scope:** the gateway still asks for `"filesystem"` by name; nothing
  lets a user SELECT a source (no `InboxConfig.source` field). Adding one pulls in the full config
  round-trip plus a frontend control, which this atom's done_when does not list and whose consumer is
  CE-8/EIAT-2 — a follow-up row for whoever lands CE-8 part 1.

- **INU-6 DONE (2026-08-15) — second-opinion verification gate.** `NotificationKind.verifiable`
  (set on `skills/proposal` + `system/agent_request`) + a `Rule.verify` opt-in whose rules-PUT rejects
  `verify:true` on a non-verifiable kind (400). New `notification_verify.py`: `verify_attention_item()`
  is REFUTED-only via `one_shot_completion(use_case="background")` (metered through `ModelCallGuard`) and
  **fail-OPEN** on every failure path (no model / timeout / breaker / budget / unparseable → `skipped`,
  delivered). `ItemStatus.FILTERED` is filed inside `emit_attention_item()` between construction and
  `notify()`; a refuted item persists with `refs.verify` + a `verify_withheld` replay payload and its
  notification is withheld. FE: a present-only **Filtered** filter chip + a **Restore** control that
  flips FILTERED→PENDING and re-fires the withheld notification exactly once (server enforces the
  fire-once on the FILTERED→PENDING edge; a repeat call is a 409 no-op).
  **DEVIATION (execution, not scope):** implemented across two agents after transient subagent stream
  deaths — the backend landed as a wip checkpoint, then tests + FE + tracking were completed inline; the
  PR squashes both into one commit. Falsified three ways (PUT-400 removed, REFUTED-only inverted,
  fail-open path made blocking) — each reds the naming test.

- **INU-7 DONE (2026-08-15) — Proposals contract (C6) + app emission path + Proposals lens.**
  New `proposals_contract.py` owns the payload and the dispatcher; **it executes nothing itself** —
  each of the four cases calls the dispatcher that already exists (`action` → the action-provider
  registry + `provider.execute`; `workflow` → `workflows.service.start_run`; `skill_promotion` →
  `learning.proposals.accept(pid, installer=…)`; `app_callback` → `app_routes.resolve_route` +
  `call_app_route`). T4.1 is re-expressed rather than forked: `learning/proposals._surface_in_inbox`
  now attaches `refs["proposal"]` with `apply={"skill_promotion": {"pid"}}` and keeps
  `refs["learning_proposal"]`, so the skills accept path is unchanged and is simply one case of the
  contract. The case set is **closed** (`apply_case()` raises on zero/two/unknown; `_DISPATCH` asserted
  total against `ApplyCase` at import) because a default branch swallowing an unmapped value is a
  measured defect class here. **A failed apply keeps the item PENDING** and records
  `refs["proposal_error"]`, proven per case. App emission: `permissions.proposals[]` (`ProposalKind`,
  slug-validated at manifest-validate time), registered at enable as `("app:<name>",
  "proposal:<suffix>")` with `verifiable=True` so INU-6's skeptic may apply, deregistered on
  disable/uninstall via a new `notification_kinds.unregister` (a kind outliving its app is the phantom
  INU-8's `deregister` lesson names). `POST /api/inbox/proposals` reads identity from `request["app"]`
  only and 403s an undeclared kind or a foreign `app_callback`, one SEL row per emission either way;
  `POST /api/inbox/{id}/apply` answers 200 + `ok:false` on a failed apply because a status code cannot
  say "nothing happened and it is still here". FE `ProposalsLens` (inbox narrowed to `proposal`):
  batch-approve enabled only for one `(provenance, item_kind)` group — mixed selection renders
  `aria-disabled` + the reason on `title` (this kit's `disabledReason` convention: a natively disabled
  button leaves the tab order) — a batch is N applies with per-row outcomes, and edit-then-approve
  edits the **apply** payload, not the prose preview.
  **DEVIATION:** `apply.workflow` takes `ref` only, dropping C6's `{ref | inline}` sketch — nothing
  existing starts an unsaved inline def, and a declarable-but-dead shape is the #47 defect this plan
  keeps closing. Falsified four ways (failed apply marked handled; the undeclared-kind 403 skipped; a
  foreign `app_callback` allowed; batch-approve enabled for a mixed selection) — each reds its test.
