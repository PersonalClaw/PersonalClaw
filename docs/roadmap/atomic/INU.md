# INBOX-NOTIFICATIONS-UNIFICATION — atomic plans

**Source plan:** [`INBOX-NOTIFICATIONS-UNIFICATION`](../plans/INBOX-NOTIFICATIONS-UNIFICATION.md)  
**Code:** `INU`  
**Source status:** in_progress

8 atoms: all 8 done (S1-S5 as PRs #111-#115; S6 verification gate, S7 Proposals contract and S8 inbox provider-seam resolution closed since). No blocking cross-plan deps — plan owns the attention contracts; all cross-plan edges are downstream consumers.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `INU-1` | ✅ (##111) | Kind registry + rules engine (S1) | — | notification_kinds.py registry (frozen (source,kind,label,default_mode,default_severity)) covers every notify() emitter's kind; ~10/26 emitters migrated to typed constants (no bare-string kinds); notification_rules.py load/validate + resolve_rule with fail-open defaults + evaluation wired into notify() as THE delivery path (never/badge/immediate/digest); digest queue writer with 2x trim; guarded GET/PUT /api/notifications/rules; regression test proves absent rules file reproduces today's behavior for every kind |
| `INU-2` | ✅ (##112) | Inbox as the attention store (S2) | `INU-1` | InboxItem gains item_kind/refs/SEEN + non-channel id helper {kind}_{uuid8}_{ts} keeping the ts rsplit contract, tolerant from_dict loads old items; emit_attention_item() creates one PENDING item AND routes exactly one notify(); wired for needs_input at loop watchdog + gateway autopause (no double-fire); inbox service/API list/filter by item_kind, mark-SEEN on view, handled/dismissed for non-message kinds; frontend kind chips + kind-specific rows deep-linking refs |
| `INU-3` | ✅ (##113) | Settings unification + alert-fields backfill (S3) | `INU-1` | notifications settings page with global gate + source-by-kind rules matrix (row per registered kind, grouped by source) + digest schedule field, edits persist and take effect without restart; idempotent data-inspection backfill projects inbox.json alert_keywords/alert_on_name_mention onto channel message/mention rule conditions then deletes those fields + PUT guards + inbox alert UI in the same change (re-run is a no-op, PUT to removed field 400s); docs/architecture/inbox-channels.md Notifications section rewritten to the rules model |
| `INU-4` | ✅ (##114) | Fold the proposal surfaces (S4) | `INU-1`, `INU-2` | skills enqueue() also emits a proposal item (refs.pid); inbox row accept/reject call skills/proposals.accept/reject resolving HANDLED/DISMISSED; idempotent backfill gives each pending proposal without an item exactly one (re-run no-op); skills approval tab cross-links the filtered inbox (single install path); outlived tool approvals mirror an agent_request item after a grace period (asyncio.shield-protected), answering either surface resolves both |
| `INU-5` | ✅ (##115) | Digest + demotion (S5) | `INU-1`, `INU-2` | notification-digest deterministic action provider registered as a silent system cron drains digest_queue.jsonl into one grouped digest inbox item (empty queue produces nothing); unread_count() derives from inbox PENDING and the notification log loses acked/unread semantics (becomes read-only delivery audit); CHANGELOG entry names the one-time badge reset with personalclaw snapshot advised |
| `INU-6` | ✅ | Second-opinion verification gate (S6) | `INU-1`, `INU-2`, `INU-3` | NotificationKind.verifiable + rule verify field (rules PUT 400s verify:true on a non-verifiable kind); notification_verify.py verify_attention_item() does REFUTED-only filtering via one_shot_completion(use_case=background), metered through ModelCallGuard, fail-OPEN on every failure path (no model/timeout/parse-fail/budget → verify:skipped); ItemStatus.FILTERED added, hooked inside emit_attention_item() between construction and notify() (refuted → FILTERED + refs.verify + no notification); Filtered chip + Restore flips FILTERED->PENDING firing the withheld notification once; V6 drives true+planted-false proposals and an unbound model (both surface skipped) |
| `INU-7` | ✅ | Proposals contract + app emission path (S7) | `INU-4`, `INU-6` | C6 Proposal dataclass + apply dispatcher routes the four apply cases (action/workflow/skill_promotion/app_callback) through EXISTING dispatchers, a failed apply keeps the item PENDING with the error, and T4.1's skill path is re-expressed as skill_promotion; app emission adds permissions.proposals manifest field + kind registration at enable-time + POST /api/inbox/proposals (scoped-token identity, 403 on undeclared kind or foreign app_callback, SEL per emission, app proposals verifiable=True by default); inbox Proposals lens with same-(provenance,kind)-only batch-approve (mixed sweeps impossible in UI) + edit-then-approve for editable payloads |

## Atom scopes
| `INU-8` | ✅ | Resolve app-contributed inbox sources through the app registry's manifest factory (`InboxTypeHandler`) + make the `PROVIDER_TYPES`/handler guard bidirectional | — | An app declaring `{"type":"inbox","implementation":"mod:factory"}` has its provider resolved+registered at enable-time by a real `InboxTypeHandler` (the same `load_factory` path every other type uses) and deregistered on disable/uninstall; `get_default_provider` resolves app sources with documented precedence and the class-vs-instance mismatch handled explicitly; a fixture app's source is driven end-to-end; the #47 guard asserts BOTH directions with a reason-carrying allowlist and `agent` / `notification` / `skills` are each resolved (handler, removal, or allowlist naming the real mechanism) |

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

**Status:** done

Amendment (2026-07-26 — round 2, Proposals contract): Session 7 (T7.1-T7.3) + T4.1 re-expression; C6

**Done when:** C6 Proposal dataclass + apply dispatcher routes the four apply cases (action/workflow/skill_promotion/app_callback) through EXISTING dispatchers, a failed apply keeps the item PENDING with the error, and T4.1's skill path is re-expressed as skill_promotion; app emission adds permissions.proposals manifest field + kind registration at enable-time + POST /api/inbox/proposals (scoped-token identity, 403 on undeclared kind or foreign app_callback, SEL per emission, app proposals verifiable=True by default); inbox Proposals lens with same-(provenance,kind)-only batch-approve (mixed sweeps impossible in UI) + edit-then-approve for editable payloads

**DONE (2026-08-15).** `proposals_contract.py` owns C6 (`Proposal` + `ApplyOutcome`) and the
dispatcher; every case CALLS an existing dispatcher and this module executes nothing itself:
`action` → `action_providers.registry.get_action_provider` + `provider.execute(config, ActionContext)`;
`workflow` → `workflows.service.start_run` (idempotency key `proposal:<item id>`, so a double-click is
a retry); `skill_promotion` → `learning.proposals.accept(pid, installer=…)`, the T4.1 path re-expressed
as one case (its `_surface_in_inbox` now attaches `refs["proposal"]` with
`apply={"skill_promotion": {"pid"}}` and keeps `refs["learning_proposal"]` for existing readers);
`app_callback` → `tool_providers.app_routes.resolve_route` + `call_app_route` (the owner's reverse
proxy, `agentCallable` gate and app-scoped token included). The case set is closed: `apply_case()`
raises on zero/two/unknown keys and `_DISPATCH` is asserted total against `ApplyCase` at import, so no
unmapped value can fall through a default. A failed apply leaves the status untouched and records
`refs["proposal_error"]` (asserted per case). App emission: `permissions.proposals[]` (`ProposalKind`,
round-tripped + validated as a slug so a bad suffix can't break the `<source>/<kind>` rules key),
registered at enable time as `("app:<name>", "proposal:<suffix>")` with `attention=True` +
`verifiable=True`, deregistered on disable/uninstall (new `notification_kinds.unregister`) so no
phantom kind outlives its app; `POST /api/inbox/proposals` takes identity from `request["app"]` only
and 403s an undeclared kind or a foreign `app_callback`, with a SEL row per emission (granted and
denied). `POST /api/inbox/{id}/apply` returns 200 + `ok:false` on failure — the row is still PENDING.
Frontend: narrowing the inbox to `proposal` swaps in `ProposalsLens`, where batch-approve is enabled
only for a single `(provenance, item_kind)` group (mixed → `aria-disabled` + a reason on `title`), a
batch is N applies with per-row outcomes, and an `editable` payload is edited as the **apply** payload
(editing the prose preview would have been an inert control).

DEVIATION: `apply.workflow` accepts `ref` only, not C6's sketched `{ref | inline}` — no existing
dispatcher starts an unsaved inline definition, and declaring a shape nothing serves is the repo's #47
defect. A producer saves the def first, then proposes its name.

### `INU-8` — Inbox provider-seam resolution: app-contributed sources + a bidirectional #47 guard (S8)

**Status:** ✅ done (#PENDING)

Provider-seam contract change owned by this plan. Created 2026-08-11 after CE-8 and EIAT-2 both dead-ended on it.

**Done when:** an app declaring `{"type":"inbox","implementation":"mod:factory"}` in `app.json` has its provider resolved and registered at enable-time by a real `InboxTypeHandler` (registered through `register_type_handler("inbox", ...)`, the same `load_factory` path every other app provider type uses) and DEREGISTERED on disable/uninstall so no phantom source survives; `inbox_providers.get_default_provider(name)` resolves app-contributed sources with a documented precedence (app-registry factory → entry-point group → native → filesystem) and its `SEAM LIMIT` docstring is replaced by the real contract; the class-vs-instance shape mismatch is handled explicitly rather than duck-typed at the call site; a fixture app's inbox source is driven end-to-end (declare → enable → messages flow through the generic seam → disable → source gone); `test_provider_types_equal_registered_handlers` asserts BOTH directions with an explicit reason-carrying allowlist, and `agent` / `notification` / `skills` are each resolved per type — no type is left silently declarable-and-dead.

#### Why this is a seam defect, not a per-app bug

Measured on `main` (a2e874a8), not inferred from plan text:

- **CORRECTED 2026-08-11 during implementation — the original count here was wrong, and the way it was wrong is the lesson.** This atom first claimed "18 declarable types vs **14** handlers, so `agent`/`inbox`/`notification`/`skills` are unhandled." That was an artifact of measuring the gap with the guard's OWN regex, `register_type_handler\("([a-z_]+)"`, which is single-line and silently skips registrations written across multiple lines — and those skipped ones are exactly `agent`, `notification`, `skills`. Multiline-aware, **all 18 types have a registration**. Never measure a gap with the instrument you suspect of under-reporting.
- **The defect is real, in a form that hides better.** `inbox` was mapped to `EntitySeamHandler`, whose `create()` *runs* the manifest factory and whose `register()` is a deliberate no-op — so an app's provider was constructed at enable-time and then **thrown away**, while `get_default_provider` still read only the entry-point group. Declarable → installs clean → silently dead: the same **#47** class, reached by a different mechanism. `KnowledgeTypeHandler` (WS-1) and `ChannelTypeHandler` are the precedent for graduating a type off the seam no-op once a registry has a real consumer.
- The guard that claims to prevent exactly this asserts only one direction. `tests/test_app_manifest.py::TestProviderTypesMatchHandlers` is documented as *"PROVIDER_TYPES ... MUST equal the set of provider types the runtime actually registers a handler for"*, but its assertion is `missing = handlers - set(PROVIDER_TYPES)`. That catches a handler with no declarable type (install-blocked, loud). It **cannot** catch a declarable type with no handler (installs fine, silently dead). The rail has never been able to see this class.
- `inbox_providers/get_default_provider()` already documents the dead end in its own `SEAM LIMIT` paragraph: resolution reads **only** the `personalclaw.message_source_providers` entry-point group; the install pipeline pip-installs an app's declared dependencies but never makes the app itself an installed distribution, so an app can contribute no entry point; and `discover_providers` binds a module-level `Provider`/`<Name>Provider` **class**, so a manifest's `create_provider` **factory** would be invisible even if it could. Both halves must change.

Two consumers are already blocked on it, with their app-side work written and inert: **CE-8 part 1** (Slack inbox source over the existing `RealSlackClient`, recorded BLOCKED-E1 twice by independent passes) and **EIAT-2** (`mail-inbox` declares the identical `{"type":"inbox","implementation":"mail_inbox_runtime.provider:create_provider"}` shape). CE-8's log states the fix "belongs to the INBOX/provider-seam contract owner" — this plan.

#### Design

**1. `InboxTypeHandler` — use the uniform pattern, invent nothing.** Every working type handler is the same three methods: `create()` calls `providers.loader.load_factory(ext)` (which resolves `module:factory` from the manifest) and applies `ProviderSettings.load(ext.name)`; `register()` puts the instance into its domain registry; `deregister()` removes it. `InboxTypeHandler` follows that shape exactly and registers into a new app-source registry the inbox seam reads. Registering it in the same commit as it becomes reachable satisfies the #47 rule for `inbox`.

**2. Resolve the class-vs-instance mismatch deliberately.** The entry-point path yields a **class** (`get_message_providers() -> dict[str, type]`, instantiated as `cls()` at the end of `get_default_provider`). The manifest path yields an **instance** (the factory already ran, and it may close over app config/credentials — re-instantiating it is not possible). So the app registry must hold **instances**, and `get_default_provider` must not assume it can call `cls()` on whatever it finds. Preferred shape: keep the two registries separate and give `get_default_provider` an explicit precedence chain that returns an instance from the app registry directly, falling through to `cls()` only on the entry-point path. Rejected alternative: normalise app instances into "classes" via a lambda/factory wrapper — it makes `dict[str, type]` a lie and pushes the shape confusion into every future reader.

**3. Precedence, stated once and documented.** `app-contributed (manifest factory) → entry-point group → native → filesystem`. App-contributed wins so an installed Slack/mail app actually takes its `source_name`; `native` and `filesystem` stay the terminal fallbacks the current docstring promises. The `SEAM LIMIT` paragraph is deleted and replaced by this contract — a stale "do not mistake this for a working path" warning left next to a working path is its own defect.

**4. Deregistration is load-bearing.** `DutyGateTypeHandler`'s comment records why: an unregistered duty gate fails **open**. The inbox analogue is a phantom source — a disabled/uninstalled app's source still answering `get_default_provider`, so messages appear to flow from an app that is gone. `deregister()` must be real and covered by the end-to-end fixture (disable → source gone), not assumed.

**5. Repair the guard, then resolve the other three honestly.** Make the assertion bidirectional. For the reverse direction, an allowlist is legitimate **only** when it carries the mechanism that actually serves the type. Verified starting points, each to be re-confirmed by the implementer rather than trusted here:
- **RESOLVED 2026-08-11 by census, and the "vestigial → remove" hypothesis below was REFUTED.** A sweep of every shipped `app.json` found `native-skills` declares `"type":"skills"`, `native-agents` declares `"type":"agent"`, and `filesystem-inbox` declares `"type":"inbox"`. Removing `skills` or `agent` would have broken shipped native apps. All three keep their reason-carrying `EntitySeamHandler`, and the repaired guard now *enforces* that the reason exists (`source_of_truth` non-empty) — so the allowlist lives in code at the registration site rather than in a test-side list that drifts. `notification` is declared by no manifest but is served by the mechanism its registration names (`entity_settings/notifications.json` + `DashboardState.notify()`'s gate).
- Original (wrong) hypothesis, kept for provenance: *"`skills` looks vestigial since apps seed skills via `apps/skill_seed.py` → most likely remove from `PROVIDER_TYPES`."* The atom required a census before removal precisely because a guess like this can break shipped apps; the census is what caught it.
Deleting a declarable type is a manifest-contract narrowing: check no shipped app declares it before removal, and record the check.

#### Implementation plan

1. **Verify the premise still holds** (2 min, cheap): recompute the `PROVIDER_TYPES` − handlers difference and re-read the `SEAM LIMIT` docstring. If either has moved, stop and re-derive — this atom's whole shape depends on that gap.
2. **Add the app-source registry** the inbox seam reads (instances, keyed by `source_name`), with register/deregister and no import cycle back into `providers/`.
3. **Add `InboxTypeHandler`** in `providers/registry.py` using the `load_factory` + `ProviderSettings` pattern, and register it via `register_type_handler("inbox", ...)`.
4. **Rewrite `get_default_provider`** to the documented precedence chain, returning app instances directly and reserving `cls()` for the entry-point path. Replace the `SEAM LIMIT` docstring with the contract.
5. **Make the guard bidirectional** in `tests/test_app_manifest.py`, with the reason-carrying allowlist; then resolve `agent` / `notification` / `skills` per the design (handler, removal, or reasoned allowlist) in the same change, so the guard goes green honestly rather than by exemption.
6. **Fixture-app end-to-end test**: declare an `inbox` provider → enable → assert the seam resolves that source and a message flows through the generic path → disable → assert the source is gone. This is the test that would have failed against the old divergence; prove it fails without the handler.
7. **Gate**: `make lint`; `pytest` over `test_app_manifest`, the inbox suites, `test_manifest_types_match_handlers`, plus the full-suite-only ratchets a new provider type trips (`agent_reference` for `reference/providers.md` regen, `inert_surface_baseline`). Regenerate the reference — a new `PROVIDER_TYPES`/handler pair drifts `reference/providers.md`.
8. **Hand off**: note in the Execution log that CE-8 part 1 and EIAT-2 are now unblocked, so their owners can land the staged app-side adapters.

**Scope guard — what this atom is NOT.** It does not implement the Slack or mail sources (CE-8 / EIAT-2 own those), does not touch the entry-point mechanism for non-app providers, and does not redesign the inbox item model. It makes one seam resolve app-declared providers the way every other seam already does, and makes the guard capable of noticing the next time it doesn't.
