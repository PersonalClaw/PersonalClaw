# INBOX-NOTIFICATIONS-UNIFICATION — atomic plans

**Source plan:** [`INBOX-NOTIFICATIONS-UNIFICATION`](../plans/INBOX-NOTIFICATIONS-UNIFICATION.md)  
**Code:** `INU`  
**Source status:** in_progress

7 atoms: 5 done (S1-S5, PRs #111-#115), 2 todo (S6 verification gate, S7 Proposals contract). No blocking cross-plan deps — plan owns the attention contracts; all cross-plan edges are downstream consumers.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `INU-1` | ✅ (##111) | Kind registry + rules engine (S1) | — | notification_kinds.py registry (frozen (source,kind,label,default_mode,default_severity)) covers every notify() emitter's kind; ~10/26 emitters migrated to typed constants (no bare-string kinds); notification_rules.py load/validate + resolve_rule with fail-open defaults + evaluation wired into notify() as THE delivery path (never/badge/immediate/digest); digest queue writer with 2x trim; guarded GET/PUT /api/notifications/rules; regression test proves absent rules file reproduces today's behavior for every kind |
| `INU-2` | ✅ (##112) | Inbox as the attention store (S2) | `INU-1` | InboxItem gains item_kind/refs/SEEN + non-channel id helper {kind}_{uuid8}_{ts} keeping the ts rsplit contract, tolerant from_dict loads old items; emit_attention_item() creates one PENDING item AND routes exactly one notify(); wired for needs_input at loop watchdog + gateway autopause (no double-fire); inbox service/API list/filter by item_kind, mark-SEEN on view, handled/dismissed for non-message kinds; frontend kind chips + kind-specific rows deep-linking refs |
| `INU-3` | ✅ (##113) | Settings unification + alert-fields backfill (S3) | `INU-1` | notifications settings page with global gate + source-by-kind rules matrix (row per registered kind, grouped by source) + digest schedule field, edits persist and take effect without restart; idempotent data-inspection backfill projects inbox.json alert_keywords/alert_on_name_mention onto channel message/mention rule conditions then deletes those fields + PUT guards + inbox alert UI in the same change (re-run is a no-op, PUT to removed field 400s); docs/architecture/inbox-channels.md Notifications section rewritten to the rules model |
| `INU-4` | ✅ (##114) | Fold the proposal surfaces (S4) | `INU-1`, `INU-2` | skills enqueue() also emits a proposal item (refs.pid); inbox row accept/reject call skills/proposals.accept/reject resolving HANDLED/DISMISSED; idempotent backfill gives each pending proposal without an item exactly one (re-run no-op); skills approval tab cross-links the filtered inbox (single install path); outlived tool approvals mirror an agent_request item after a grace period (asyncio.shield-protected), answering either surface resolves both |
| `INU-5` | ✅ (##115) | Digest + demotion (S5) | `INU-1`, `INU-2` | notification-digest deterministic action provider registered as a silent system cron drains digest_queue.jsonl into one grouped digest inbox item (empty queue produces nothing); unread_count() derives from inbox PENDING and the notification log loses acked/unread semantics (becomes read-only delivery audit); CHANGELOG entry names the one-time badge reset with personalclaw snapshot advised |
| `INU-6` | ⬜ | Second-opinion verification gate (S6) | `INU-1`, `INU-2`, `INU-3` | NotificationKind.verifiable + rule verify field (rules PUT 400s verify:true on a non-verifiable kind); notification_verify.py verify_attention_item() does REFUTED-only filtering via one_shot_completion(use_case=background), metered through ModelCallGuard, fail-OPEN on every failure path (no model/timeout/parse-fail/budget → verify:skipped); ItemStatus.FILTERED added, hooked inside emit_attention_item() between construction and notify() (refuted → FILTERED + refs.verify + no notification); Filtered chip + Restore flips FILTERED->PENDING firing the withheld notification once; V6 drives true+planted-false proposals and an unbound model (both surface skipped) |
| `INU-7` | ⬜ | Proposals contract + app emission path (S7) | `INU-4`, `INU-6` | C6 Proposal dataclass + apply dispatcher routes the four apply cases (action/workflow/skill_promotion/app_callback) through EXISTING dispatchers, a failed apply keeps the item PENDING with the error, and T4.1's skill path is re-expressed as skill_promotion; app emission adds permissions.proposals manifest field + kind registration at enable-time + POST /api/inbox/proposals (scoped-token identity, 403 on undeclared kind or foreign app_callback, SEL per emission, app proposals verifiable=True by default); inbox Proposals lens with same-(provenance,kind)-only batch-approve (mixed sweeps impossible in UI) + edit-then-approve for editable payloads |

## Atom scopes

### `INU-1` — Kind registry + rules engine (S1)

**Status:** done (PR ##111)

Session 1 — Kind registry + rules engine (T1.1-T1.4, V1); C1/C2/C3

**Done when:** notification_kinds.py registry (frozen (source,kind,label,default_mode,default_severity)) covers every notify() emitter's kind; ~10/26 emitters migrated to typed constants (no bare-string kinds); notification_rules.py load/validate + resolve_rule with fail-open defaults + evaluation wired into notify() as THE delivery path (never/badge/immediate/digest); digest queue writer with 2x trim; guarded GET/PUT /api/notifications/rules; regression test proves absent rules file reproduces today's behavior for every kind

### `INU-2` — Inbox as the attention store (S2)

**Status:** done (PR ##112)

Session 2 — Inbox as the attention store (T2.1-T2.4, V2); C4/C5

**Done when:** InboxItem gains item_kind/refs/SEEN + non-channel id helper {kind}_{uuid8}_{ts} keeping the ts rsplit contract, tolerant from_dict loads old items; emit_attention_item() creates one PENDING item AND routes exactly one notify(); wired for needs_input at loop watchdog + gateway autopause (no double-fire); inbox service/API list/filter by item_kind, mark-SEEN on view, handled/dismissed for non-message kinds; frontend kind chips + kind-specific rows deep-linking refs

### `INU-3` — Settings unification + alert-fields backfill (S3)

**Status:** done (PR ##113)

Session 3 — Settings unification (frontend) + alert-fields backfill (T3.1-T3.3, V3)

**Done when:** notifications settings page with global gate + source-by-kind rules matrix (row per registered kind, grouped by source) + digest schedule field, edits persist and take effect without restart; idempotent data-inspection backfill projects inbox.json alert_keywords/alert_on_name_mention onto channel message/mention rule conditions then deletes those fields + PUT guards + inbox alert UI in the same change (re-run is a no-op, PUT to removed field 400s); docs/architecture/inbox-channels.md Notifications section rewritten to the rules model

### `INU-4` — Fold the proposal surfaces (S4)

**Status:** done (PR ##114)

Session 4 — Fold the proposal surfaces (Wave 2) (T4.1-T4.4, V4)

**Done when:** skills enqueue() also emits a proposal item (refs.pid); inbox row accept/reject call skills/proposals.accept/reject resolving HANDLED/DISMISSED; idempotent backfill gives each pending proposal without an item exactly one (re-run no-op); skills approval tab cross-links the filtered inbox (single install path); outlived tool approvals mirror an agent_request item after a grace period (asyncio.shield-protected), answering either surface resolves both

### `INU-5` — Digest + demotion (S5)

**Status:** done (PR ##115)

Session 5 — Digest + demotion (Wave 2) (T5.1-T5.3, V5)

**Done when:** notification-digest deterministic action provider registered as a silent system cron drains digest_queue.jsonl into one grouped digest inbox item (empty queue produces nothing); unread_count() derives from inbox PENDING and the notification log loses acked/unread semantics (becomes read-only delivery audit); CHANGELOG entry names the one-time badge reset with personalclaw snapshot advised

### `INU-6` — Second-opinion verification gate (S6)

**Status:** todo

Amendment (2026-07-26 — verification gate): Session 6 (T6.1, T6.2, V6); C1.verifiable, C2.verify

**Done when:** NotificationKind.verifiable + rule verify field (rules PUT 400s verify:true on a non-verifiable kind); notification_verify.py verify_attention_item() does REFUTED-only filtering via one_shot_completion(use_case=background), metered through ModelCallGuard, fail-OPEN on every failure path (no model/timeout/parse-fail/budget → verify:skipped); ItemStatus.FILTERED added, hooked inside emit_attention_item() between construction and notify() (refuted → FILTERED + refs.verify + no notification); Filtered chip + Restore flips FILTERED->PENDING firing the withheld notification once; V6 drives true+planted-false proposals and an unbound model (both surface skipped)

### `INU-7` — Proposals contract + app emission path (S7)

**Status:** todo

Amendment (2026-07-26 — round 2, Proposals contract): Session 7 (T7.1-T7.3) + T4.1 re-expression; C6

**Done when:** C6 Proposal dataclass + apply dispatcher routes the four apply cases (action/workflow/skill_promotion/app_callback) through EXISTING dispatchers, a failed apply keeps the item PENDING with the error, and T4.1's skill path is re-expressed as skill_promotion; app emission adds permissions.proposals manifest field + kind registration at enable-time + POST /api/inbox/proposals (scoped-token identity, 403 on undeclared kind or foreign app_callback, SEL per emission, app proposals verifiable=True by default); inbox Proposals lens with same-(provenance,kind)-only batch-approve (mixed sweeps impossible in UI) + edit-then-approve for editable payloads

