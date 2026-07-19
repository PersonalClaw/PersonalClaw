# Plan: Inbox/Notifications Unification — One Attention Store, One Configurable Delivery Layer

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18 from the owner-commissioned boundary investigation)
**Created:** 2026-07-18
**Wave:** 1 (S1-3) + 2 (S4-5). **Change class B** (persisted-state changes; gate `inbox_unification`) — the designated **first full exercise of LIFECYCLE-DOCTRINE** (gate → dual-path → migrate → cleanup).
**Depends on:** LIFECYCLE-DOCTRINE S1-2 (gates + migration runner). Coordinates with: LEARNING-FLYWHEEL §2.2 (its proposal queue lands AS inbox kind=proposal — one attention surface, not a fourth); PROACTIVE-ASSISTANT (the digest built here is its pulled-forward ambient slice); CHANNEL-EXPANSION (channel DM as a rules target; pairing prompts become `agent_request` items); MOBILE-COMPANION (the `push` target activates there).
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
- **Demotion (S5):** notification log becomes a delivery audit (acked/unread semantics removed); `unread_count()` re-derives from inbox; dashboard badge + notifications panel read the new truth; old alert fields in `inbox.json` removed after migration.

### Lifecycle artifacts (per LIFECYCLE-DOCTRINE)

- Gate `inbox_unification` (class B, default OFF until S3 completes, ON for fresh installs at S4, removal one release after default-ON).
- Migrations: `m_YYYYMMDD_inbox_alert_fields_to_rules` (inbox.json keywords/name-mention → conditions on the channel message/mention rules; removes old fields), `m_YYYYMMDD_pending_skill_proposals_to_inbox` (existing pending proposals get inbox items; idempotent by pid).

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

### Session 1 — Kind registry + rules engine (additive, gate OFF = zero behavior change)

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `notification_kinds.py`: registry dataclass + registrations covering every existing `notify()` call site's kind (enumerate by grep; record the inventory in the Execution log); constants exported | create `src/personalclaw/notification_kinds.py`, tests | every current emitter's kind string has a registration; duplicate registration raises |
| T1.2 | Migrate the ~10 emitters to typed constants (mechanical; zero behavior change — same strings flow through) | the 10 listed modules | grep finds no bare-string kinds at call sites; suite green |
| T1.3 | Rules store + evaluation: load/validate `notification_rules.json` (guarded PUT routes beside `entity_routes.py` patterns), `resolve_rule(source, kind) -> Rule` with registry defaults + corrupt-file fail-open; evaluation wired into `notify()` **behind the gate** (gate OFF → exact legacy path, byte-identical notes) | `src/personalclaw/notification_rules.py`, `providers/entity_routes.py`, `dashboard/state.py` | with gate OFF: existing notification tests green unchanged; with gate ON in tests: never/badge/immediate/digest each behave per Design |
| T1.4 | Register gate `inbox_unification` (class B, this plan) + digest queue writer (append-only JSONL, trim at 2× cap) | `lifecycle/gates.py` site, `notification_rules.py` | gate listed; queue writes covered by test |
| V1 | Validation: gate OFF — drive chat/cron/loop notifications as a user, confirm zero visible change; gate ON (dev home) — set a `never` rule and a `digest` rule, observe drop + queue append | — | both states verified; ledger written |

### Session 2 — Inbox as the attention store

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `InboxItem` extension: `item_kind` (default message), `refs`, `SEEN` status, id helper for non-channel kinds (`{kind}_{uuid8}_{ts}`); `from_dict` tolerance test for old items | `src/personalclaw/inbox.py`, tests | old fixture items load; new kinds round-trip; `ts` property holds for both id shapes |
| T2.2 | Emit-side helpers: `emit_attention_item(kind, source, title, body, refs, notify_rule=...)` — creates the inbox item AND routes one notification through `notify()` (single choke point preserved); wired for `needs_input` (loop watchdog + gateway autopause sites) behind the gate | `inbox.py`, `loop/watchdog.py`, gateway autopause site | gate ON: a loop needs-input produces one PENDING `needs_input` item + one immediate notification; gate OFF: legacy behavior only |
| T2.3 | Inbox service/API: list/filter by `item_kind`, mark-SEEN on view, handled/dismissed transitions for non-message kinds (no draft/reply machinery for them) | `inbox_service.py`, `dashboard/handlers/` inbox routes | API filter returns kinds; transitions persist; message-kind behavior untouched |
| T2.4 | Frontend inbox: kind filter chips + kind-specific row rendering (needs_input rows deep-link their loop/session via `refs`) | `web/src/pages/inbox/` components | chips filter; deep links navigate (URL-state doctrine respected) |
| V2 | Validation: run a loop to a checkpoint → needs_input appears in inbox, deep-links to the loop, resolves to HANDLED on answer; message items unaffected | — | holds |

