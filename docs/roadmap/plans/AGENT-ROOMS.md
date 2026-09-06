# AGENT-ROOMS

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/AR.md`](../atomic/AR.md) as 8 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Agent Rooms — Persistent Multi-Agent Deliberation, Human-Governed

**Status:** PROPOSED — created 2026-07-26 (roadmap rev 13; owner decision: DEFERRED until WORKFLOWS-V2 core slices + ACP-AGENT-PARITY land — a room over parity-broken ACP members is a support nightmare, and pipeline-shaped multi-agent work belongs to Workflows v2)
**Created:** 2026-07-26
**Pillar:** A (Execution Engine + Convergence) · **Wave:** 4
**Depends on:** WORKFLOWS-V2 core slices (the engine owns pipeline-shaped multi-agent orchestration — rooms must not become a second engine), ACP-AGENT-PARITY (every bindable agent must behave identically across the provider seam before it can be a room member), AUTONOMY-GUARDRAILS (per-member budgets/kill-switch ride its machinery), INBOX-NOTIFICATIONS-UNIFICATION (room pause/approval prompts are attention items). Coordinates with AGENT-ROUTING (56 — a room is where a routing *conversation* would live if it ever needed more than one specialist), SESSION-MANAGEMENT (50 — rooms sit beside sessions in the sidebar taxonomy).
**Scope:** ~6 sessions (estimate held loosely — this is a **direction-holder, deliberately not deepened**; no contracts, no task tables until un-deferral). Persistent multi-agent rooms: a shared transcript where the human and N bound agents converse over time, with deterministic turn arbitration, per-member context cursors, per-member tool/safety profiles, and the human as sole approver. **Soul guardrail:** the room is deliberation chrome over the existing provider seam — each member is an ordinary agent binding holding its OWN provider session; the room never invents a cross-provider protocol, and everything that decides *who speaks* is deterministic Python, never a model call.

---

## Why this is a plan (and why it is deferred)

The gap is real: PersonalClaw has subagents (in-turn, ephemeral — `subagent.py::SubagentManager`), loop rosters (in-run, pipeline-shaped — `loop/classify.py` roster + strategy), and side conversations — but no *persistent* place where two or more differently-specialized agents and the human deliberate over days ("my researcher and my writer argue about the draft; I referee"). Sibling platforms have proven the shape: **persistent agent channels with an A2A `max_exchanges` budget** are shipped prior art — agents address each other in a standing channel, a hard exchange cap prevents runaway loops, and the human's message resets the budget. That precedent is the credibility for the round-budget design below, and also the warning: those channels get support-ticket-heavy exactly where member runtimes behave inconsistently.

Hence the two hard prerequisites. **(1) ACP-AGENT-PARITY:** a room member bound to an ACP CLI that silently lacks tool cards, resume, or approval fidelity (the audit's 10 gaps) turns every room bug into a provider-triage session; parity (or its honest documented boundary) must land first. **(2) WORKFLOWS-V2 core slices:** most "multi-agent" asks are actually pipelines — decompose, fan out, synthesize — and those belong to the engine (loop rosters already migrate there per LOOPS-EVOLUTION). Rooms are only for the *deliberative* remainder: open-ended, human-refereed, non-terminating conversation. Building rooms first would siphon pipeline-shaped work onto the wrong topology and then need unwinding.

## The cheap precursor (build this first, on the engine)

A **"council" workflow template** — N agents independently answer the same brief, a synthesis step merges/contrasts their answers, the human reads one artifact — delivers roughly the non-deliberative 60% of the multi-agent ask with **zero new topology**: it is an ordinary fan-out/fan-in workflow on Workflows v2 primitives. This should be listed as an explicit **LOOPS-EVOLUTION / template-library candidate** (one template, one line in its library table) and shipped long before rooms. If the council template satisfies demand, rooms may stay deferred indefinitely — that outcome is acceptable and cheap to discover.

## Scope sketch (the shape, held until un-deferral)

- **A room** = a persistent shared transcript + a member list. Each **member** = an agent binding + a role blurb + a **listen policy** (`all` — sees every message / `mention` — activated only when @-named / `silent` — observer, speaks only when the human asks). Rooms are created, joined, and archived only by the human.
- **Per-member provider sessions + context cursors.** Each member holds its **own provider session** (native or ACP — the ordinary binding path). The room feeds a member per turn with the **fenced, attributed transcript-since-its-cursor** ("--- room transcript (untrusted, attributed) --- [alice/researcher]: … [human]: …"), then advances that member's cursor. This is the provider-agnostic keystone: no shared context window, no cross-provider session state, nothing a vendor runtime must support beyond "receive a prompt" — the transcript diff IS the protocol. Fencing follows the existing `fence_untrusted` discipline since member outputs are model text quoting model text.
- **Turn arbitration is deterministic Python.** A mention-triggered queue: a message @-naming members enqueues them; one speaker at a time, FIFO; no model ever decides speaking order. **Round budget:** N agent-to-agent exchanges without a human message (default small, config-wired) → the room **pauses and asks** the human (an attention item per plan 42), and the budget **resets on any human input**. The sibling `max_exchanges` precedent, made a hard supervisor rule rather than a convention.
- **Human is sole approver.** Tool approvals from any member route to the human exactly as in a solo session; a member's approval-shaped output toward another member renders as attributed transcript text, never as a grant. **Per-member tool/safety profiles:** each member carries its own approval mode / tool allowlist / budget (the AUTONOMY-GUARDRAILS vocabulary), so a read-only critic and a tool-bearing executor can share a room without sharing privileges.
- **V1 exclusions (explicit):** no agent-initiated rooms; no cross-room memory (a member's room history never leaks into its other sessions or rooms except through the normal propose-don't-write learning path); no agents inviting agents.

## Design questions to resolve at un-deferral

1. **Transcript persistence format** — a new room store vs the existing per-session JSONL (`history.py`)? A room transcript is one shared document with per-member cursors; sessions are per-participant. Likely a new `rooms/<id>/transcript.jsonl` + cursor sidecar, but the redaction/rotation/export machinery in `history.py` should be reused, not reimplemented.
2. **Member context budget policy** — a long room outgrows any member's window; per-member compaction (summarize-since-cursor) vs room-level rolling summary, and who pays the summarization call (CONTEXT-ECONOMY owns the doctrine).
3. **Rooms × memory writes** — do member turns feed the after-turn learning path per member, once per room turn, or not at all? Interaction with the flywheel's capture-hygiene gates needs a decision before any member output can become a lesson.
4. **ACP capability gates** — which parity gaps (post ACP-AGENT-PARITY) remain acceptable for membership vs which disqualify a binding (e.g. no-resume CLIs and long-lived rooms); a member eligibility predicate over the parity plan's documented boundary.
5. **Room-level SEL** — what the audit trail records per room turn (speaker, trigger, budget state, approvals) and whether room events get their own `event_type` family or extend `agent_assignment`.
6. **UI surface** — a room as a first-class sidebar peer of sessions (SESSION-MANAGEMENT's taxonomy) vs a mode of the chat page; how attributed member messages, the pause card, and per-member status render without inventing a second chat UI.

## Owner tasks (at un-deferral, not before)
1. Re-confirm demand after the council template ships — rooms proceed only if deliberation (not fan-out) is what's actually missing.
2. Set the default round budget and the pause UX copy (the "your agents have been talking for a while" moment is the product's tone in one sentence).
3. Decide room membership eligibility for ACP bindings against the parity plan's final documented boundary.

## Execution log

### 2026-09-06 — the precursor gate was void in fact; minted as AR-9

An audit of every non-done atom found this plan walled off by a gate that could not clear. `AR-1`'s
dependency named a council fan-out/fan-in template owned by WORKFLOWS-V2-LOOPS-EVOLUTION; that plan
closed with all atoms `done` and never shipped one (zero non-doc hits for "council" on main — 28 bundled
templates, none of them council). Because the edge resolved to a *done* atom, the graph read AR-1 as
merely ext-gated while the deliverable the gate named did not exist: satisfied on paper, void in fact,
with AR-2..AR-8 queued behind it indefinitely.

The correction takes the first of the two options AR-1's own blocked_reason offered — mint the precursor
rather than waive it — because the precursor is not incidental to this plan. "The cheap precursor (build
this first, on the engine)" above specifies it, and owner task 1 consumes its outcome. It is now `AR-9`,
homed here rather than by reopening a closed plan, and AR-1 depends on it.

What is left on AR-1 is genuinely the owner's and nothing else: after AR-9 ships, re-confirm that
*deliberation* rather than fan-out is the missing capability. This log records the cheap outcome as a
first-class possibility, not a failure mode, because the plan already says so: if the template satisfies
demand, rooms stay deferred, and AR-1..AR-9 close as satisfied-by-precursor instead of leaving eight
atoms of phantom work on the board.

### 2026-09-06 — AR-9 shipped; the un-deferral question is now answerable

The council template merged as PR #2507: `src/personalclaw/workflows/bundled/council/workflow.json`, a
`parallel` of three independent `infer` members into a zero-token collector into one attributing synthesis.
"The cheap precursor (build this first, on the engine)" above asked for an ordinary fan-out/fan-in workflow
on Workflows v2 primitives with zero new topology, and that is literally what shipped — no action provider,
no new core. Two shape properties are pinned by `tests/test_council_template.py` rather than left to prose:
the fan-out stays independent (no member reads a sibling) and the fan-in stays attributive (the synthesis
returns attributed positions, disagreements and dissent, and never binds one member as *the* answer), each
detector floored against `best-of-n`, the sibling that must fail the attribution contract.

Three named seats rather than a dynamic fan-out over N is an engine constraint, not a design preference:
containers have no output surface, so a `foreach` fan-out cannot be fanned back in through bindings.

This closes the precursor half of `AR-1` and leaves owner task 1 standing alone. The question is now
genuinely answerable rather than hypothetical, because the thing that might satisfy the demand can actually
be run: does *deliberation* — open-ended, human-refereed, non-terminating conversation — remain missing once
fan-out is available? The plan pre-authorises the cheap answer, so a "no" is a real outcome that closes
`AR-1`..`AR-8` as satisfied-by-precursor rather than a failure to explain away.
