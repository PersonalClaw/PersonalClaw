# INVESTIGATE-ANYWHERE — atomic plans

**Source plan:** [`INVESTIGATE-ANYWHERE`](../plans/INVESTIGATE-ANYWHERE.md)  
**Code:** `IA2`  
**Source status:** done



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `IA2-1` | ✅ | investigate.py: InvestigateContext envelope + resolver registry + snapshot cap | — | registry dispatches (register_investigate_resolver/resolve); unknown kind raises KeyError, unknown entity returns None; oversized snapshot (~8KB) truncates with a visible notice line; unit tests in tests/test_investigate.py |
| `IA2-2` | ✅ | POST /api/investigate endpoint: resolve → create session → set ask mode → stage envelope | `IA2-1` | endpoint round-trips returning {session_key, context}; 404 unknown_entity / 400 unknown_kind per §2.2; task mode set via existing set_task_mode path; envelope staged on session._investigate_ctx (mirrored to session meta); SEL log_api_access(operation=investigate_open) present |
| `IA2-3` | ✅ | Turn-time injection _inject_investigate_context (first turn only, fenced once) | `IA2-1` | staged envelope reaches the model as fence_untrusted(snapshot, source=investigate:<kind>) + labelled preamble, joined to the attachments→knowledge→investigate chain at depth 0; second turn injects nothing; fence-break content in a snapshot is neutralised (test-proven); user-visible message untouched |
| `IA2-4` | ✅ | Frontend primitive: investigate() helper + InvestigateButton + ChatPage ContextChip + useInvestigate SDK export | `IA2-2` | button opens chat in ask mode with composer pre-filled (editable, never auto-sent) via ne:launch-chat with {session}; header ContextChip deep-links back via navigate() (not raw location.hash); useInvestigate exported in installAppSdk (Tier-S §2.8); web typecheck+vitest+build green |
| `IA2-5` | ✅ | Reference resolver pair (inbox_item + loop_finding) end-to-end from real pages | `IA2-1`, `IA2-4` | inbox_item (fenced body via inbox render path) and loop_finding (finding + cycle verdict) both investigable end-to-end; mounted on InboxDetail verdict row and LoopPeek finding header; V1 as-a-user pass (context chip, grounded answer, ask-mode mutation denied). DEVIATION: resolvers registered in investigate.py (not inbox_service/loop module) to avoid import-order coupling |
| `IA2-6` | ✅ | Adoption-sweep backend: 11 resolvers → 13 kinds, async-capable registry | `IA2-1` | each kind resolves a real seeded entity, all read-only (test asserts no store writes); registry made async-capable (resolve awaits coroutine resolvers); honest-scope calls handled — doctor findings re-run capability (dry_preview only, no persisted id), non-schedule triggers resolve last-run summary, memory_lesson accepts rule text, audit_event groups request_id neighbours via bounded tail scan; 22 new tests |
| `IA2-7` | ✅ | Adoption-sweep frontend: mount InvestigateButton across all surfaces + per-kind prompts/agents | `IA2-4`, `IA2-6` | 9 FE mounts of the ONE shared primitive (notifications, TaskDetail, ScheduleDetail runs, LoopCockpit cycle, KnowledgeDetail, MemoryPanel records+lessons, DoctorPanel cards, AuditPanel rows); grep gate clean (zero bespoke variants); per-kind opening prompts read naturally, agents conservative (empty unless clearly better); two dead-back-link route bugs fixed and locked by test_back_links_use_real_frontend_routes; V2 walk of all 13 kinds passes; full lint+test+web gate green |

## Atom scopes

### `IA2-1` — investigate.py: InvestigateContext envelope + resolver registry + snapshot cap

**Status:** done

S1 T1.1 / Contracts C1 — the envelope + resolver registry

**Done when:** registry dispatches (register_investigate_resolver/resolve); unknown kind raises KeyError, unknown entity returns None; oversized snapshot (~8KB) truncates with a visible notice line; unit tests in tests/test_investigate.py

### `IA2-2` — POST /api/investigate endpoint: resolve → create session → set ask mode → stage envelope

**Status:** done

S1 T1.2 / Contracts C2 — the endpoint (§2.2 error envelope)

**Done when:** endpoint round-trips returning {session_key, context}; 404 unknown_entity / 400 unknown_kind per §2.2; task mode set via existing set_task_mode path; envelope staged on session._investigate_ctx (mirrored to session meta); SEL log_api_access(operation=investigate_open) present

### `IA2-3` — Turn-time injection _inject_investigate_context (first turn only, fenced once)

**Status:** done

S1 T1.3 / Contracts C3 — turn-time injection beside _inject_knowledge_content

**Done when:** staged envelope reaches the model as fence_untrusted(snapshot, source=investigate:<kind>) + labelled preamble, joined to the attachments→knowledge→investigate chain at depth 0; second turn injects nothing; fence-break content in a snapshot is neutralised (test-proven); user-visible message untouched

### `IA2-4` — Frontend primitive: investigate() helper + InvestigateButton + ChatPage ContextChip + useInvestigate SDK export

**Status:** done

S1 T1.4 / Contracts C4 — Frontend + SDK

**Done when:** button opens chat in ask mode with composer pre-filled (editable, never auto-sent) via ne:launch-chat with {session}; header ContextChip deep-links back via navigate() (not raw location.hash); useInvestigate exported in installAppSdk (Tier-S §2.8); web typecheck+vitest+build green

### `IA2-5` — Reference resolver pair (inbox_item + loop_finding) end-to-end from real pages

**Status:** done

S1 T1.5 + V1 — reference pair + validation

**Done when:** inbox_item (fenced body via inbox render path) and loop_finding (finding + cycle verdict) both investigable end-to-end; mounted on InboxDetail verdict row and LoopPeek finding header; V1 as-a-user pass (context chip, grounded answer, ask-mode mutation denied). DEVIATION: resolvers registered in investigate.py (not inbox_service/loop module) to avoid import-order coupling

### `IA2-6` — Adoption-sweep backend: 11 resolvers → 13 kinds, async-capable registry

**Status:** done

S2 T2.1+T2.2 — resolver sweep (notification, task, schedule_run, trigger_run, loop_cycle, knowledge_item, memory_record, memory_lesson, doctor_finding, crash_report, audit_event)

**Done when:** each kind resolves a real seeded entity, all read-only (test asserts no store writes); registry made async-capable (resolve awaits coroutine resolvers); honest-scope calls handled — doctor findings re-run capability (dry_preview only, no persisted id), non-schedule triggers resolve last-run summary, memory_lesson accepts rule text, audit_event groups request_id neighbours via bounded tail scan; 22 new tests

### `IA2-7` — Adoption-sweep frontend: mount InvestigateButton across all surfaces + per-kind prompts/agents

**Status:** done

S2 T2.3+T2.4 + V2 — FE sweep, opening prompts/suggested agents, validation

**Done when:** 9 FE mounts of the ONE shared primitive (notifications, TaskDetail, ScheduleDetail runs, LoopCockpit cycle, KnowledgeDetail, MemoryPanel records+lessons, DoctorPanel cards, AuditPanel rows); grep gate clean (zero bespoke variants); per-kind opening prompts read naturally, agents conservative (empty unless clearly better); two dead-back-link route bugs fixed and locked by test_back_links_use_real_frontend_routes; V2 walk of all 13 kinds passes; full lint+test+web gate green

