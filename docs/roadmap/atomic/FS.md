# FEEDBACK-SIGNAL — atomic plans

**Source plan:** [`FEEDBACK-SIGNAL`](../plans/FEEDBACK-SIGNAL.md)  
**Code:** `FS`  
**Source status:** in_progress

FEEDBACK-SIGNAL decomposed into 6 atoms: FS-1/FS-2/FS-3 shipped (the 3 sessions), FS-5 shipped in AGENT-ROUTING, FS-6 shipped (PR #928 — suppression re-enforced at the live skill-surfacing gate). Only FS-4 (app-path validation fixture) remains, deferred-by-design todo.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `FS-1` | ✅ | Capture + attribution backend: feedback.py store, routes, SDK re-export, FeedbackConfig, producer meta on payloads (S1 / T1.1-T1.5) | — | feedback.jsonl store round-trips (record -> supersede-by-target -> producer_stats reflects only current verdicts; corrupt line skipped; 2x-cap trim; 0600); POST /api/feedback + target/producers/snooze/clear routes with §2.2 envelopes and server-side source_app + app-namespace forcing + enabled kill-switch 404; sdk/feedback re-export passes test_apps_import_boundary; FeedbackConfig passes test_config_roundtrip; inbox classify/draft/digest and loop findings carry {producer_kind,producer_id} + snapshot on their payloads. Log: [2026-07-27][S1] DONE. |
| `FS-2` | ✅ | FE affordance: FeedbackThumbs primitive + mounts on inbox judgment blocks and loop finding rows (S2 / T2.1-T2.3) | `FS-1` | quiet 👍/👎 pair with aria-pressed/filled-state, reduced-motion honored, skippable 'why' popover; thumb -> POST -> filled state -> re-thumb supersedes; verdict hydrates on reopen via GET /api/feedback/target; mounted on inbox classification/draft (+digest folded into detail per S2 deviation) and loop finding header. Log: [2026-07-27][S2] DONE. |
| `FS-3` | ✅ | Deterministic thresholds + retire proposals + Settings->AI accuracy table (S3 / T3.1-T3.3) | `FS-1` | suppressed_producers() fail-open + entity_settings/feedback.json (snoozed/cleared/retire_proposed); check_retire_candidates() rides InboxService.run_maintenance emitting one notify('feedback_retire') per crossing (dedup, snooze-reset); Settings->AI FeedbackPanel shows honest per-producer counts, min-N 'collecting' gate, suppressed badge, snooze/clear, registered in SUBPAGES + bento grid. Log: [2026-07-27][S3] DONE (skills/surfacing NOT wired per S3 deviation — no per-skill producer identity). |
| `FS-4` | ⬜ | App-path validation fixture: declared /api/feedback app records via sdk/feedback and route; undeclared app 403s (T2.4) | `FS-1` | a fixture app under tests/ declares /api/feedback in permissions.api, lands records via both sdk/feedback and the route with source_app set and producer forced to app:<name>:<producer>; an undeclared app path 403s (permission test green). Header notes T2.4 'remain unwired by design'. |
| `FS-5` | ✅ | Routing suggestion double-write into feedback (routing chip Route->up / dismiss->down) (T3.4) | `FS-1`, `EXT:AGENT-ROUTING:routing chip accept/dismiss handlers that call record_feedback` | routing accept/dismiss double-writes a ('routing_pair', pair) verdict so routing accuracy appears in the producers API with zero extra UI. Shipped inside AGENT-ROUTING (plan 56) rather than here; FEEDBACK-SIGNAL S3 left the coordination note when agents/routing.py was absent. Log: [2026-07-27][S3] T3.4 coordination note; header: 'shipped inside AGENT-ROUTING'. |
| `FS-6` | ✅ | Re-add a live gated consumer of suppressed_producers() so suppression is enforced again | `FS-3`, `EXT:WORKFLOWS-V2:a surfacing eligibility gate to host the `in suppressed_producers()` membership check`, `EXT:LEARNING-FLYWHEEL:skill-level producer identity/surfacing gate (alternative consumer)` | a runtime path withholds a suppressed producer: a producer below retire_threshold with n>=min_n stops surfacing at a live WF2 surfacing gate (or flywheel skill-surfacing eligibility filter), with a test proving suppression prevents surfacing. Today (verified 2026-08-04) no runtime path enforces it because WF2 Phase 1 deleted workflows.surfacing.eligible_workflows, T3.1's only gated consumer. |

## Atom scopes

### `FS-1` — Capture + attribution backend: feedback.py store, routes, SDK re-export, FeedbackConfig, producer meta on payloads (S1 / T1.1-T1.5)

**Status:** done

Session 1 (T1.1-T1.5); Design Layer 1 CAPTURE + Layer 2 ATTRIBUTION; Contracts C1 (FeedbackRecord/store/record_feedback/current_verdict/producer_stats), C3 (routes+SDK+WS), C4 (FeedbackConfig)

**Done when:** feedback.jsonl store round-trips (record -> supersede-by-target -> producer_stats reflects only current verdicts; corrupt line skipped; 2x-cap trim; 0600); POST /api/feedback + target/producers/snooze/clear routes with §2.2 envelopes and server-side source_app + app-namespace forcing + enabled kill-switch 404; sdk/feedback re-export passes test_apps_import_boundary; FeedbackConfig passes test_config_roundtrip; inbox classify/draft/digest and loop findings carry {producer_kind,producer_id} + snapshot on their payloads. Log: [2026-07-27][S1] DONE.

### `FS-2` — FE affordance: FeedbackThumbs primitive + mounts on inbox judgment blocks and loop finding rows (S2 / T2.1-T2.3)

**Status:** done

Session 2 (T2.1-T2.3); Design 'The FE affordance (S2)'; web/src/ui/FeedbackThumbs.tsx, InboxDetail.tsx, LoopsListPage/LoopPeek

**Done when:** quiet 👍/👎 pair with aria-pressed/filled-state, reduced-motion honored, skippable 'why' popover; thumb -> POST -> filled state -> re-thumb supersedes; verdict hydrates on reopen via GET /api/feedback/target; mounted on inbox classification/draft (+digest folded into detail per S2 deviation) and loop finding header. Log: [2026-07-27][S2] DONE.

### `FS-3` — Deterministic thresholds + retire proposals + Settings->AI accuracy table (S3 / T3.1-T3.3)

**Status:** done

Session 3 (T3.1-T3.3); Design Layer 3 LEARNING deterministic arm; Contract C2 (suppressed_producers/check_retire_candidates + entity_settings/feedback.json)

**Done when:** suppressed_producers() fail-open + entity_settings/feedback.json (snoozed/cleared/retire_proposed); check_retire_candidates() rides InboxService.run_maintenance emitting one notify('feedback_retire') per crossing (dedup, snooze-reset); Settings->AI FeedbackPanel shows honest per-producer counts, min-N 'collecting' gate, suppressed badge, snooze/clear, registered in SUBPAGES + bento grid. Log: [2026-07-27][S3] DONE (skills/surfacing NOT wired per S3 deviation — no per-skill producer identity).

### `FS-4` — App-path validation fixture: declared /api/feedback app records via sdk/feedback and route; undeclared app 403s (T2.4)

**Status:** todo

Session 2 T2.4 (App path validation); Design 'App boundary' seam; Contract C3 (source_app stamped server-side, app: namespace forcing)

**Done when:** a fixture app under tests/ declares /api/feedback in permissions.api, lands records via both sdk/feedback and the route with source_app set and producer forced to app:<name>:<producer>; an undeclared app path 403s (permission test green). Header notes T2.4 'remain unwired by design'.

### `FS-5` — Routing suggestion double-write into feedback (routing chip Route->up / dismiss->down) (T3.4)

**Status:** done

Session 3 T3.4 (plan-56 double-write); Design 'Routing suggestions' producer ('routing_pair', from->to)

**Done when:** routing accept/dismiss double-writes a ('routing_pair', pair) verdict so routing accuracy appears in the producers API with zero extra UI. Shipped inside AGENT-ROUTING (plan 56) rather than here; FEEDBACK-SIGNAL S3 left the coordination note when agents/routing.py was absent. Log: [2026-07-27][S3] T3.4 coordination note; header: 'shipped inside AGENT-ROUTING'.

### `FS-6` — Re-add a live gated consumer of suppressed_producers() so suppression is enforced again

**Status:** done

Status line ✅ DONE (PR #928); Contract C2 suppressed_producers(); host gate = `skills/surfacing.py::surface_skills` (the live turn-time gate via `SkillsLoader.get_surfaced_skills` → `context.py`), NOT `workflows/surfacing.py` — that path's `may_suggest`/`veto_reasons` are themselves inert (zero runtime callers), so hosting there would have re-created the inert-control defect FS-6 fixes. Producer identity for a skill = `("skill_synthesis", <key>)`; fail-open in depth. See `docs/roadmap/design-notes/fs6-suppression-consumer.md`.

**Done when:** a runtime path withholds a suppressed producer: a producer below retire_threshold with n>=min_n stops surfacing at a live WF2 surfacing gate (or flywheel skill-surfacing eligibility filter), with a test proving suppression prevents surfacing. Today (verified 2026-08-04) no runtime path enforces it because WF2 Phase 1 deleted workflows.surfacing.eligible_workflows, T3.1's only gated consumer.

