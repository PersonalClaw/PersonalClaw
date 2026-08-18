# TEAM-SHARED-ENTITIES — atomic plans

**Source plan:** [`TEAM-SHARED-ENTITIES`](../plans/TEAM-SHARED-ENTITIES.md)  
**Code:** `TSE`  
**Source status:** in_progress

5 atoms: TSE-1/2/3 done (owner identity+attribution, task multi-user tolerance, memory contributor provenance); TSE-4 (trigger-store provider seam, §2.2+§3) and TSE-5 (PoC trigger-provider app, §4) remain — TSE-4 gated on AUTOMATION-SUBSTRATE (now satisfied) + TSE-1, TSE-5 on TSE-4.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `TSE-1` | ✅ | Owner identity: username at first boot + attribution fields + invisible-single-user migration test | — | dashboard.username wired through the 4-point config contract, asked at onboarding beside display name, editable in Settings→Account, slug-normalized at write+load; Task.author/assignee + TaskComment.author stamped from current_username(); byte-level store round-trip + full regression suite show zero behavior change (Success Criteria 1-2). Shipped [S1] 2026-07-28, test_identity.py 31 tests. |
| `TSE-2` | ✅ | Task multi-user tolerance: assignee display, mine-vs-everyone filter, MINE-only counters/pickers | `TSE-1` | fixture multi-tenant TaskProvider renders foreign assignees, mine-vs-everyone filter works, and Home widgets / ready-task counts / agent work-selection count only owner tasks (via registry.ready_tasks mine_only default); Task.belongs_to(username) test-locked (Success Criteria 3). Shipped [S2] 2026-07-28, test_task_ownership.py 16 cases. |
| `TSE-3` | ✅ | Memory contributor provenance: labeled+fenced recall, owner-weighted ranking, attributed writes; knowledge label passthrough | `TSE-1` | migration v9 adds contributor to both memory tables stamped at the single INSERT; recall labels foreign memories + fences as metadata; owner memories win ties in sort-key only (never admission); writes carry owner username, spoof-proof; §2.4 satisfied by inspection (Success Criteria 6). Shipped [S3] 2026-07-30, test_memory_contributor.py 38 cases. |
| `TSE-4` | ✅ | TriggerStore provider seam: new `trigger` provider type + handler, owner-filter at arm time, foreign-trigger read-only rendering | `TSE-1`, `EXT:AUTOMATION-SUBSTRATE:triggers.json + one TriggerService (steps 1-3) — confirmed landed 2026-08-04 per Status line` | TriggerStore interface extracted from TriggerService (list/get/upsert/delete + change-notify, native impl wrapping triggers.json); `trigger` added to PROVIDER_TYPES + TriggerTypeHandler in same commit (test_manifest_types_match_handlers passes); Trigger gains `author` field; TriggerService arms/fires ONLY author==owner rows (structural, a foreign row cannot tick); foreign triggers render read-only in Automations; sdk/triggers.py re-exports contracts (Success Criteria 4). |
| `TSE-5` | ✅ | Proof-of-concept trigger-provider app: owner triggers autonomously fire workflow/automation/prompt/action; second-username inert fixtures | `TSE-4` | an app installs manifest-only with zero core edits, registers as a `trigger` provider, and each owner-authored trigger autonomously fires each of a workflow, automation, prompt, and action under local policy; second-username ("alice") fixture rows prove visible-but-inert rendering and the structural cannot-arm filter (Success Criteria 5). |

## Atom scopes

### `TSE-1` — Owner identity: username at first boot + attribution fields + invisible-single-user migration test

**Status:** done

§1 Owner Identity: a Username at First Boot (Session 1); Implementation Effort row 1

**Done when:** dashboard.username wired through the 4-point config contract, asked at onboarding beside display name, editable in Settings→Account, slug-normalized at write+load; Task.author/assignee + TaskComment.author stamped from current_username(); byte-level store round-trip + full regression suite show zero behavior change (Success Criteria 1-2). Shipped [S1] 2026-07-28, test_identity.py 31 tests.

### `TSE-2` — Task multi-user tolerance: assignee display, mine-vs-everyone filter, MINE-only counters/pickers

**Status:** done

§2.1 Tasks (Session 2); Implementation Effort row 2

**Done when:** fixture multi-tenant TaskProvider renders foreign assignees, mine-vs-everyone filter works, and Home widgets / ready-task counts / agent work-selection count only owner tasks (via registry.ready_tasks mine_only default); Task.belongs_to(username) test-locked (Success Criteria 3). Shipped [S2] 2026-07-28, test_task_ownership.py 16 cases.

### `TSE-3` — Memory contributor provenance: labeled+fenced recall, owner-weighted ranking, attributed writes; knowledge label passthrough

**Status:** done

§2.3 Memory (Session 3) + §2.4 Knowledge (near-free rider)

**Done when:** migration v9 adds contributor to both memory tables stamped at the single INSERT; recall labels foreign memories + fences as metadata; owner memories win ties in sort-key only (never admission); writes carry owner username, spoof-proof; §2.4 satisfied by inspection (Success Criteria 6). Shipped [S3] 2026-07-30, test_memory_contributor.py 38 cases.

### `TSE-4` — TriggerStore provider seam: new `trigger` provider type + handler, owner-filter at arm time, foreign-trigger read-only rendering

**Status:** done

§2.2 Triggers (Session 4) + §3 The Trigger-Store Provider Seam (core — gated); Implementation Effort row 4

**Done when:** TriggerStore interface extracted from TriggerService (list/get/upsert/delete + change-notify, native impl wrapping triggers.json); `trigger` added to PROVIDER_TYPES + TriggerTypeHandler in same commit (test_manifest_types_match_handlers passes); Trigger gains `author` field; TriggerService arms/fires ONLY author==owner rows (structural, a foreign row cannot tick); foreign triggers render read-only in Automations; sdk/triggers.py re-exports contracts (Success Criteria 4).

### `TSE-5` — Proof-of-concept trigger-provider app: owner triggers autonomously fire workflow/automation/prompt/action; second-username inert fixtures

**Status:** done

§4 Proof-of-Concept Trigger-Provider App (Session 5, app); Implementation Effort row 5

**Done when:** an app installs manifest-only with zero core edits, registers as a `trigger` provider, and each owner-authored trigger autonomously fires each of a workflow, automation, prompt, and action under local policy; second-username ("alice") fixture rows prove visible-but-inert rendering and the structural cannot-arm filter (Success Criteria 5).