### Session 3 — Settings unification (frontend) + migration #1

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Notifications settings page: global gate section (existing controls) + rules matrix (source×kind grid: mode selector, targets, conditions editor) + digest schedule field; served from the rules store | `web/src/pages/settings/` (new panel beside MemoryPanel pattern), rules PUT routes | matrix edits persist and take effect on next notify() (no restart) |
| T3.2 | Migration `m_*_inbox_alert_fields_to_rules` per Design (+ removal of the old fields and their PUT guards after migration; inbox settings page loses the alert section, points at Notifications) | `lifecycle/migrations/m_*.py`, `providers/entity_routes.py`, inbox settings component | migration fixture: keywords/name-mention reappear as conditions; doctor shows applied; old fields gone post-migration |
| T3.3 | Docs: `docs/architecture/inbox-channels.md` Notifications section rewritten to the rules model (mark legacy paragraphs removed); configuration reference updated | the two docs | docs match gate-ON behavior |
| V3 | Validation: as a user — configure a keyword condition in the new UI, send a matching channel message (echo transport), observe the escalated immediate notification; non-matching stays badge | — | holds |

### Session 4 — Fold the proposal surfaces (Wave 2)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Skills proposals → inbox: `enqueue()` also emits a `proposal` item (refs.pid); accept/reject actions on the inbox row call `skills/proposals.accept/reject`; item resolves HANDLED/DISMISSED accordingly | `skills/proposals.py`, inbox handlers, inbox frontend row actions | proposing → item appears; approve from inbox installs the skill (existing accept path); reject dismisses |
| T4.2 | Migration `m_*_pending_skill_proposals_to_inbox` (idempotent by pid) | `lifecycle/migrations/m_*.py` | fixture with 3 pending proposals gains exactly 3 items; re-run no-op |
| T4.3 | Skills page's approval tab becomes a filtered-inbox embed (component reuse — one surface, no second approval UI to maintain) — or, if embed friction is high, links to the filtered inbox (record choice as DEVIATION/decision) | `web/src/pages/skills/` | one code path renders proposals everywhere |
| T4.4 | Session-modal approvals: when a tool-approval prompt outlives its session view (unanswered > TTL or session backgrounded), mirror an `agent_request` item (refs.session, deep-link to the approval); answering either surface resolves both | approval prompt machinery (`chat_runner`/approval path — locate `resume`/`approve` route), inbox wiring | timed-out approval appears in inbox; answering from inbox unblocks the session |
| V4 | Validation: full proposal round-trip from inbox; a backgrounded approval recovered via inbox on a phone-sized viewport | — | holds |

### Session 5 — Digest + demotion + cleanup (Wave 2)

| ID | Task | Files | Done when |
|---|---|---|---|
| T5.1 | Digest cron: system cron registration (respects `--no-crons`), drain queue → grouped `digest` inbox item + delivery per rule targets (dashboard + channel_dm via existing `deliver_notification`/`deliver_cron_result` precedent — record which fits) | `notification_rules.py` digest builder, cron registration site (`schedule.py` patterns) | scheduled run produces one digest item with correct grouping; empty queue → no item |
| T5.2 | Demotion: `unread_count()` derives from inbox PENDING; notification log loses acked/unread semantics (becomes delivery audit; panel renders read-only history); badge + panel wired to inbox truth | `dashboard/state.py`, notifications panel component | badge counts inbox PENDING only; audit panel shows deliveries; no unread semantics remain on the log |
| T5.3 | Cleanup per doctrine: gate default-ON for fresh installs → flip note in CHANGELOG → legacy code paths (pre-rules notify branch, old unread derivation) deleted; gate registered for removal next release | `dashboard/state.py`, gates registry, `CHANGELOG.md` | grep finds no gate-OFF branches; gate marked for removal; suite green |
| V5 | Validation: 24h dogfood on the owner's real instance (owner task 2) — digest arrives on schedule with the day's badge-mode items; unread badge tracks inbox exactly; SEL/audit review shows sane delivery history | — | owner-confirmed; ledger written |

## Owner tasks (real world)

1. **Design review of the rules matrix UX** (S3 — 30 min): the source×kind grid is the plan's main UI bet; approve or redirect before frontend build.
2. **24h dogfood** of the completed system on your real instance (V5) — the digest schedule, rule defaults, and badge behavior are taste calls only real use validates.
3. Decide the **default digest time** (08:00 local proposed) and which kinds default to `digest` vs `badge` (proposal: mentions=immediate, messages=badge, proposals=digest, needs_input=immediate, system=badge).

## Risks & open questions

- **Risk — double-notification during dual-path:** with the gate ON, an emitter migrated in S2 must not ALSO fire its legacy notification; the emit-helper owns both halves — conformance asserted per migrated site (test per emitter).
- **Risk — inbox page becomes a junk drawer:** kind chips + sane defaults (above) are the mitigation; PROACTIVE-ASSISTANT owns real triage intelligence later.
- **Open:** whether `digest` items themselves notify `immediate` on the dashboard (proposal: yes, once, at digest delivery — it IS the morning knock).
