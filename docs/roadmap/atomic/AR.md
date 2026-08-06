# AGENT-ROOMS — atomic plans

**Source plan:** [`AGENT-ROOMS`](../plans/AGENT-ROOMS.md)  
**Code:** `AR`  
**Source status:** proposed

AGENT-ROOMS is a PROPOSED+DEFERRED direction-holder with no contracts, no task tables, no execution log, and zero done work. The whole plan is gated on an owner un-deferral decision (contingent on the LOOPS-EVOLUTION "council" template shipping and demand being re-confirmed) plus four cross-plan prereqs (WORKFLOWS-V2 core slices, ACP-AGENT-PARITY, AUTONOMY-GUARDRAILS, INBOX-NOTIFICATIONS-UNIFICATION). I produced 8 atoms: an un-deferral/design-resolution gate (AR-1) that must land first, and 7 implementation seams from the scope sketch (room store/transcript, member model + listen policies, per-member cursors + fenced transcript feed, deterministic arbitration + round budget, per-member safety profiles + human-sole-approver, room-level SEL, UI surface) — each depending on AR-1 and its own cross-plan prereq. Nothing is startable now.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `AR-1` | ⬜ | Un-defer the plan: ship the council precursor, re-confirm demand, and resolve the 6 design questions into contracts | `EXT:LOOPS-EVOLUTION:council fan-out/fan-in workflow template ships first; demand re-confirmation gate`, `EXT:WORKFLOWS-V2:core slices own pipeline-shaped multi-agent orchestration; rooms are only the deliberative remainder` | The LOOPS-EVOLUTION council template has shipped, the owner re-confirms deliberation (not fan-out) is the missing capability, and all 6 design questions (transcript persistence format, member context budget policy, rooms x memory writes, ACP capability gates, room-level SEL, UI surface) plus the 3 owner tasks (default round budget + pause UX copy, ACP membership eligibility) are resolved into a Contracts section and task table added to this plan. |
| `AR-2` | ⬜ | Room store and persistent shared-transcript format with per-member cursors | `AR-1` | A human can create, join, and archive a room; its shared transcript persists to rooms/<id>/transcript.jsonl with a cursor sidecar, reusing history.py redaction/rotation/export rather than reimplementing them. |
| `AR-3` | ⬜ | Member model: agent binding + role blurb + listen policy, each holding its own provider session | `AR-1`, `EXT:ACP-AGENT-PARITY:every bindable agent must behave identically across the provider seam before it can be a room member` | Members can be added to a room with a role blurb and a listen policy (all / mention / silent); each member holds its OWN native or ACP provider session via the ordinary binding path, with no shared context window or cross-provider session state. |
| `AR-4` | ⬜ | Per-member context cursors: feed fenced attributed transcript-since-cursor, then advance; per-member compaction | `AR-2`, `AR-3`, `EXT:CONTEXT-ECONOMY:owns the summarization doctrine and who pays the compaction call` | Each member turn is fed the fence_untrusted, attributed transcript-since-its-cursor ('[alice/researcher]: ...'), its cursor advances after, and per-member compaction (summarize-since-cursor) keeps a long room within each member's window per the resolved budget policy. |
| `AR-5` | ⬜ | Deterministic turn arbitration: mention-triggered FIFO queue + round budget that pauses to a human attention item | `AR-3`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:room pause/approval prompts are attention items on its kind registry` | @-mentions enqueue members into a deterministic FIFO speaker queue (one speaker at a time; no model ever decides speaking order); N agent-to-agent exchanges without a human message pause the room and raise an inbox attention item, and any human input resets the budget. |
| `AR-6` | ⬜ | Per-member tool/safety profiles and human-as-sole-approver routing | `AR-3`, `EXT:AUTONOMY-GUARDRAILS:per-member budgets/kill-switch/approval-mode machinery` | Each member carries its own approval mode / tool allowlist / budget (the Autonomy-Guardrails vocabulary) so a read-only critic and a tool-bearing executor can share a room; all tool approvals route to the human as in a solo session, and a member's approval-shaped output toward another member renders as attributed transcript text, never as a grant. |
| `AR-7` | ⬜ | Room-level SEL audit trail per turn | `AR-5`, `AR-6` | Every room turn records a SEL audit entry capturing speaker, trigger, budget state, and approvals, under the decided event_type family (own family vs extending agent_assignment). |
| `AR-8` | ⬜ | Room UI surface: attributed messages, pause card, per-member status | `AR-2`, `AR-5`, `EXT:SESSION-MANAGEMENT:rooms sit beside sessions in the sidebar taxonomy` | A room renders in the web/ UI (as a first-class sidebar peer of sessions or a mode of the chat page, per the resolved decision) showing attributed member messages, the round-budget pause card, and per-member status, without inventing a second chat UI. |

## Atom scopes

### `AR-1` — Un-defer the plan: ship the council precursor, re-confirm demand, and resolve the 6 design questions into contracts

**Status:** todo

The cheap precursor (build this first, on the engine); Design questions to resolve at un-deferral; Owner tasks (at un-deferral, not before)

**Done when:** The LOOPS-EVOLUTION council template has shipped, the owner re-confirms deliberation (not fan-out) is the missing capability, and all 6 design questions (transcript persistence format, member context budget policy, rooms x memory writes, ACP capability gates, room-level SEL, UI surface) plus the 3 owner tasks (default round budget + pause UX copy, ACP membership eligibility) are resolved into a Contracts section and task table added to this plan.

### `AR-2` — Room store and persistent shared-transcript format with per-member cursors

**Status:** todo

Scope sketch (the shape, held until un-deferral); Design questions #1 (transcript persistence format)

**Done when:** A human can create, join, and archive a room; its shared transcript persists to rooms/<id>/transcript.jsonl with a cursor sidecar, reusing history.py redaction/rotation/export rather than reimplementing them.

### `AR-3` — Member model: agent binding + role blurb + listen policy, each holding its own provider session

**Status:** todo

Scope sketch (the shape, held until un-deferral) — per-member provider sessions

**Done when:** Members can be added to a room with a role blurb and a listen policy (all / mention / silent); each member holds its OWN native or ACP provider session via the ordinary binding path, with no shared context window or cross-provider session state.

### `AR-4` — Per-member context cursors: feed fenced attributed transcript-since-cursor, then advance; per-member compaction

**Status:** todo

Scope sketch — per-member provider sessions + context cursors; Design questions #2 (member context budget policy)

**Done when:** Each member turn is fed the fence_untrusted, attributed transcript-since-its-cursor ('[alice/researcher]: ...'), its cursor advances after, and per-member compaction (summarize-since-cursor) keeps a long room within each member's window per the resolved budget policy.

### `AR-5` — Deterministic turn arbitration: mention-triggered FIFO queue + round budget that pauses to a human attention item

**Status:** todo

Scope sketch — Turn arbitration is deterministic Python; Round budget

**Done when:** @-mentions enqueue members into a deterministic FIFO speaker queue (one speaker at a time; no model ever decides speaking order); N agent-to-agent exchanges without a human message pause the room and raise an inbox attention item, and any human input resets the budget.

### `AR-6` — Per-member tool/safety profiles and human-as-sole-approver routing

**Status:** todo

Scope sketch — Human is sole approver; Per-member tool/safety profiles

**Done when:** Each member carries its own approval mode / tool allowlist / budget (the Autonomy-Guardrails vocabulary) so a read-only critic and a tool-bearing executor can share a room; all tool approvals route to the human as in a solo session, and a member's approval-shaped output toward another member renders as attributed transcript text, never as a grant.

### `AR-7` — Room-level SEL audit trail per turn

**Status:** todo

Design questions #5 (Room-level SEL)

**Done when:** Every room turn records a SEL audit entry capturing speaker, trigger, budget state, and approvals, under the decided event_type family (own family vs extending agent_assignment).

### `AR-8` — Room UI surface: attributed messages, pause card, per-member status

**Status:** todo

Design questions #6 (UI surface)

**Done when:** A room renders in the web/ UI (as a first-class sidebar peer of sessions or a mode of the chat page, per the resolved decision) showing attributed member messages, the round-budget pause card, and per-member status, without inventing a second chat UI.

