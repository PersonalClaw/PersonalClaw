# ACP-AGENT-PARITY

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/AAP.md`](../atomic/AAP.md) as 10 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: ACP Agent Parity — One Provider, the Whole Platform

**Status:** PROPOSED — created 2026-07-14 from the ACP agent-parity audit (`docs/roadmap/research/acp-agent-parity-audit.md`)
**Created:** 2026-07-14
**Wave:** 0-eligible — no hard dependencies; standalone architectural cleanup of an existing seam. Phase 1 can start today (all three binaries are on the dev machine).
**Depends on:** nothing hard. Touches the same approval-gate seams AUTONOMY-GUARDRAILS hardens (coordinate, don't block: this plan extends host gates *across the provider seam*; guardrails deepens them for everyone). EXECUTION-ISOLATION's sandbox wrap already applies to ACP processes (`transport.py:316`) — no interaction.
**Scope:** a user who binds ONE ACP provider (claude-code, codex, or kiro-cli) should be able to use the ENTIRE platform end to end — chat, tools, approvals, loops, cron, learning, resume — without discovering that half the harness silently only works for the native runtime. The audit's verdict: the seam is cleaner than feared (`chat_runner._run_chat` is provider-neutral; context injection, approvals-when-requested, variants/fork/queue, preference learning all already cross it), but 10 concentrated gaps remain. This plan validates the code-audit predictions at runtime (Phase 1), then closes the closable gaps and *honestly documents* the protocol-limited ones (Phase 2). **No dual paths:** where the ACP protocol genuinely can't carry something, we document the boundary as a known constraint — we do not build a shadow mechanism that pretends it does.

---

## Overview

The native execution loop (`agents/native/runtime.py`) has deep harness integration: the in-process tool registry, pre-execution deny-list/task-mode/PreToolUse gating, failure breakers, unattended stripping, dry-run, procedural-memory capture, typed tool-result meta, mid-turn steering. ACP-provided agents (claude-code, codex, kiro-cli) were suspected to be second-class citizens. The 2026-07-14 code audit (95 evidence citations) found the architecture better than feared — most integration lives *above* the seam in provider-neutral `chat_runner` code — but confirmed 10 real gaps, clustered in four places:

1. **Tool surface:** the native registry (knowledge/tasks/loops/inbox/artifacts/workflows/subagents/web) reaches an ACP CLI only via the `personalclaw-core` MCP server, and only if the CLI's *own* config spawns it. The host sends `"mcpServers": []` at `session/new` on every live path (`acp/client.py:419, 481`). claude/codex get no seeded config at all; kiro's discovery of `~/.personalclaw/agents/personalclaw.json` is unverified.
2. **Per-tool machinery** inside `NativeAgentRuntime` (breaker, loop detection, dry-run, unattended, steering) never runs for ACP turns.
3. **Learning capture:** the procedural-outcome drain is native-only.
4. **Per-dialect protocol asymmetries** (plan mode, effort, personas, concurrent sessions, resume).

The cleanup principle: **the host seam owns everything it can carry; the agent app bundle owns per-CLI config; upstream protocol limits get documented, not worked around.**

Verified starting points (paths under `src/personalclaw/` unless noted):

- `acp/client.py:419, 481` — both `session/new` call sites hardcode `"mcpServers": []`. The plumbing for the fix half-exists: the P9 pool path already accepts an `mcp_servers` param (`llm/acp_session_provider.py:240-247`) that no live caller passes.
- `personalclaw mcp-core` is an existing single stdio endpoint aggregating artifacts/workflows/memory/subagents + core tools (`mcp_core.py:918-952`), already referenced by the kiro-targeted agent config generator (`agent.py:253`).
- `acp/translate.py:104-141` already parses structured `rawInput` from tool_call frames (including strReplace old/new/path) — the raw material for closing most of the tool-card fidelity gap host-side, without touching the CLIs.
- `chat_runner.py:1771-1795` — the host approval gate (deny-list, task-mode, blocking PreToolUse) applies to ACP tools **only when the CLI emits `session/request_permission`**; CLI-auto-approved tools bypass it entirely, with hooks explicitly informational after the fact (`chat_runner.py:1623-1632`).
- `provider_bridge.py:528-537` — the bridge *pops* `unattended`/`dry_run`/`extra_tool_roots`/`project_id` before non-native builders ever see them.
- `acp_bundles/_register.py:38` `register_acp_cli_entry` — no bundle passes `session_files_dir`, so the `session/load` resume path is dead by construction (`client.py:388-414`).

**Soul guardrail:** this is parity for ONE user's machine — config seeding, gate-coverage plumbing, and honest documentation. No protocol forks, no patched CLIs, no reimplementation of CLI-internal behavior on the host.

---

## Phase 1 — VALIDATION (as-a-user sweeps, one provider at a time)

Per the user's explicit directive, validation comes FIRST: the audit is a code-level read; every UNKNOWN cell and every WIRED/PARTIAL/ABSENT verdict gets confirmed at runtime before a line of fix code is written. Fixes sequenced on *observed* severity, not predicted.

**Method:** the campaign validation doctrine — real UI via Chrome DevTools MCP, every cycle **mutates state → inspects persistence → verifies consumers** (no shallow render-checks). One provider per session, in order **claude-code → codex → kiro-cli**, each driving the whole platform end to end. Each provider session executes the audit's §6 twelve-step checklist verbatim, with a native control run of the same step where the checklist calls for one:

1. MCP tool reachability (gap 1) — "list your tools; do you have knowledge_search / task_create / notify?", then exercise one for real. For kiro: verify whether `~/.personalclaw/agents/personalclaw.json` is honored.
2. Approval-gate coverage (gap 2) — file read / file write / destructive bash; note which surface a host card vs run silently; repeat the write under task-mode=Ask.
3. Plan mode (gap 9) — claude/codex should plan natively (forwarded `acp_mode=plan`); kiro blocked only by the host gate.
4. Unattended loop (gap 3) — small Code loop bound to the provider, unattended; watch for wedging (esp. kiro) and whether writes execute (claude/codex `bypassPermissions`).
5. Resume (gap 6) — restart the gateway mid-conversation; expect fall-to-compressed-history, verify continuity quality.
6. Tool-card fidelity (gap 7) — multi-tool turn; input args, output, done-state, diff chips, recovery hints.
7. Context/turn telemetry — context-% chip, turn-complete line, `/compact` behavior.
8. Reasoning effort + model override — composer effort pill (kiro expected absent/no-op) + discovered-model pick.
9. Learning — correction → "Learned:" chip (should fire, neutral); confirm no procedural outcomes recorded (expected gap 4).
10. Steering + queued messages — mid-turn send should queue (not steer) on ACP; queue drains after the turn.
11. Subagents (if MCP reachable) — spawn one; verify completion injects back into the right session.
12. Concurrent sessions (kiro only) — `acp_concurrent_sessions` on, two kiro chats, one PID serving both.

**Preconditions (from the audit's binary snapshot, 2026-07-14):** all three binaries present. `claude` + `codex` + `codex-acp` installed with auth artifacts; `claude-agent-acp` adapter will provision via npx/durable-install on first enable (Node 24 present). **kiro-cli is Amazon-internal auth — check `mwinit` freshness FIRST in the kiro session**; a stale midway token would masquerade as protocol failures.

**Deliverable per provider (checked in beside the audit):**
- A **verified matrix column** — every audit cell for that provider re-marked with the runtime result: CONFIRMED (matches the code-audit prediction) or DIVERGED (with what actually happened). UNKNOWN cells (registry reachability, AskUserQuestion, codex/kiro compaction + slash commands, context-% emission, subagent inject-back) become definite.
- A **gap inventory with severity** (P0 safety / P1 capability-dead / P2 fidelity / P3 cosmetic), feeding the Phase 2 sequencing. Anything the audit missed gets added to the inventory, not silently fixed.

Incidental small bugs found en route get fixed in-session per campaign doctrine; anything structural waits for Phase 2 so fixes land against the full three-provider picture.

**Effort: ~3 sessions (1 per provider).**

---

## Phase 2 — PARITY FIXES (sequenced by gap severity)

Each fix names its **owner**: *core seam* (host-side `src/personalclaw/`), *agent app bundle* (`apps/claude-code-agent` / `codex-agent` / `kiro-cli-agent` + `acp_bundles/_register.py`), or *upstream CLI limitation* (documented as a known constraint — see §2.7). Ordering follows the audit's gap ranking, adjusted by Phase 1 findings if they diverge.

### 2.1 MCP reachability — gap 1 (the biggest single unlock)

Without this, an ACP session has none of knowledge/tasks/inbox/artifacts/workflows/subagents/notify — the single largest capability cliff.

- **Owner: core seam + agent app bundles, two-pronged.**
- **Prong A (protocol-first, core seam):** pass the `personalclaw-core` server spec (`personalclaw mcp-core` stdio command, resolved via the same `_resolve_personalclaw_bin` used by `agent.py:253`) in `mcpServers` at `session/new` — the ACP protocol field exists and the pool path already has the parameter (`acp_session_provider.py:240-247`); wire it through both `client.py` call sites (419, 481 — including `start_fresh_turn_session`) and the live `session.py` caller. Phase 1 tells us which CLIs actually honor protocol-passed `mcpServers`; where honored, this is the clean fix — zero user-config mutation, per-session `PERSONALCLAW_SESSION_KEY` env already flows via `transport.py:323-326`.
- **Prong B (config seeding, agent app bundle):** for any CLI that ignores protocol `mcpServers`, the bundle's `create_provider`/enable path seeds the CLI's own config: claude-code → `mcpServers` block in the (already opt-in isolatable) `CLAUDE_CONFIG_DIR` settings; codex → `~/.codex/config.toml` `mcp_servers` entry; kiro → ensure `personalclaw.json` is discoverable from `~/.kiro/agents/` (symlink or copy of `~/.personalclaw/agents/personalclaw.json`, which already lists `@personalclaw-core`). Seeding is marker-scoped and idempotent (the prompt-seed contract: never clobber user config outside our block; remove on disable exactly what we wrote). The claude-code isolated-config hardening (`PERSONALCLAW_CC_ISOLATE`) becomes the *preferred* documented setup because seeding an isolated dir touches nothing of the user's.
- **Acceptance:** per provider, "list your tools" shows personalclaw-core tools; `knowledge_search`, `task_create`, `notify`, and `subagent_run` (with correct session inject-back via the `session_pid_<pid>.txt` + env resolution) all work as-a-user. The dashboard MCP manager's external servers (`~/.personalclaw/mcp.json` → rebuilt into `personalclaw.json`) reach kiro; claude/codex external-MCP parity rides the same prong that wins for core.

### 2.2 Approval-gate coverage — gap 2 (the safety hole)

CLI-auto-approved tools currently bypass the deny-list, task-mode gate, and blocking PreToolUse hooks; hooks fire informationally after execution (`chat_runner.py:1623-1632`).

- **Owner: core seam (mode forwarding + gate) with per-dialect knobs in the bundles; residue is an upstream limitation, documented.**
- **Mechanism:** make the host the permission authority wherever the protocol allows. For Zed dialects (claude-code, codex): stop leaving the CLI in its own default-allow mode — forward the *most-restrictive* native mode (`default`, never `acceptEdits`/`dontAsk`/`bypassPermissions` except the explicit unattended path in §2.3) and, where the adapter supports it, configure "always ask" so every tool emits `session/request_permission` and therefore hits the existing host gate (`chat_runner.py:1771-1795`) — deny-list, task-mode, blocking PreToolUse, trust/YOLO all then apply uniformly. The claude-code isolation path already strips `permissions.allow/ask` + `defaultMode` from the CLI config (`apps/claude-code-agent/provider.py:106-164`) — extend that from opt-in hardening to the bundled default for host-managed sessions.
- **Honest boundary:** some CLI-internal reads/operations may never surface a permission request regardless of mode (Phase 1 step 2 measures exactly which, per provider). Those are **documented as a known constraint** in §2.7's parity doc — the host cannot pre-gate what the protocol never shows it. We do NOT build an ACP-side syscall-shim wrapper to intercept them; the OS sandbox wrap (`transport.py:316`) remains the outer boundary for those, and the SEL audit of EVENT_TOOL_CALL remains the detection layer.
- **Acceptance:** with task-mode=Ask, a file write via any ACP provider produces a host approval card (or is blocked) — never a silent write; the deny-list rejects a denied command at the permission prompt with the standard denial message; PreToolUse blocking hooks fire pre-execution on every permission-surfaced tool. The residual not-gateable set per provider is enumerated in the parity doc, not discovered by users. **Landed form (`AAP-5`):** the enumeration lives in `acp/permission_authority.NOT_GATEABLE` — a per-provider registry where each entry carries the reason and the observation that proved it, and every provider is listed even when its residual set measured EMPTY, so "no entry" can never read as "gated". §2.7's parity doc RENDERS that registry rather than re-deriving it, which keeps the prose from drifting out of sync with the gate.

### 2.3 Unattended + loop support — gaps 3 and 5 (loops work on ACP)

- **Owner: core seam, with a documented kiro limitation.**
- **Unattended (gap 3):** stop popping `unattended` at the bridge for ACP (`provider_bridge.py:534`); thread it to the ACP session setup where it maps to what the dialect *can* do: Zed dialects → `bypassPermissions` (already the loop manager's move, `loop/manager.py:181` — unify so cron/scheduled runs get it too, not just loops) **plus** host-side fail-fast: any `session/request_permission` arriving on an unattended ACP session is auto-denied-with-reason and the turn continues or aborts per loop policy (the native T5 semantic: never wedge waiting for a human). kiro (no mode axis) gets the fail-fast half only — an unattended kiro loop can still run, and every interactive prompt resolves deterministically instead of wedging. That asymmetry is documented.
- **Loop guards (gap 5):** the breaker doesn't need to move into the CLI — the host already sees every EVENT_TOOL_CALL/EVENT_TOOL_RESULT in the neutral stream. Extract the counting/threshold logic from `_FailureBreaker` + `record_structural` (`runtime.py:70-215`) into a runtime-agnostic observer consumed by `chat_runner` for ACP sessions: consecutive-failure warn/block thresholds and no-progress/ping-pong detection produce the same steering injections (as queued user-visible notices + turn-abort at the circuit threshold) that the native loop gets. **Boundary:** the native breaker can *block the next tool call pre-execution*; the ACP observer can only abort/steer *between* protocol events — stated in the doc, not papered over.
- **Acceptance:** an unattended Code loop bound to each of the three providers runs to completion or fails fast — never wedges; a deliberately failing-tool ACP session trips the circuit and aborts the turn with the standard breaker message.

### 2.4 Resume — gap 6

- **Owner: agent app bundles (registration) + core seam (dir provisioning).**
- Each bundle's `register_acp_cli_entry` call passes a `session_files_dir` (e.g. `~/.personalclaw/acp_sessions/<provider>/`); the core registration helper creates it. The `client.py:388-414` load path then finds its session file and `session/load` becomes live for capability-negotiating CLIs. Phase 1 step 5 gives the per-provider baseline; post-fix, re-run it.
- **Honest boundary:** `loadSession` is capability-gated per CLI — where a CLI doesn't advertise it, the compressed-history bootstrap (already WIRED) remains the documented behavior, and the activity line says so ("Session restored from history") rather than implying a protocol resume.
- **Acceptance:** gateway restart mid-conversation on a resume-capable provider produces "Session resumed" and full-fidelity continuation; non-capable providers degrade to compressed history with accurate UI labeling.

### 2.5 Learning + fidelity — gaps 4, 7, 8

- **Procedural-memory drain (gap 4) — owner: core seam.** Same pattern as §2.3: the neutral event stream already carries tool name/args/result/success for every ACP tool call. Build the M5d outcome-accumulation off `chat_runner`'s EVENT_TOOL_CALL/EVENT_TOOL_RESULT handling for ACP sessions (the native runtime keeps its in-loop accumulator), draining into the same store `drain_tool_outcomes` feeds. Incognito/restricted guards apply identically (they already live in `chat_runner`).
- **Typed tool-result meta (gap 7) — owner: core seam, best-effort by construction.** `translate.py` already extracts structured `rawInput` (`translate.py:104-141`) — populate the AgentEvent's structured-input field from it so `_redact_tool_input_obj` renders schema-driven fields instead of returning None (`chat_runner.py:286-318`); map ACP tool_kind + strReplace-style frames to the file-change diff-chip path by *kind*, not by the native-only `_WRITE_FILE_TOOLS` name set (`chat_runner.py:352-418`). `content_type`/`raw_ref`/`recovery_hints` on results remain native-richer — the protocol doesn't carry them; the meta stays empty where the frames are empty (the existing `chat_runner.py:1704` behavior), documented. No fabricated meta.
- **risk_level plumbing (gap 8) — owner: core seam.** `resolve_effective_risk` today falls back to name/kind/bash heuristics for ACP (no declared risk_level, `runtime.py:597` is native-only). Add a declared-risk map for the *known* surfaces: personalclaw-core MCP tools carry their native `ToolDefinition` risk levels through the MCP server's tool listing (they're the same tools — the declaration exists, it just doesn't survive the round-trip), and ACP `tool_kind` (read/edit/execute/…) feeds a kind→floor mapping. CLI-proprietary tools stay heuristic — documented.
- **Acceptance:** after an ACP turn using a personalclaw-core tool, a procedural outcome row exists (and none under incognito); an ACP edit-tool turn shows a diff chip and structured input fields; the approval card for a personalclaw-core destructive tool shows its declared risk chip, not the heuristic one.

### 2.6 Dialect asymmetry closure (gap 9) + project stamping (gap 10)

- **Plan-mode for kiro — owner: upstream CLI limitation, documented + host-compensated.** The default dialect has no mode axis (`set_mode_request` → None). No shadow mechanism: the host task-mode gate already blocks non-plan mutations at the permission prompt (§2.2 makes that coverage real), and the parity doc states kiro "plans" by host enforcement, not native CLI behavior.
- **Reasoning effort for kiro — upstream limitation, documented.** The composer effort pill greys out (not silently no-ops) when the bound dialect returns None for `set_effort_request` — the UI tells the truth (small core-seam change: surface dialect capabilities in the discovered-agent payload).
- **Personas for claude/codex — upstream limitation, documented.** One base agent per Zed adapter; the picker simply doesn't offer a persona axis for them (already the behavior — confirm no dead UI).
- **P9 concurrent sessions for claude/codex — upstream unproven, documented.** `supports_concurrent_sessions=False` stays until a spike proves the Zed adapters can interleave (out of scope here; noted as a future spike).
- **Slash commands — core seam, capability-gated.** `stream_command` routes to protocol `commands/execute` where negotiated (claude today, `client.py:537-548`); where not, the existing plain-prompt fallback stands and the UI labels the command as "sent as text" — Phase 1 step 7 determines codex/kiro reality.
- **project_id stamping (gap 10) — owner: core seam.** Stop popping `project_id` for ACP (`provider_bridge.py:541`); thread it into the personalclaw-core MCP server's per-session context (the session key already crosses via `PERSONALCLAW_SESSION_KEY` — resolve project binding server-side in `mcp_core` from the session, exactly how the native runtime binds it per turn per `session.py:1088`). Then `artifact_save` from an ACP session stamps the right project with zero protocol change. `extra_tool_roots` (brownfield loops): same session-side resolution for personalclaw-core file tools; the CLI's *own* file tools remain confined only by CLI settings + sandbox — documented. Mid-turn queue-steering (`#37`) stays native-only: the ACP protocol has no mid-turn injection seam (`chat_runner.py:1458-1462`) — the queue-then-drain behavior is the documented ACP semantic.

### 2.7 The parity doc (the honest-boundary deliverable)

`docs/agents/acp-parity.md` — the per-provider capability statement generated from the Phase 1 verified matrices + the Phase 2 end-state: what is at parity, what is host-compensated, and what is a protocol/CLI constraint (with the upstream issue to watch, where one exists). Linked from each agent app's README and the discovered-agents UI ("capability notes"). This is where "no dual paths" lands: every ABSENT that stays ABSENT is written down with its reason.

**Phase 2 effort: ~6 sessions** — (a) MCP reachability 1.5 (two prongs × three CLIs); (b) approval-gate coverage 1; (c) unattended + loop guards 1; (d) resume 0.5; (e) learning + fidelity 1; (f) dialect closure + project stamping + parity doc 1. Each fix closes with a re-run of the relevant §6 checklist steps on all three providers (mutate → persistence → consumers).

---

## Provider-Fidelity Wiring (where each piece plugs in)

- **No new provider TYPE, no new dialect.** All fixes ride the existing `acp_agent` ProviderEntry shape, the three existing dialects, and the neutral AgentEvent stream. The only registration change is bundles passing `session_files_dir` (§2.4).
- **Bridge kwargs:** `unattended` (§2.3) and `project_id` (§2.6) stop being popped for ACP at `provider_bridge.py:528-541`; `dry_run` and `extra_tool_roots` (for CLI-native tools) remain native-only — documented, not shadowed.
- **Config seeding is marker-scoped + reversible** (§2.1 prong B): the prompt-seed contract — never clobber user CLI config outside our block, remove on disable exactly what we wrote, SEL-audit every seed/unseed.
- **SEL:** auto-denied unattended permission requests, breaker trips on ACP sessions, and config seeds/unseeds all log to `sel.py`.
- **Tests:** the extracted breaker/observer gets unit tests off synthetic event streams; gate-coverage gets a regression test asserting task-mode=Ask blocks a permission-surfaced write for a fake ACP provider; `register_acp_cli_entry` gains a test that a registered bundle produces a live `session_files_dir`.

---

## Implementation Effort

**~9 sessions total: Phase 1 ≈ 3 (one per provider, claude-code → codex → kiro-cli), Phase 2 ≈ 6.**

Phase 2 order is severity order and each step is independently shippable; if Phase 1 reorders severity (e.g. protocol-passed `mcpServers` turns out to Just Work on all three, collapsing §2.1 to half a session), resequence accordingly.

---

## Risks

| Risk | Mitigation |
|---|---|
| CLIs ignore protocol-passed `mcpServers` → prong A dead | Prong B (config seeding) is designed in from the start, not a fallback scramble; Phase 1 step 1 answers this before any fix code |
| Forcing always-ask mode makes ACP chats approval-spammy | trust_reads / trust-session / per-agent floors already auto-resolve safe requests at the host gate (`chat_runner.py:1927+, 1138-1170`) — the gate seeing MORE requests is the point; UX cost is bounded by the existing auto-approve ladder, and §2.5's declared-risk plumbing makes trust_reads accurate for core tools |
| Config seeding corrupts a user's CLI setup | Marker-scoped blocks, idempotent re-seed, removal keyed to our own writes, SEL audit; claude-code prefers the isolated `CLAUDE_CONFIG_DIR` where nothing of the user's is touched |
| Host-side breaker double-fires against a CLI's own retry logic | Thresholds start at the native values but the observer only warns/aborts — it never mutates the CLI's loop; Phase 1's failing-tool cycle calibrates before enabling by default |
| kiro mwinit staleness pollutes Phase 1 findings | Explicit precondition: check auth first in the kiro session; auth failures recorded as ENV, not as capability verdicts |
| Parity doc rots as CLIs/adapters update | It's generated from the checked-in verified matrices; re-running the §6 checklist is the documented refresh procedure, and each entry names the CLI/adapter version it was verified against |
| Scope creep into patching upstream CLIs | Hard rule in §2.7: protocol/CLI limits get documented, never shimmed; anything requiring upstream change is filed as a watch item, not built |

---

## Success Criteria

1. **Phase 1:** three checked-in verified matrix columns (every audit cell CONFIRMED or DIVERGED at runtime) + a severity-ranked gap inventory; zero UNKNOWN cells remain for any provider.
2. A chat bound to each of the three providers can list and successfully invoke `knowledge_search`, `task_create`, `notify`, and `subagent_run` (with correct completion inject-back) — the personalclaw-core surface is reachable on all three.
3. With task-mode=Ask, a file write attempted via any ACP provider is gated by a host approval card or blocked — never silently executed; the deny-list and blocking PreToolUse hooks apply to every permission-surfaced ACP tool; the residual not-gateable set is enumerated per provider in the parity doc.
4. An unattended Code loop bound to each provider runs without wedging: Zed dialects execute via `bypassPermissions`, kiro fail-fasts interactive prompts deterministically; a failing-tool ACP session trips the host-side breaker and aborts with the standard message.
5. Gateway restart mid-conversation on a resume-capable provider shows "Session resumed" with full continuity; non-capable providers show accurate compressed-history labeling.
6. After an ACP turn: procedural outcomes are recorded (and suppressed under incognito), edit-tools render diff chips + structured input fields, and personalclaw-core tools show declared (not heuristic) risk on approval cards.
7. ACP `artifact_save` stamps the session's bound project; the composer effort pill is greyed (not no-op) on kiro; every remaining ABSENT is documented in `docs/agents/acp-parity.md` with its reason — a user binding one ACP provider can discover the platform's true shape from that one page instead of by tripping over it.

---

## Phase 1 results — claude-code verified matrix (atom `AAP-1`)

**Swept:** 2026-08-17 · **Adapter:** `@agentclientprotocol/claude-agent-acp` 0.60.0 ·
**CLI:** `claude` 2.1.233.669 (ASBX Claude Code, channel stable) · **Node** v24.18.0 ·
**Host:** this repo at `b01cb76e`, isolated `PERSONALCLAW_HOME`, gateway on `:10421`,
⚠️ **This header is single-SHA but the column is MIXED-SHA:** `b01cb76e` is correct for `O1`-`O26`
only. `O27`-`O34` come from the 2026-08-19 re-drive, and `O27` measures `provider_bridge.py`
re-injecting `unattended` — which is `8091f285` (AAP-6), merged *after* `b01cb76e`. The re-drive
never updated this line. Cite the per-observation ledger, not this SHA, when judging staleness.
`PERSONALCLAW_AUTH_MODE=none`, `PERSONALCLAW_CC_ISOLATE` **unset** (the bundle default).

**Method.** The `claude-code-agent` bundle was installed from the first-party apps dir into
the isolated home (`POST /api/apps`), which registered the `acp:claude-code` ProviderEntry; a
dashboard chat session was bound to it (`POST /api/chat/sessions/{s}/acp-agent`) and driven
through the audit §6 checklist over the same HTTP/SSE + WebSocket surfaces the dashboard
itself uses — `POST /api/chat` for the turn (SSE), `/api/ws` for the activity and telemetry
lines the SSE stream does not carry, and the persisted `sessions/*.jsonl`,
`security_events.jsonl`, `memory.db`, `learning.db` and `session_map.json` under the isolated
home for the state half of every mutate → persist → consume cycle. Every mark below names the
command or artifact that produced it so a reader can re-run it.

**Marks.** `CONFIRMED` — the runtime matches the audit's predicted verdict for that cell.
`DIVERGED` — it does not; the observed verdict replaces the predicted one. For the audit's
`UNKNOWN` cells the prediction is the §5 gap text and the mark records the now-definite
verdict. `NOT-EXERCISED` — no runtime observation was obtained, with the reason stated;
reading the code and reasoning that a cell *should* work is explicitly **not** a mark.

### Observation ledger

Every mark in the matrix below cites one of these. `S1` = `chat-1-…` (bound after creation),
`S2` = `chat-2-…` (workspace-dir set *before* binding).

| id | what was run | what was observed |
|---|---|---|
| `O1` | `POST /api/apps` with the first-party `claude-code-agent` dir, then `GET /api/agent-providers` | `acp:claude-code` registered, `ready: true`, `state: ready`, `detail: "initialize OK (caps: _meta, auth, loadSession, mcpCapabilities, promptCapabilities, providers, sessionCapabilities)"` — **`loadSession` IS advertised** by adapter 0.60.0 |
| `O2` | `GET /api/agent-providers/acp:claude-code/agents` | one agent (`id: acp:claude-code`, `provider_agent: ""`) → no persona axis; `models` = 14 discovered ids (`default`, `…opus-4-6…` … `…haiku-4-5…`); `supported_efforts` = low/medium/high/xhigh/max; `permission_modes` = `default, acceptEdits, plan, dontAsk, bypassPermissions` |
| `O3` | `POST /api/chat/sessions/{S1}/acp-agent {provider: "acp:claude-code"}` then `GET …/{S1}` | `acp_provider: "acp:claude-code"` — ephemeral discovered-agent binding round-trips |
| `O4` | S1 turn: "List your available tool names verbatim … state YES or NO for each of: knowledge_search, task_create, notify" | the CLI's own tool set only (Agent, Bash, Edit, Read, Skill, ToolSearch, Write, Workflow, + deferred Cron*/WebFetch/…); `knowledge_search` **NO**, `task_create` **NO**, `notify` **NO**. No `mcp__personalclaw*` server. The reply also volunteered that "the session context references PersonalClaw-style tools (`memory_remember`, `skill_invoke`, `knowledge_create`) … not in my actual tool list" — i.e. the *index* arrived as prompt text while the *tools* did not. The spawned CLI DID list the operator's own MCP servers (builder-mcp, slack-mcp, …) as connecting |
| `O5` | S1 turn: read `probe.txt`, write `written.txt`, `rm -f doomed.txt`; polled `GET /api/chat/sessions` for `pending_approval_info` and resolved each with `POST …/approve {action:"approved"}` | **four consecutive host approval cards** — `Terminal` (bash), `Read File`, `Write`, `Terminal` (`rm`). Every tool of the turn surfaced `session/request_permission`; none executed silently. Each card's `tool_kind` was **`""`** (empty) and `tool_input` was the adapter's `rawInput` JSON as a **string** |
| `O6` | the same turn's `/api/ws` frames | `tool_call` frames carry `kind` (`execute`/`read`/`edit`) and `input_preview` (raw JSON text) but `input: null`; `tool_result` frames carry `content_type: ""`, `raw_ref: ""`, `truncated: false`, `original_length: null`, `recovery_hints: []`; **zero** `diff` / `file_change` / `old_string` / `new_string` keys in the entire capture |
| `O7` | the same turn's activity frames | `Session created · default · auto · via acp:claude-agent-acp`; `Turn complete: 106 events, 6 tool calls, context 0%`; one `context_usage` frame with `pct: 0.0`; a `chat_check_work_offer` frame; an `agent_request` inbox notification whose body read "(risk: destructive)" for a read-only `pwd; ls` |
| `O8` | S2 (workspace_dir = `…/.dev-home/scratch` set BEFORE binding, `PERSONALCLAW_HOME=…/.dev-home`), turn: "Run exactly one Bash command: pwd" | no approval card (read-only-bash auto-resolved); `pwd` → **`~/.personalclaw/workspace`**. Neither the session's `workspace_dir` nor the configured `PERSONALCLAW_HOME` reached the ACP process; the file the CLI later wrote landed in the operator's real home |
| `O9` | S2's activity frames | `Injected 11,560 chars of context (memory, lessons, history, episodic)` — turn-0 context assembly runs for an ACP turn |
| `O10` | `security_events.jsonl` under the isolated home after O5/O8 | 12 `tool_invocation` rows for the ACP turns, hash-chained, e.g. `operation: "Terminal", tool_kind: "execute", outcome: "invoked", metadata: {"risk": "destructive"}`, plus `outcome: "approved"` rows carrying the approval decision. No `risk_level` other than the heuristic one |
| `O11` | `ls .dev-home/session_pid_*.txt` | `session_pid_28787.txt` → `dashboard:chat-1-…`, `session_pid_52634.txt` → `dashboard:chat-2-…` — the session-key resolution file IS written for ACP sessions, and the two sessions hold **two different PIDs** |
| `O12` | `sqlite3 .dev-home/memory.db` + `learning.db` after the 6-tool-call ACP turn | `semantic_memory`, `episodic_memories`, `memory_events`, `mem_*` all `0`; `learning.db staging` `0` — no procedural-outcome row from an ACP turn |
| `O13` | S2 with `POST /api/chat/task-mode {mode:"ask"}`, then "Use your Write tool right now to create … ask-mode-probe.txt" | the write was **blocked**, not executed: `tool_result` = "Tool permission request failed: Error: Tool use aborted" and the tool line read `Write … (Ask mode — only read-only tools run (switch to Agent to make changes))`. The file was never created. A follow-up **read-only** `ls` bash was denied by the same gate |
| `O14` | S2 in `plan`, "Quote verbatim every line of your context containing 'Task mode'" | the reply quoted `## Task mode: Agent` — but from **replayed sibling-session history**, not the live framing. Re-run on a FRESH session (S3) created with `task_mode=plan` before its first turn quoted `## Task mode: Plan` and "Plan mode is active… you MUST NOT make any edits" → the framing is live and correct; the stale line was history |
| `O15` | S3's first-turn context (fresh session) | `Injected 18,394 chars of context (memory, lessons, history, episodic)` and the quoted context contained verbatim user/assistant turns from a DIFFERENT dashboard session — cross-session recall reaches an ACP turn |
| `O16` | killed the gateway, restarted it, then sent a turn on the SAME session S3 | `GET /api/chat/sessions` now reports S3 with `acp_provider: ""`, `task_mode: "agent"`, `workspace_dir: ""`; the turn resolved on the **native** axis and errored `no model provider resolves for use case 'chat'`. `gateway.log`: `Pool decision: key=dashboard:chat-3-… resume_sid=None …` — no `session/load` was attempted. `session_map.json` holds a sid for the session (`2cb03780-…`) but records no `cwd` |
| `O17` | S4: `workspace_dir` set to `…/.dev-home/scratch`, provider bound, one `pwd`; DEBUG logging on | `gateway.log`: `Pool decision: key=dashboard:chat-1-1786980914 … cwd=/private/tmp/aap1-wt/.dev-home/scratch pool_cwd=/private/tmp/aap1-wt/.dev-home/scratch` — the cwd DID reach `get_or_create`; the ACP process still answered `pwd` → `~/.personalclaw/workspace`. The cwd is dropped between `get_or_create` and the spawn |
| `O18` | `grep -in "set_config_option\|session/new\|mcpServers\|set_mode" gateway.log` at DEBUG | nothing — the host logs no ACP wire frames, so mode/`mcpServers` forwarding cannot be observed from the host's own logs; the marks below that depend on it are taken from CLI-observable behavior instead |
| `O19` | S5: provider bound, `task_mode=plan` set BEFORE the first turn, then "Use the Write tool now to create … pm3.txt" | the CLI did **not** call `Write`. It emitted a `"Ready to code?"` permission request whose `tool_input.plan` says "My CLI permission mode is `plan`, which forbids writing anywhere except this plan file"; its final reply says "Permission mode: `plan` (Claude Code's CLI plan mode)". Rejecting the card left the file uncreated. On S4, where `plan` was set MID-conversation, the same request produced a plain `Write` attempt and the CLI reported "Agent" mode |
| `O20` | S6 bound with `{model: "global.anthropic.claude-haiku-4-5-…", reasoning_effort: "low"}`, then "Name the exact model id you are running as" | the bind echoed both values; the activity line read `Session created · default · global.anthropic.claude-haiku-4-5-20251001-v1:0 · via acp:claude-agent-acp`; the CLI answered "Model: `global.anthropic.claude-haiku-4-5-20251001-v1:0`; reasoning effort not specified in session context" |
| `O21` | S6: "Run this exact bash command: `git push --dry-run .`" | `tool_result` = "Permission denied: Permission to use Bash with command git push --dry-run . has been denied." **No `approval` frame reached the host** and `security_events.jsonl` contains **zero** rows mentioning `push` — the denial came from the CLI's own deny list, not the host's, and left no SEL row |
| `O22` | S6: a correction turn ("No - stop doing that. Always answer me in exactly one sentence…") | two `activity_event` frames of kind `learned`: `Learned: never more` and `Learned: User correction to honor: No - stop doing that…`. The CLI also wrote a memory file into the operator's real `~/.claude/projects/…/memory/` |
| `O23` | S6: sent the literal message `/compact` | the turn hard-failed: `Prompt error: {'code': -32601, 'message': '"Method not found": _vendor.dev/commands/execute'}`. No plain-prompt fallback ran. SEL logged `slash_command` with `outcome: bypass` |
| `O24` | S6 in `ask` mode: "Attempt these six Write tool calls one after another without stopping, even if each one fails" | **six** consecutive `tool_result` failures in ONE turn ("Tool permission request failed: Error: Tool use aborted"), then `Turn complete: 106 events, 6 tool calls`. No warn, no block, no circuit abort, no steering injection — the native breaker's warn@3 / block@5 / circuit thresholds never fired |
| `O25` | `POST …/{S6}/regenerate` then `POST …/{S6}/fork` | the regenerated assistant message carries `variants` + `variant_idx`; the fork created `chat-4-…` with 24 messages — but the fork's `acp_provider` is `""`, so the branch does not inherit the ACP binding |
| `O26` | `POST /api/chat` with a second message while a turn was in flight | **inconclusive** — the probe turn (count to 40) finished 1.2 s before the second message landed, so the two never overlapped; `queue` stayed `null` and the second message ran as its own turn. Recorded as not-exercised rather than as a queue verdict |
| `O27` | 2026-08-19 re-drive of the unattended cell: `POST /api/loops {kind:"code", attended:false}`, `PUT` bound `provider: acp:claude-code` + an isolated `workspace_dir`, then `PATCH {action:"start"}` on a home whose `chat`+`background` both resolve to a local model | **it runs and it does not wedge.** Status `running`; the adapter process spawned under the isolated home; SEL logged `mode_change:unattended_auto_approve` → `allowed`, `resources="session=dashboard:loop-961dcbd8 mode=bypassPermissions"`, plus `set_approval_policy` → `auto` and the worker's tool as `Terminal` `invoked` then `ungated`; the task's file appeared in the workspace with the right contents at ~t+225s; `/api/approvals` was `[]` throughout. The audit predicted ABSENT for this cell, so the verdict is DIVERGED-better, matching the `unattended` re-injection now in `provider_bridge.py` for the ACP branch |
| `O28` | auto-nudge re-arm on `acp:claude-code` (K43's recipe): `POST /api/autonudge {session_name, idle_secs: 20, max_cycles: 2}`, then silence | **armed, fired, re-armed, fired, capped** — `cycle_count` 0 → 1 → **2 of 2**, `active` true → **false** after the cap, `last_fire_ts` set twice, and the transcript carries BOTH injected `[auto-nudge cycle N]` turns with claude-code's own `NUDGED` reply after each. Same verdict as kiro's `K43` |
| `O29` | memory consolidation (K42's recipe): 6-message session, then `POST /api/memory/consolidate {"key": "dashboard_<session>"}` | **CONFIRMED, read from the store rather than the reply.** The route answered `{"ok": true}`, a `locks/consolidate_dashboard_<session>` lock appeared, and the history metadata line moved `last_consolidated` **0 → 6**. `semantic_active` (4) and `episodic_active` (1) did **not** move, which is correct for six short probe turns — the pass is that the consolidation RAN and advanced its offset, not that it invented rows |
| `O30` | tool-disable prefs: `POST /api/mcp/toggle-tool {"server":"personalclaw-core","tool":"get_context","enabled":false}` on a claude-code-bound home | **ABSENT, same named reason as kiro's `K45`** — `{"error": "server 'personalclaw-core' not found"}`. The only per-tool disable surface addresses *configured* MCP servers, and an ACP CLI's tools are not registry entries. Provider-independent, so this cell is decided for claude-code by the same mechanism |
| `O31` | the skill ladder: `GET /api/skills/proposals` after the session's turns | `{"proposals": []}`, exactly as kiro's `K44`. The reason is provider-independent — there is no forced-run surface, so "the gate was not met" and "the review is inert" are the same observation from outside (`G44`). Stays NOT-EXERCISED for an INSTRUMENTATION reason, not a fixture gap |
| `O32` | the combined prompt-side probe (K30's recipe) in ONE turn on a fresh claude-code session: `agent=aap1-probe` (a profile carrying `system_prompt` + `voice`), `color_theme=lumon`, `meta.knowledge=[<item id>]`, `meta.files=[<home>/uploads/aap1-brief.txt]`, and the literal `@aap1-prompt` in the message, asking for six verbatim quotes | **four cells CONFIRMED in one turn.** Reply quoted, verbatim: (1) `MANDATORY PROFILE MARKER: … PROFILE-MARKER-AAP1X …` — and it emitted the token at the top, i.e. it OBEYED the profile prompt; (2) `The AAP1 KIWI PROTOCOL states: the secret sweep codeword is ZANZIBAR-8821 …`; (3) `ATTACHMENT-CONTENT-MARKER-A77: … the sweep vehicle is a hovercraft.`; (6) `## Task mode: Agent`. So the agent-profile system prompt, the knowledge injector and the attachment extractor all reach claude-code, matching kiro's `K30` |
| `O33` | item (5) of the same turn: the persona/voice line | **CONFIRMED, and it surfaced a conflict.** BOTH persona lines were delivered and the CLI quoted both: the profile's `voice` (`Use a terse operator voice…`) **in the agent system-prompt block**, and the `color_theme=lumon` line (`Use a Lumon-inspired persona…`) **appended to the user request**. The reply says so unprompted — *"a genuine conflict, not an ambiguity I can resolve from the payload"* — and picked the profile's. So Lumon persona injection reaches an ACP provider (the cell), and the two injection sites have no precedence rule (`G45`) |
| `O34` | `@prompt` expansion, the STRONG form (K31's control): `PUT /api/prompts/aap1-prompt` with a body, `POST …/render` to prove it exists server-side, then one turn referencing `@aap1-prompt` | **ABSENT on the ACP path, provider-independently.** `/render` returned the body verbatim; the **persisted user message stored the literal `@aap1-prompt` with the expanded body absent**; claude-code replied exactly `ABSENT`. Expansion is composer-side (`ChatPage.tsx` calls `/render`), so nothing on the ACP path expands an `@name` — kiro's `K31` reproduced |

### 4a. Prompt-side context — claude-code column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Memory recall injection (turn-0 context) | WIRED | CONFIRMED | WIRED | `O9`, `O15` — the injection line fires and the CLI quoted injected memory/history text back verbatim |
| Knowledge context (@-mention + picker `meta.knowledge`) | WIRED | **CONFIRMED** (re-drive) | WIRED | `O32` — with `meta.knowledge=[<item id>]` the CLI quoted the stored item verbatim, marker included: `the secret sweep codeword is ZANZIBAR-8821` |
| Attachments/paste (extracted text prepended) | WIRED | **CONFIRMED** (re-drive) | WIRED | `O32` — `ATTACHMENT-CONTENT-MARKER-A77` quoted verbatim from a `meta.files` path |
| @prompt expansion (+ typed vars, snippets) | WIRED | **CONFIRMED** (re-drive) | WIRED, but composer-side — nothing on the ACP path expands it | `O34` — the prompt body was written with `PUT` and proven server-side by `/render` FIRST, then a turn carrying the literal `@aap1-prompt` persisted the literal and the CLI answered `ABSENT`. Same conclusion as kiro's `K31`, and provider-independent |
| Skills index in context + `skill_invoke`/`skill_search` execution | PARTIAL | CONFIRMED | PARTIAL | `O4` — the CLI reported the index arrived as prompt text ("the session context references … `skill_invoke`") while the tools themselves are absent from its list |
| Session-live skill drafts (`skill_remember`) | PARTIAL | CONFIRMED | PARTIAL | `O4` — same measurement: no `skill_remember` in the CLI's tool list |
| Task-mode framing (Agent/Ask/Plan/Build suffix) | WIRED | CONFIRMED | WIRED | `O14` — a fresh plan-mode session's context contains `## Task mode: Plan` + "Plan mode is active… you MUST NOT make any edits" |
| Agent profile system prompt / voice layer | PARTIAL | **CONFIRMED** (re-drive) | WIRED | `O32` — a profile carrying a distinctive `system_prompt` was bound; the CLI quoted the marker **and obeyed it** (emitting the token at the top of the reply), so the audit's "the CLI's own prompt dominates" worry does not hold here |
| Project binding (context preamble + cwd) | WIRED | **DIVERGED** | PARTIAL — preamble only | `O8`, `O17` — the preamble/context half works, but the cwd half does not: `cwd` reaches `get_or_create` and the ACP process still runs in `~/.personalclaw/workspace` |
| project_id → artifact stamping | ABSENT | CONFIRMED | ABSENT (stronger) | `O4` — `artifact_save` is not reachable at all on claude-code, so there is nothing to stamp |
| Persona injection (Lumon theme) | WIRED | **CONFIRMED** (re-drive) | WIRED | `O33` — `color_theme=lumon` on the turn; the CLI quoted the Lumon instruction verbatim. It ALSO carried the profile's `voice` and reported the clash itself (`G45`) |
| Cancelled-turn preamble re-injection | WIRED | NOT-EXERCISED | — | no turn was interrupted mid-flight |
| Compressed thread-history bootstrap (new process) | WIRED | CONFIRMED | WIRED | `O14`, `O15` — a brand-new session's context replayed prior turns verbatim, including sibling-session history |

### 4b. Approvals / permissions / safety — claude-code column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Interactive approval cards | WIRED | CONFIRMED | WIRED | `O5` — four cards in one turn, each resolvable via `POST …/approve` |
| trust_reads (effective-safe auto-approve) | PARTIAL | CONFIRMED | PARTIAL | `O8` — a read-only `pwd` auto-resolved with no card; `O7`/`O10` — the same heuristic labelled a read-only `pwd; ls` "destructive", so the downgrade is name/kind-based and coarse |
| Trust (session) / YOLO (global) auto-approve | WIRED | NOT-EXERCISED | — | every card was resolved with `approved`/`rejected`; `trust` and YOLO were never used |
| Per-agent approval floor ("Always allow") | WIRED | NOT-EXERCISED | — | no agent profile with `approval_mode: auto` was bound |
| Task-mode enforcement BEFORE approval (trust can't bypass) | PARTIAL | CONFIRMED | PARTIAL — but the measured residual is narrow | `O13` (ask blocks `Write` AND a read-only `ls`), `O19`/`O24` (plan blocks every `Write`), and the SEL rows carry `reason: task_mode:ask` / `task_mode:plan`. Across 44 SEL-audited ACP tool events (24 `invoked`, 11 `denied`, 4 `approved`, 3 `surfaced`, 1 `rejected`, 1 `bypass`) **no tool executed without reaching the host gate**; the only non-surfacing case was a CLI-side *denial* (`O21`) |
| Plan mode → native backend plan | WIRED | CONFIRMED | WIRED (with a caveat) | `O19` — the CLI itself reports "Claude Code's CLI plan mode" and substitutes `"Ready to code?"` for the edit. Caveat: honored only when plan is set BEFORE the session's first turn |
| Hard deny-list (`security.is_denied`) pre-execution | ABSENT | CONFIRMED | ABSENT | `O21` — the host never pre-blocked; the denial that did occur came from the CLI and produced no SEL row |
| PreToolUse hooks blocking execution | PARTIAL | NOT-EXERCISED | — | no PreToolUse hook was installed during the sweep |
| PostToolUse / Stop / SessionStart / UserPromptSubmit / Error hooks | WIRED | NOT-EXERCISED | — | as above — no hooks were installed |
| SEL audit of every executed tool + effective risk | WIRED | CONFIRMED | WIRED (with one blind spot) | `O10` — hash-chained `tool_invocation` rows with `tool_kind` and `metadata.risk` for every ACP tool, plus `approved`/`denied`/`rejected` decisions. Blind spot: a CLI-side denial is invisible (`O21`) |
| Unattended mode (strip interactive tools + fail-fast approvals, T5) | ABSENT | **DIVERGED** — the capability is PRESENT now | WIRED end to end | `O27` — an unattended Code loop bound to `acp:claude-code` reached `running`, SEL recorded `mode_change:unattended_auto_approve` `allowed` with `mode=bypassPermissions` against `dashboard:loop-961dcbd8`, the worker's write EXECUTED (`aap1-probe.txt` = `OK`) and `/api/approvals` stayed `[]` — it never wedged |
| Dry-run replay (T9 observe mode) | ABSENT | NOT-EXERCISED | — | `dry_run` has no dashboard entry point to drive as-a-user |
| OS sandbox wrap of the agent process | WIRED | NOT-EXERCISED | — | sandbox mode was left at its default; no confinement boundary was probed |
| Isolated CLI config hardening (`PERSONALCLAW_CC_ISOLATE`) | WIRED (opt-in) | CONFIRMED off-by-default, and the default measurably leaks | opt-in, and OFF is the shipped default | with the flag unset the spawned CLI loaded the operator's real `~/.claude` — it enumerated the operator's own MCP servers (`O4`) and wrote into `~/.claude/plans/` and `~/.claude/projects/…/memory/` (`O19`, `O22`) |

### 4c. Tools — claude-code column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Filesystem/shell tools (cwd-confined + extra_tool_roots) | PARTIAL | **DIVERGED** — worse | PARTIAL, and NOT cwd-confined to the session's workspace | `O8`, `O17` — the CLI's own `Read`/`Write`/`Terminal` run in `~/.personalclaw/workspace` regardless of the session's `workspace_dir`, and reached `/Volumes/…`, `~/.claude` and `~/.personalclaw` freely (`O5`, `O19`, `O22`) |
| Full native tool registry (knowledge/tasks/loops/inbox/memory/artifacts/workflows/subagents/web/schedule) | UNKNOWN | CONFIRMED (§5 gap 1 predicted "likely absent") | **ABSENT** | `O4` — `knowledge_search` NO, `task_create` NO, `notify` NO; no `personalclaw-core` MCP server in the CLI's tool list |
| Tool disable prefs (PT3/UT4 per-tool + per-provider) | ABSENT | **CONFIRMED** (re-drive) | ABSENT — the only per-tool surface cannot address an ACP CLI's tools | `O30` — `{"error": "server 'personalclaw-core' not found"}`, the same named reason as kiro's `K45` |
| Per-turn tool retrieval + progressive disclosure (`tool_search`/`tool_schema`) | ABSENT | CONFIRMED | ABSENT | `O4` — the CLI enumerated only its OWN tools (including its own `ToolSearch`); no host-injected retrieval tools appeared |
| Failure breaker (warn@3/block@5/circuit@30) | ABSENT | CONFIRMED | ABSENT | `O24` — six consecutive failures in one turn, zero warn/block/circuit output |
| Structural loop detection (no-progress/ping-pong) | ABSENT | CONFIRMED | ABSENT | `O24` — six identically-shaped failing calls, no steering injection or abort |
| Typed tool-result meta (content_type/raw_ref/truncated/recovery_hints/ok) | ABSENT | CONFIRMED | ABSENT (empty, not fabricated) | `O6` — every `tool_result` carries `content_type: ""`, `raw_ref: ""`, `truncated: false`, `original_length: null`, `recovery_hints: []` |
| Structured tool-input rendering (dict → schema-driven fields) | ABSENT | CONFIRMED | ABSENT | `O6` — `input: null` on every frame; only `input_preview` (the raw `rawInput` JSON as text) is populated. `O5` — the approval card's `tool_input` is likewise a string |
| File-change diff chips (write/edit before-after) | ABSENT | CONFIRMED | ABSENT — and the raw material IS present | `O6` — zero `diff`/`file_change`/`old_string`/`new_string` keys, while the first `tool_call` frame of each call DOES carry `kind: "edit"`, which is exactly the by-kind signal §2.5 proposes |
| AskUserQuestion card | UNKNOWN | CONFIRMED (audit: fires only if the CLI exposes an identically-named tool) | **ABSENT** | `O4` — the CLI's full tool list contains no `AskUserQuestion`, and the MCP route that could supply one is unreachable |
| Subagents (`subagent_run` + completion inject-back) | UNKNOWN | CONFIRMED (audit: only via personalclaw-core MCP if reachable) | **ABSENT** — though its session-key precondition holds | `O4` — no `subagent_run` tool. `O11` — the `session_pid_<pid>.txt` file the inject-back depends on IS written for ACP sessions, so only the tool itself is missing |
| MCP tools (external servers) | PARTIAL | CONFIRMED | PARTIAL — and the subset is the OPERATOR'S, not PersonalClaw's | `O4` — the spawned CLI enumerated builder-mcp / slack-mcp / aws-mcp / chrome-devtools from the operator's own `~/.claude` config; nothing from PersonalClaw's `mcp.json` |
| Queue-steering mid-turn (#37) | ABSENT | NOT-EXERCISED | — | `O26` — the mid-turn send probe missed the window; neither steer nor queue was observed |

### 4d. Learning / memory — claude-code column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Preference-facet capture (every turn) | WIRED | CONFIRMED | WIRED | `O22` — `activity_event` kind `learned`: "Learned: never more" (a poor extraction, but the path ran) |
| Correction→lesson review | WIRED | CONFIRMED | WIRED | `O22` — "Learned: User correction to honor: …" fired on the correction turn, with no model provider needed |
| Procedural-outcome capture (M5d tool-outcome drain) | ABSENT | CONFIRMED | ABSENT | `O12` — after a 6-tool-call ACP turn, `memory.db` (`semantic_memory`, `episodic_memories`, `memory_events`, `mem_*`) and `learning.db staging` were all `0` |
| Skill-ladder review (4-tier, propose-only) | WIRED | NOT-EXERCISED | — | the ladder's expensive review needs a model provider, which this isolated home lacks |
| Memory consolidation on session end | WIRED | **CONFIRMED** (re-drive) | WIRED | `O29` — `last_consolidated` **0 → 6** in the history metadata plus a `consolidate_…` lock. Read from the store, not from the route's `{"ok": true}` |
| Incognito/restricted no-write guarantees | WIRED | NOT-EXERCISED | — | no incognito/restricted session was driven |

### 4e. Session / conversation mechanics — claude-code column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Variants / regenerate (‹n/N› switcher) | WIRED | CONFIRMED | WIRED | `O25` — the regenerated assistant message carries `variants` + `variant_idx` |
| Edit & resend, branch continuation (fork) | WIRED | CONFIRMED with a caveat | WIRED, but the branch loses the runtime | `O25` — the fork carries all 24 messages and `acp_provider: ""` |
| Queued messages (merge/pop + live bubbles) | WIRED | NOT-EXERCISED | — | `O26` — probe missed the in-flight window |
| Empty-turn auto-retry | WIRED | NOT-EXERCISED | — | no empty turn occurred across ~14 turns; not forceable as-a-user |
| Auto-nudge re-arm (loops) | WIRED | **CONFIRMED** (re-drive) | WIRED | `O28` — `cycle_count` 0 → 1 → **2 of 2**, `active` true → false at the cap, both `[auto-nudge cycle N]` injections answered by the CLI |
| Context-% accounting | PARTIAL (UNKNOWN which backends emit) | **DIVERGED** | the chip is EMITTED but always reports a fabricated `0%` | `O7` — a `context_usage` frame with `pct: 0.0` and `Turn complete: 106 events, 6 tool calls, context 0%`; every one of ~14 turns reported `0%`, including turns with 18 KB of injected context |
| Compaction | WIRED (CLI-owned `/compact`) | **DIVERGED** | ABSENT via the host — the command path errors | `O23` — `/compact` returns `-32601 "Method not found": _vendor.dev/commands/execute` |
| Slash commands (via `stream_command`) | WIRED (protocol `commands/execute`) | **DIVERGED** | ABSENT — and there is NO plain-prompt fallback | `O23` — the turn hard-errors instead of degrading to a text prompt |
| Session resume across gateway restarts (`session/load`) | PARTIAL (falls to `session/new` + compressed history) | **DIVERGED** — worse | ABSENT, and the runtime silently changes | `O1` — the adapter DOES advertise `loadSession`. `O16` — after a restart the session's `acp_provider`, `task_mode` and `workspace_dir` are all cleared, `resume_sid=None` (no `session/load` attempted), and the next turn resolves on the **native** axis |
| Warm pool / instant start | WIRED | CONFIRMED (pool present, cold on this run) | WIRED-but-cold | `O17` — `Pool decision: … pool_size=0 pool_qsize=0`, so every turn cold-started; the pool path exists and was exercised, it just had nothing warm |
| Concurrent sessions on one process (P9) | ABSENT (dialect False) | CONFIRMED | ABSENT | `O11` — two concurrently-bound claude-code sessions hold two DIFFERENT adapter PIDs |
| Pipe-death auto-retry / re-queue | WIRED | NOT-EXERCISED | — | no adapter process was killed mid-turn |
| Model override per session (composer picker) | WIRED | CONFIRMED | WIRED | `O20` — the CLI named the exact pinned model id back |
| Reasoning effort per turn | WIRED | CONFIRMED host-side only | host forwards it; CLI-honoring unobserved | `O2` — the adapter advertises five efforts, so the axis exists. `O20` — the host accepted and echoed `low`, but the CLI cannot self-report its effort, so "honored" was not measured |
| Agent/persona selection | ABSENT (no persona axis) | CONFIRMED | ABSENT — and no dead UI | `O2` — discovery returns exactly one agent with `provider_agent: ""`, so the picker has no persona rows to offer |
| Discovered-agent ephemeral binding (chat picker → `POST …/acp-agent`) | WIRED | CONFIRMED | WIRED, and *ephemeral* is literal | `O3` — the bind round-trips. `O16`/`O25` — it does not survive a gateway restart or a fork |
| Turn telemetry (event/tool counts, tokens, cost estimate) | WIRED | CONFIRMED | WIRED (counts), context-% fabricated | `O7` — `Turn complete: 106 events, 6 tool calls, context 0%` |

### Mark counts (claude-code, 63 audit cells)

| mark | count | after the 2026-08-19 residual re-drive |
|---|---|---|
| CONFIRMED (runtime matched the audit's prediction) | 35 | **43** |
| DIVERGED (runtime contradicted it) | 6 | **7** |
| NOT-EXERCISED (no runtime observation obtained; reasons below) | 22 | **13** |

Both columns are counted from the rows above, not carried in prose — the second one is what the
rows say today.

All **four** cells the audit left literally `UNKNOWN` for claude-code are now definite:
native tool registry → **ABSENT**, `AskUserQuestion` → **ABSENT**, subagents → **ABSENT**,
context-% emission → **emitted but always 0%**. The sweep is therefore **PARTIAL against
`AAP-1`'s "zero UNKNOWN cells"**: nothing is left un-decided that the audit flagged, but 13
cells were not driven and are listed rather than guessed.

### Residual not-exercised cells (and why)

**22 → 13, and the grouping below is now DERIVED FROM THE ROWS, not maintained beside them.**
Re-deriving it caught two errors in the original list that had cancelled out in the total: it counted
*the loop half of the failure-breaker check* as a cell (it is a sub-clause of a row already marked
`CONFIRMED` by `O24`) and it omitted *incognito/restricted no-write guarantees*, which is a real
`NOT-EXERCISED` row. Both are fixed here. 1 + 4 + 5 + 3 = 13, and 43 + 7 + 13 = 63.

1. **Needs a model provider in the isolated home** (~~5~~ **1**, and the original REASON is dead):
   ~~unattended mode~~ (`O27`), ~~auto-nudge re-arm~~ (`O28`) and ~~memory consolidation~~ (`O29`)
   are closed — a loop/cron run no longer dies on `no model provider resolves for use case
   'chat'|'background'` (`O16`), because a local model serves both. The one survivor is not waiting
   on a provider either: **skill-ladder review** reproduced kiro's `G44` exactly (`O31` —
   `{"proposals": []}`, and the route census has accept/promote/verify but no forced-run surface),
   so it needs *instrumentation*. Provider-independent.
2. **Needs a fixture that was not built** (~~9~~ **4**): ~~knowledge @-mention~~ (`O32`),
   ~~attachment/paste~~ (`O32`), ~~@prompt expansion~~ (`O34` — ABSENT, provider-independent),
   ~~agent-profile system prompt~~ (`O32`), per-agent approval floor, PreToolUse hooks, the other
   five hook kinds, incognito/restricted no-write guarantees, ~~tool-disable prefs~~ (`O30` —
   ABSENT, provider-independent), ~~Lumon persona injection~~ (`O33`). **Three of the four have a
   proven recipe** — `K36` for the approval floor, `K39` for PreToolUse blocking (six lifecycle
   triggers, the PreToolUse one exiting non-zero), `K40` for the other five kinds counted from the
   hooks' own log. The fourth, incognito, has kiro's `K33` as its recipe.
3. **Needs a timing/failure injection that did not land** (5): queued messages and
   queue-steering (`O26` — the probe turn finished 1.2 s early), cancelled-turn preamble,
   empty-turn auto-retry, pipe-death auto-retry.
4. **No as-a-user entry point** (3): dry-run replay, OS sandbox confinement probe, and
   trust/YOLO auto-approve (deliberately not enabled — every card was resolved explicitly so
   the gate itself stayed measurable).

Not a cell, but worth carrying: **the loop half of the failure-breaker check** needs six consecutive
failing tool calls *inside a loop* (kiro's `K15` shape). Its chat half is already decided — `O24`
found zero warn/block/circuit output after six failures in one turn (`G6`).

### Gap inventory — severity-ranked (claude-code findings)

**P0 — safety**

- **`G1` The ACP process cwd escapes BOTH the session's workspace and the configured home.**
  With `workspace_dir` set to a scratch dir and `PERSONALCLAW_HOME` set to an isolated dir, the
  spawned CLI answered `pwd` → `~/.personalclaw/workspace` and wrote its files there (`O8`,
  `O17`). The cwd DOES reach `get_or_create` (`cwd=…/scratch` in the `Pool decision` log) and is
  dropped below it: `src/personalclaw/llm/acp_agent.py:771` reads `cwd` from `entry.options`
  only, so the per-call build kwarg `registry.build(…, cwd=cwd, …)` passes through `**kwargs`
  unread, and `src/personalclaw/acp/client.py:131` then falls back to a hardcoded
  `Path.home() / ".personalclaw" / "workspace"` that ignores `config_dir()`. Consequences: a
  bound Project's cwd is not honored for ACP; the CLI's file/bash tools operate inside the
  harness's own state dir; and any isolated-home deployment (dev, test, a second entity) is
  silently escaped. **Not fixed here — outside this atom's fence.** ⚠️ **SUPERSEDED as of the
  2026-08-22 audit below: the mechanism this row describes has since changed on `main` —
  `llm/acp_agent.py:826` now reads `str(kwargs.get("cwd") or "").strip() or options.get("cwd")`
  (the dropped kwarg), and `acp/client.py:137` records that the hardcoded
  `Path.home()/".personalclaw"/"workspace"` fallback was replaced. Whether that closes G1's full
  clause is a re-verification question, not a reading — it is listed as a candidate in the audit
  entry. Do not treat this paragraph as current.** Owner: core seam, and it
  should front-run `AAP-9`'s `project_id` work since both are the same "session scope doesn't
  cross the seam" defect.
- **`G2` Host gate coverage is contingent on the operator's own `~/.claude`, not structural.**
  Measured coverage was total — 44 SEL-audited ACP tool events, every one surfaced `session/request_permission`
  (`O5`, `O13`, `O19`, `O24`) — but only because `PERSONALCLAW_CC_ISOLATE` is off by default and
  this operator's real settings happened to auto-approve nothing. The same default let the
  spawned CLI enumerate the operator's own MCP servers (`O4`) and write into `~/.claude/plans/`
  and `~/.claude/projects/…/memory/` (`O19`, `O22`). An operator with `permissions.allow` entries
  gets silent execution with no host card. This is the measurement `AAP-5` needs: the fix is to
  make the isolated config the bundled default for host-managed sessions, not to add a gate.

**P1 — capability-dead**

- **`G3` No `personalclaw-core` surface at all** (`O4`) — knowledge, tasks, inbox, artifacts,
  workflows, subagents and `notify` are unreachable; `artifact_save` therefore cannot exist,
  which makes `project_id` stamping vacuous rather than merely unstamped. Confirms audit gap 1;
  owner `AAP-4`. The prong-A question is still open: nothing in the host logs the ACP wire, so
  whether claude-code would honor protocol-passed `mcpServers` was NOT determined (`O18`).
- **`G4` Slash commands are broken, not merely unsupported** (`O23`) — `/compact` returns
  `-32601 "Method not found": _vendor.dev/commands/execute` from adapter 0.60.0 and the turn
  **hard-errors**; the plain-prompt fallback the audit assumed does not run. The audit marked
  this cell WIRED for claude-code; it is not. Owner: core seam (capability-gate `stream_command`
  and fall back to text), `AAP-9`.
- **`G5` A gateway restart silently changes the runtime** (`O16`) — the session's `acp_provider`
  and `task_mode` are cleared, `resume_sid` is `None` (no `session/load`
  attempted even though the adapter advertises `loadSession`, `O1`), and the next turn resolves
  on the native axis. On this provider-less home that surfaced as an error; on a machine with a
  native model provider it would silently run a *different* runtime with different tools and
  different confinement. Strictly worse than the audit's "falls to compressed history". Owner
  `AAP-7`, and the binding-persistence half is new scope for it.
  *Corrected 2026-08-21 (audit-first pass, see the execution log):* this bullet originally named
  `workspace_dir` as a third cleared field. It is not — `workspace_dir` has always had both a
  writer (`chat_persistence._save_session_to_history`) and a reader in **both** restore paths,
  and a measured save→restart→restore round-trip returns it unchanged. `O16`'s observation of
  `workspace_dir: ""` stands as recorded; the inference that the restart cleared it does not,
  because S3 was never given one (`O8`/`O17` set it on S2/S4). The same caution applies to `K20`'s
  `reasoning_effort: null`: that field round-trips too. Two fields were lost, not four.
- **`G6` No host-side brake on a failing ACP loop** (`O24`) — six consecutive tool failures in
  one turn produced no warn, no block, no circuit abort and no steering injection. Confirms
  audit gap 5; owner `AAP-6`.
  **STILL OPEN as of 2026-08-21** (see the `AAP-8` log entry). `AAP-6` built the whole brake and
  correctly stamped the failure bit in `acp/translate.py:238`, but `acp/adapter.py` did not map
  `tool_meta`, so the bit never reached `chat_runner.py:2877` and `_acp_failed` could never be True.
  `AAP-8` fixed that one adapter line; the remaining `G6` question is a live drive, which is
  owner-gated. Do not read `AAP-6`'s log as having closed this.
- **`G7` Procedural memory never learns from ACP turns** (`O12`) — zero rows after a 6-tool-call
  turn. Confirms audit gap 4; owner `AAP-8`.
  **Root cause + code fix landed 2026-08-21** (see the `AAP-8` log entry): `drain_tool_outcomes`
  existed only on the native runtime, so `chat_runner.py:238`'s duck-typed read missed on every
  `acp:*` provider. Both ACP providers now accumulate per turn via `acp/outcomes.py`. Measured in
  tests, **not** re-measured on a live CLI — that drive is owner-gated, so this row stays as measured.

**P2 — fidelity**

- **`G8` The context-% chip reports a fabricated `0%`** (`O7`) — a `context_usage` frame IS
  emitted every turn with `pct: 0.0`, and `Turn complete: … context 0%` printed `0%` on all ~14
  turns including ones carrying 18 KB of injected context. This is worse than absent: the UI
  states a number the backend never supplied. Owner `AAP-8` — the honest shape is to omit the
  chip when the backend emits no stats, not to print zero.
- **`G9` Tool-card fidelity, with the raw material already present** (`O6`) — `input: null`,
  empty result meta, zero diff keys; but each first `tool_call` frame carries
  `kind: "read"|"edit"|"execute"` and each update carries the adapter's `rawInput` JSON. Owner
  `AAP-8`; the by-kind mapping §2.5 proposes is confirmed viable at runtime.
- **`G10` Effective risk is heuristic and mis-calibrated in both directions** — a read-only
  `pwd; ls` was labelled `destructive` in the SEL row and the approval notification (`O7`,
  `O10`), while ask-mode denied a read-only `ls` outright (`O13`). Every ACP permission request
  arrived with `tool_kind: ""` (`O5`), so even the kind→floor mapping has nothing to read on the
  approval path — the `kind` lives on the `tool_call` frame, not the permission frame. Owner
  `AAP-8`; that asymmetry is a design input for §2.5.
  **FIXED 2026-08-21 (`AAP-8`, code-only). Three of this bullet's claims were wrong — corrected
  here, with the rows they misread cited:**
  1. **"the `kind` lives on the `tool_call` frame, not the permission frame" is false as a
     protocol statement.** ACP types the permission frame's `toolCall` as a `ToolCallUpdate`, so
     `kind` *and* `rawInput` are both legal fields there; codex populates `kind` (`C5`, and `G18`
     above already noted this correction), claude-code-acp populates neither (`O5`). The true
     statement is narrower and is what the fix keys on: **the kind is always on the `tool_call`
     frame and only sometimes on the permission frame**, and both frames carry the same
     `toolCallId`, so the correlation the input cache already performed was available for the kind
     too.
  2. **The `destructive` label and the empty `tool_kind` are two different defects with two
     different symptoms** — this bullet folded them into one cause. `O10`'s row is written from the
     **`tool_call`** frame (`chat_runner.py:2649`), where the kind IS present as `execute`; what was
     missing there is the *command text*, because ACP agents open a call with `rawInput: {}` +
     `status: pending` and fill the input in a later `tool_call_update`
     (`translate.extract_tool_update_events`' own docstring says so). `resolve_effective_risk`
     returned the **literal** `"destructive"` for that state — a verdict about the command minted
     from the command's absence. The empty permission-frame `tool_kind` (`O5`) caused something
     else entirely: a wire-declared `kind: "read"|"search"|"fetch"` reached the resolver as `""` and
     floored at `caution`, and since trust-reads auto-approves only `safe`, **a plain file read
     raised a card forever even with trust-reads on** — which is the real mechanism behind the
     `trust_reads` row's PARTIAL verdict, not the coarseness that row's prose blames.
  3. **`O7`'s attribution of the `destructive` notification to `pwd; ls` is unresolved and probably
     wrong.** The notification body is composed at `chat_runner.py:570` from the **permission**
     event's risk, where `tool_kind` was `""`; measured against the real decoder, that state
     resolves `safe` when the command is readable and `caution` when it is not — it cannot reach
     `destructive` for a title with no destructive verb. The `O5`/`O7` turn raised **two** cards
     both titled `Terminal` (the bash and the `rm -f doomed.txt`), and the body names only the
     title, so the observed row cannot be attributed to either from the body alone. The `rm` is
     genuinely destructive and labelled correctly. Left as recorded rather than rewritten: `O7` is
     an observation, and re-attributing it needs an owner-gated live drive.
  **What the fix does** (one commit): the declared kind is correlated onto the permission event by
  `toolCallId` (`acp/translate.py`, a `tool_call_kinds` cache alongside the existing
  `tool_call_inputs` one, owned per-turn by `AcpSession`); the permission decoder's inline-input
  fallback now reads `rawInput` as well as `input`/`params`; the input cache is read rather than
  popped; and `task_modes.py` gains **one** tri-state vocabulary, `classify_invocation` →
  `READ_ONLY`/`MUTATING`/`UNCLASSIFIED`, which `_is_read_only_tool`, `task_mode_denies` and
  `resolve_effective_risk` all derive from. `UNCLASSIFIED` fails **closed at the gate** (an
  unreadable command does not run under ask/plan/build) and **honest at the label** (`caution` —
  never `safe`, so a card still appears; never `destructive`, which asserted what nobody measured).
  The kind is still **not** fed to `task_mode_denies` — §2.2's recorded choice, now pinned by a
  structural test.
- **`G11` A CLI-side denial is invisible to the audit trail** (`O21`) — `git push --dry-run` was
  denied by the CLI's own deny list; no `approval` frame reached the host and
  `security_events.jsonl` has zero rows for it. The host's SEL therefore under-reports what the
  CLI refused, which is the mirror image of gap 2 and belongs in the §2.7 parity doc.
- **`G12` `acp_mode` is honored only when set before a session's first turn** — plan set before
  the first turn put the CLI in real plan mode (`O19`); plan set mid-conversation produced a
  plain `Write` attempt and the CLI reported "Agent" mode, even though a new session line was
  emitted. Mechanism not isolated. Owner `AAP-9`.
- **`G13` A fork does not inherit the ACP binding** (`O25`) — the branch carries all 24 messages
  with `acp_provider: ""`. Same ephemeral-binding root as `G5`.

**P3 — cosmetic / legibility**

- **`G14` The activity line names the adapter, not the runtime the user picked** (`O7`) —
  `Session created · … · via acp:claude-agent-acp`, because
  `AcpAgentProvider.provider_id` is `f"acp:{Path(self._command[0]).name}"`
  (`src/personalclaw/llm/acp_agent.py:72-75`). The bundle deliberately passes the dialect
  explicitly to avoid basename inference; the label was missed. Under the npx fallback the same
  line would read `acp:npx`. **FIXED** (`fix-g14-g15-session-line`): `provider_id` now returns the
  configured `acp:<cli>` entry name (threaded as `runtime_id` through `_factory`), basename only as
  the fallback — the same rule `AcpSessionProvider.provider_id` and `discover_agents`'
  `options["runtime_id"]` already followed.
  **CORRECTION — `G14` was misfiled as P3/cosmetic.** The same basename value is *also* the
  not-gateable/SEL provider key: `chat_runner.py:2497-2499` derives `_acp_cli = provider_id[4:]`
  and feeds it to `acp_permission_authority.not_gateable_entry()` (`:1367`) plus two SEL audit rows
  (`:1414` `"provider": acp_cli`, `:2914`). `normalize_provider` maps `claude-agent-acp` →
  `claude-code` via `_PROVIDER_ALIASES`, but **`npx` resolves to no coverage row at all** — so under
  the npx fallback a *declared* not-gateable tool read as an undeclared hole, and every ungated-tool
  audit row named the adapter rather than the runtime. The label was the visible symptom of a
  security-legibility defect, not the whole of it.
- **`G15` "Session created" prints on EVERY turn** — the SYMPTOM is real and now fixed; the filed
  mechanism and recommendation were both wrong. **CORRECTED:** (a) the line cite was stale — the real
  sites were `chat_runner.py:1912-1930` on `origin/main` at `05bba66e`; (b) a `resumed` branch *did*
  exist (`:1913`), so "never says resumed" was an overstatement — it said "resumed" on the rare
  cold-reload path only; (c) the actual cause is that `SessionManager.get_or_create`'s **reuse path
  returns `resumed=False` unconditionally** (`session.py:1065`, `return provider, was_new, False`),
  so every later turn of one live session took the `else` branch; (d) **the recommendation "gate on
  `is_new`" would have inverted the same lie** — `is_new` is True only when a runner was *started*
  this turn (creation path returns `(provider, True, resumed)` at `session.py:1281`; reuse returns
  `was_new`, which is False except right after `mark_new()`), so gating the verb on it alone makes a
  reused session read "resumed". **FIXED** by splitting the two questions the one flag was being
  asked to answer: `is_new` says whether a runner was started at all, `resumed` picks the verb when
  one was — `is_new and resumed` → "resumed", `is_new and not resumed` → "created", `not is_new` →
  **"continued"** (a third, previously unsayable state). One broadcast, one format string.
  `AAP-7`'s accurate-labelling acceptance criterion needs this row too.
- **`G16` The preference-facet extractor learned the fragment "never more"** from
  "…in exactly one sentence from now on, never more." (`O22`) — the capture path is live
  (that is the CONFIRMED part) but the extraction is poor. **FIXED 2026-08-22** (see the
  execution log). Mechanism, since the bullet as filed was imprecise about it in three ways:
  (1) the writer is `preference_facets.detect_facet_candidate`'s veto branch, whose regex was
  the trigger word plus `[^.!?\n]*` — so `never` matched as a *degree adverb* and the clause
  took `" more"`; (2) the fragment reached TWO surfaces from that one detector — the
  `Learned: never more` chip (`dashboard/chat_runner.py:211-217`, which prints the returned
  text bare) and the durable lesson row `Never: never more`
  (`after_turn_review.capture_preference_facet`, which prefixes it) — so one fix closes both;
  (3) `K16`'s turn produced **two** `learned` events from **two different writers** — this one
  and `is_correction_signal` → "User correction to honor: …" — and only the first is the facet
  extractor. **`K49`'s second veto artifact is NOT closed by this fix and is a different
  defect:** `Never: never violate these):` was clipped from *injected prompt boilerplate*, so
  its root cause is provenance (capture ran over injected context that arrived inside the user
  message), not grammar — "never violate these" is a well-formed prohibition and the tightened
  rule accepts it, correctly by its own terms. That row needs the capture path to distinguish
  the user's own words from injected content; filed here rather than half-fixed.

### Incidental bugs fixed in-session

**None.** Every defect this sweep found (`G1`, `G4`, `G5`, `G8`, `G12`, `G13`, `G14`, `G15`,
`G16`) lives outside this atom's fence — in `llm/acp_agent.py`, `acp/client.py`,
`dashboard/chat_runner.py` and the learning capture path — and `G1`/`G5` are structural enough
that the plan's own rule applies: "anything structural waits for Phase 2 so fixes land against
the full three-provider picture." They are filed above at their measured severity instead of
being half-fixed.

- **`G45` Two persona injection sites, no precedence rule** (`O33`) — a profile's `voice` lands in the
  agent system-prompt block and a session's `color_theme` persona is appended to the user request, so a
  session carrying both delivers two conflicting instructions in one turn. The CLI reported it unprompted
  (*"a genuine conflict, not an ambiguity I can resolve from the payload"*) and picked the profile's.
  Not ACP-specific — the two writers are host-side — but it is what an ACP turn shows, because both
  blocks arrive as plain text the CLI can quote back. Severity **P3** (legibility): nothing is unsafe,
  the model just has to guess which voice the operator meant.

## Phase 1 results — codex verified matrix (atom `AAP-2`)

**Swept:** 2026-08-17 · **Adapter:** `@agentclientprotocol/codex-acp` 1.1.4 ·
**CLI:** `codex` 0.146.1.359 (stable) at `~/.toolbox/bin/codex`, whose own
`~/.codex/config.toml` sets `model_provider = "amazon-bedrock"`, `model = "openai.gpt-5.6-sol"`,
`model_reasoning_effort = "xhigh"` · **Node** v24.18.0 · **Host:** this repo at AAP-1's tip
(`bf0eb342`), isolated `PERSONALCLAW_HOME=/private/tmp/aap2-wt/.dev-home`, gateway on `:10431`,
`PERSONALCLAW_AUTH_MODE=none`, `CODEX_ACP_BIN` pinned to the already-provisioned adapter with
`PERSONALCLAW_ACP_NO_PROVISION=1` (so the sweep performs no `npm install` side effect).

**Method.** Same shape as `AAP-1`: the `codex-agent` bundle was installed from the first-party
apps dir into the isolated home (`POST /api/apps`), which registered the `acp:codex`
ProviderEntry; dashboard chat sessions were bound to it
(`POST /api/chat/sessions/{s}/acp-agent`) and driven through the audit §6 checklist over the
same HTTP/SSE + WebSocket surfaces the dashboard uses — `POST /api/chat` for the turn (SSE),
`/api/ws` for the activity, `tool_call`/`tool_result`, `approval` and telemetry frames the SSE
stream does not carry, and the persisted `sessions/*.jsonl`, `security_events.jsonl`,
`memory.db`, `learning.db` and `session_map.json` under the isolated home for the state half of
every mutate → persist → consume cycle. Every mark names the command or artifact that produced
it. **Note on isolation:** unlike the claude bundle there is no `PERSONALCLAW_CC_ISOLATE`
equivalent for codex — `PersonalClawApps/codex-agent/provider.py` says so in its own docstring
("Codex manages its own configuration and auth, so this bundle does **not** apply the
`CLAUDE_CONFIG_DIR` isolation the Claude bundle needs"), so every codex ACP session on this
machine runs against the operator's real `~/.codex`. That is measured below, not assumed.

**Marks.** Same vocabulary as `AAP-1`: `CONFIRMED` — the runtime matches the audit's predicted
verdict for that cell. `DIVERGED` — it does not; the observed verdict replaces the predicted
one. `NOT-EXERCISED` — no runtime observation was obtained, with the reason stated; reading the
code and reasoning that a cell *should* work is explicitly **not** a mark.

### Observation ledger (codex)

Ledger ids are `C…` so they never collide with `AAP-1`'s `O…`. `S1` = `chat-1-1787026836`
(workspace_dir set to `…/.dev-home/scratch` **before** binding).

| id | what was run | what was observed |
|---|---|---|
| `C1` | `POST /api/apps` with the first-party `codex-agent` dir, then `GET /api/agent-providers` | `acp:codex` registered, `ready: true`, `state: ready`, `detail: "initialize OK (caps: auth, loadSession, mcpCapabilities, promptCapabilities, providers, sessionCapabilities)"` — `loadSession` **is** advertised (as on claude); unlike claude there is no `_meta` capability |
| `C2` | `GET /api/agent-providers/acp:codex/agents` | one agent (`id: acp:codex`, `name: "Codex"`, `provider_agent: ""`) → no persona axis; `models` = 5 live-discovered ids (`openai.gpt-5.6-sol`, `…-terra`, `…-luna`, `openai.gpt-5.5`, `openai.gpt-5.4`); **`supported_efforts` = `[]`** (claude advertised five); `permission_modes` = the same host-side list `default, acceptEdits, plan, dontAsk, bypassPermissions` |
| `C3` | `POST …/{S1}/workspace-dir {"/private/tmp/aap2-wt/.dev-home/scratch"}` then `POST …/{S1}/acp-agent {provider:"acp:codex"}`, then `GET /api/chat/sessions` | both round-trip: `workspace_dir` = the scratch dir, `acp_provider: "acp:codex"` |
| `C4` | S1 turn 1: "Run exactly one shell command: pwd … then list your available tool names verbatim, then YES/NO for knowledge_search, task_create, notify" | `pwd` → **`~/.personalclaw/workspace`** — the escape reproduces on codex. Tool list: `exec_command, write_stdin, list_mcp_resources, list_mcp_resource_templates, read_mcp_resource, update_plan, request_user_input, apply_patch, view_image, get_goal, create_goal, update_goal, tool_search_tool, parallel`; `knowledge_search` **NO**, `task_create` **NO**, `notify` **NO**; no `personalclaw-core` MCP server. The turn's FIRST assistant chunk was the CLI's own notice — "Warning: Skill descriptions were shortened to fit the 2% skills context budget. Codex can still see every skill…" — i.e. the operator's real `~/.codex` skills/plugins loaded, and an adapter/CLI notice is rendered to the user as assistant prose |
| `C5` | S1 turn 2: read `…/scratch/probe.txt`, create `…/scratch/written.txt`, `rm -f …/scratch/doomed.txt`; `/api/ws` captured and each card resolved with `POST …/approve {action:"approved"}` | the read auto-resolved with **no** card; the edit and the `rm` each raised one `approval` frame, both `risk: "destructive"`, both with **`tool: "unknown"`** and `tool_kind: ""`. `written.txt` was created (`HELLO`) and `doomed.txt` deleted only after approval. The sibling `tool_call` frames DO carry a real title and kind (`Read file '…'`/`read`, `Editing files`/`edit`, `rm -f …`/`execute`) and the edit's `input_preview` is a **unified diff** (`--- … +++ … @@ -0,0 +1 @@ +HELLO`), while `input` is `null` on all three; every `tool_result` carries `content_type: ""`, `raw_ref: ""`, `truncated: false`, `original_length: null`, `recovery_hints: []`. `Session created · default · auto · via acp:codex-acp` printed again on this second turn, and `Turn complete: 103 events, 3 tool calls, context 0%` with one `context_usage` frame `pct: 0.0` |

| `C6` | S1 with `POST /api/chat/task-mode {mode:"ask"}` set before the turn, then "quote every context line containing 'Task mode' … then use apply_patch to create `ask-mode-probe.txt`" | the write was **blocked** — a `tool_call` (`kind: edit`) frame appeared, then **no `tool_result` at all** and the turn ended with the assistant message `*Conversation interrupted*`; the file was never created. SEL: `unknown | denied | {"reason": "task_mode:ask"}`. The model's step-1 answer quoted `## Task mode: Agent` (its 4th turn on a session whose first three ran in agent mode, so live-vs-replayed could not be separated here) |
| `C7` | S2 = a FRESH session, provider bound, `task_mode=plan` set **before** its first turn, then "quote 'Task mode' lines … state your CLI's approval mode … use apply_patch to create `plan-mode-probe.txt`" | the CLI **called `apply_patch` anyway** (`tool_call`, `kind: edit`) — no `update_plan`, no plan artifact, no sign of a native plan mode. The host blocked it and the turn again ended `*Conversation interrupted*` before the model answered steps 1-2. File never created |
| `C8` | S2: sent the literal message `/compact` | identical failure to claude: `Prompt error: {'code': -32601, 'message': '"Method not found": _vendor.dev/commands/execute'}`, no plain-prompt fallback, no compaction |
| `C9` | S2: "use your `request_user_input` tool right now to ask me to choose RED or BLUE" | `request_user_input` **failed CLI-side**: "`request_user_input is unavailable in Default mode`". No host card, no `approval` frame, **no SEL row**, and the turn's telemetry counted `0 tool calls` |
| `C10` | S1: six `cat /nonexistent-aap2-probe-N` commands, one tool call each, "do not stop early"; 30 s in, a second `POST /api/chat` was sent while the turn was running | all six ran and failed (`exit_code: 1` each) → **no warn, no block, no circuit abort, no steering injection**; `Turn complete: 28 events, 6 tool calls`. The mid-turn message returned `{"ok": true, "queued": true}` and produced a `queue_push` frame with a `queue_id`; after the turn a `queue_pop` + `chat_user_message` fired and it ran as its own turn (the reply was `QUEUED`). Note the six shell calls arrived titled `Read file '…'` with `kind: read` and auto-approved as `risk: safe` |
| `C11` | S1: one compound command `cat …/probe.txt && rm -f …/canary.txt`, card policy = **reject** | the adapter titled it with the raw command, the host classified it `kind: execute` / `risk: destructive` and raised a card; rejecting it left `canary.txt` **intact** and the turn continued normally (`tool_result` with `exit_code: null`, `Turn complete`). So the read/safe auto-approve is not spoofable by hiding a mutation behind a read, and a *host rejection* is graceful |
| `C12` | S3 bound with `{model: "openai.gpt-5.4", reasoning_effort: "low"}`, then "name your exact model id; state your reasoning effort; list every MCP server available to you" | the bind echoed both values and the activity line read `Session created · default · openai.gpt-5.4 · via acp:codex-acp`; `gateway.log` shows `ACP model: openai.gpt-5.4`. The CLI answered that its model id and effort are **"not exposed to me in this session"**, and listed **12 MCP servers — all the operator's own** (`builder-mcp`, `slack-mcp`, `aws-mcp`, `pippin-mcp`, `spec-studio-mcp`, `weblab-mcp`, `sage-plus-service-mcp`, `concur-mcp`, `apd-aura-mcp`, `aurora-helios-mcp`, `change-management-mcp`, `feebas-service-mcp`) — and nothing from PersonalClaw |
| `C13` | S3 turn 2: a correction ("No - stop doing that. Always answer me in exactly one sentence from now on, never more.") | two `activity_event`s of kind `learned`: `Learned: never more` and `Learned: User correction to honor: …` — the identical poor extraction AAP-1 measured. In the same turn `gateway.log` logged `ACP model: auto (from agent config)` for the newly spawned process — the session's pinned `openai.gpt-5.4` was **not** re-applied on turn 2, while the activity line still printed `openai.gpt-5.4` |
| `C14` | state under the isolated home after 10 turns: `ls session_pid_*.txt`, `session_map.json`, `sqlite3 memory.db`/`learning.db`, plus `ps` descendant counts | three `session_pid_<pid>.txt` files → one per session, **three different PIDs** (so no process sharing, and the subagent inject-back precondition holds). `session_map.json` holds a `sid` per session and **no `cwd`**. After turns carrying 6 and 3 tool calls: `memory_events` had **zero** rows; the only rows in `memory.db`/`learning.db` appeared after the **0-tool** correction turn (2 lesson rows + one `user.selfmodel.pending…` row whose payload is `{"pattern": "PersonalClaw + no-tools", "tools": [], "succeeded": true}`). Each live adapter carried **31 descendant processes** (the operator's MCP fleet — `gateway.log`: `Tracked 34 descendant PIDs`), 93 across three sessions |
| `C15` | `POST …/{S1}/fork`, then `POST …/{S2}/regenerate` | the fork (`chat-4-…`) carries all 14 messages with `acp_provider: ""` — the branch loses the runtime. The regenerated assistant message carries `variants` (2) and `variant_idx: 1` |
| `C16` | killed the gateway, restarted it with the same env, inspected every session, then sent one turn on S3 | **all three** sessions came back with `acp_provider: null`, `reasoning_effort: null`, `workspace_dir: null`, `mode: null` — while S3's `model` **survived** as `openai.gpt-5.4`. The turn on S3 resolved on the **native** axis and errored `no model provider resolves for use case 'chat'`; `Pool decision: key=dashboard:chat-3-… resume_sid=None model=None` — no `session/load` attempted despite `loadSession` being advertised (`C1`) |
| `C17` | S1 re-bound, then: (a) a write card resolved with `{action:"trust"}`; (b) a second write with the poller disabled; (c) `task_mode=ask` set, then a third write | (a) `trust-a.txt` created; (b) **no card at all** — instead a second `tool_call` frame with `"auto": true` (and `tool: "unknown"`, `kind: ""`), and `trust-b.txt` created → session trust is live; (c) `trust-c.txt` was **never created** and SEL logged `unknown | denied | {"reason": "task_mode:ask"}` → **session trust does not bypass task mode** |
| `C18` | S1: `sleep 45; echo DONE-AAP2` as one tool call, then `POST …/{S1}/stop` 40 s in; afterwards a turn asking the CLI to quote any context line containing "cancel"/"interrupted" | the stop returned `{"ok": true}` and emitted a `stop_event` (`state: stopping`), and one second later the turn ended with the error **`ACP prompt timed out`** rather than a cancellation notice. The follow-up turn could not resolve the preamble question: codex **declines to quote its context** ("I can't provide hidden context or instructions") |
| `C19` | after the sweep: `find ~/.personalclaw ~/.codex -type f -mmin -120`, `sqlite3 -readonly ~/.codex/memories_1.sqlite`, then removal + re-verification by session id | `~/.personalclaw`: **zero** files written (the escaped cwd `~/.personalclaw/workspace` was entered but nothing was created there — every probe used absolute paths inside the isolated home). `~/.codex`: 7 conversation transcripts (`sessions/2026/08/17/rollout-…-<sid>.jsonl`) and 7 `shell_snapshots/<sid>.*.sh` keyed to this sweep's ACP session ids, plus codex's own sqlite WAL/otel/tmp churn. `memories_1.sqlite` was touched but is **empty** (`jobs: 0`, `stage1_outputs: 0`) — no memory was written. All 14 attributable files were deleted and re-verified: a `find ~/.codex -name "*<sid>*"` over all 16 sweep session ids now returns **0** |

### 4a. Prompt-side context — codex column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Memory recall injection (turn-0 context) | WIRED | CONFIRMED | WIRED | `C4`, `C7`, `C12` — `Injected 10,403 / 15,569 / 11,169 chars of context (memory, lessons, history, episodic)` on each fresh session, and `C6` shows an injected framing line quoted back verbatim |
| Knowledge context (@-mention + picker `meta.knowledge`) | WIRED | NOT-EXERCISED | — | no `meta` payload was sent; the specific injector was never driven |
| Attachments/paste (extracted text prepended) | WIRED | NOT-EXERCISED | — | as above |
| @prompt expansion (+ typed vars, snippets) | WIRED | NOT-EXERCISED | — | as above |
| Skills index in context + `skill_invoke`/`skill_search` execution | PARTIAL | CONFIRMED | PARTIAL | `C4` — no `skill_invoke`/`skill_search` in the CLI's tool list, while SEL carries `skill_surface` / `surfaced` rows and `gateway.log` logs `Surfaced skills: task-and-project`: the index text goes in, the tools do not |
| Session-live skill drafts (`skill_remember`) | PARTIAL | CONFIRMED | PARTIAL | `C4` — no `skill_remember` in the tool list |
| Task-mode framing (Agent/Ask/Plan/Build suffix) | WIRED | CONFIRMED — presence only | the framing block IS injected; whether its value tracks the live mode was NOT separable | `C6` — the CLI quoted `## Task mode: Agent`, but on a session whose earlier turns ran in agent mode, so replayed history is an equally good explanation. `C7` (the fresh-session control) died on the tool denial before answering, and `C18` shows codex otherwise refuses to quote its context |
| Agent profile system prompt / voice layer | PARTIAL | NOT-EXERCISED | — | no agent profile with a distinctive `system_prompt` was bound |
| Project binding (context preamble + cwd) | WIRED | **DIVERGED** | the cwd half does not work | `C4` — `workspace_dir` was set to the scratch dir **before** binding and `pwd` inside the spawned CLI answered `~/.personalclaw/workspace`; `gateway.log` shows `cwd=…/.dev-home/scratch pool_cwd=/Volumes/workplace/personalclaw-workspace`, so neither the session's dir nor the pool's reaches the process. The preamble half was not separately measured (`C18`) |
| project_id → artifact stamping | ABSENT | CONFIRMED | ABSENT (stronger) | `C4` — `artifact_save` is not reachable at all, so there is nothing to stamp |
| Persona injection (Lumon theme) | WIRED | NOT-EXERCISED | — | the persona toggle was never enabled |
| Cancelled-turn preamble re-injection | WIRED | NOT-EXERCISED | — | attempted: `C18` cancelled a turn mid-tool, but codex declines to quote its context, so no verdict on the re-injection |
| Compressed thread-history bootstrap (new process) | WIRED | CONFIRMED | WIRED | every turn spawns a NEW adapter process (`Session created` + a new PID each turn) and continuity held across 10 turns on S1; `C6` shows prior-turn text replayed into a later turn |

### 4b. Approvals / permissions / safety — codex column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Interactive approval cards | WIRED | CONFIRMED | WIRED | `C5` (two cards in one turn, both resolvable), `C11` (a card rejected, the tool did not run) |
| trust_reads (effective-safe auto-approve) | PARTIAL | CONFIRMED | PARTIAL | `C4`/`C5`/`C10` — `pwd`, a file read and six `cat` calls auto-resolved with `risk: safe`; the classification is title-driven (the adapter titles an `exec_command` "Read file '…'"), but `C11` shows a compound command that hides a mutation behind a read is still classified `execute`/`destructive` |
| Trust (session) / YOLO (global) auto-approve | WIRED | CONFIRMED (session trust) | WIRED for session trust; YOLO not exercised | `C17` — after one `{action:"trust"}` the next write ran with no card, surfacing a `tool_call` frame with `"auto": true` |
| Per-agent approval floor ("Always allow") | WIRED | NOT-EXERCISED | — | no agent profile with `approval_mode: auto` was bound |
| Task-mode enforcement BEFORE approval (trust can't bypass) | PARTIAL | CONFIRMED | WIRED — and the bypass question is now closed | `C17` — with session trust ACTIVE, an ask-mode write was still denied (`reason: task_mode:ask`) and the file never appeared. Also `C6`, `C7`. Across the whole sweep no ACP tool executed without passing the host gate |
| Plan mode → native backend plan | WIRED | **DIVERGED** | ABSENT — plan is enforced only by the host gate | `C7` — plan set BEFORE a fresh session's first turn; the CLI still called `apply_patch` and never called `update_plan`. This is the shape the audit predicted for kiro, not for codex |
| Hard deny-list (`security.is_denied`) pre-execution | ABSENT | NOT-EXERCISED | — | no deny-listed command was driven on codex (the `rm` in `C5` reached a card rather than a pre-block, but `rm -f <file>` is not known to be on the list) |
| PreToolUse hooks blocking execution | PARTIAL | NOT-EXERCISED | — | no hook was installed during the sweep |
| PostToolUse / Stop / SessionStart / UserPromptSubmit / Error hooks | WIRED | NOT-EXERCISED | — | as above |
| SEL audit of every executed tool + effective risk | WIRED | CONFIRMED | WIRED, with two blind spots | `C5`/`C10`/`C17` — hash-chained `tool_invocation` rows with `tool_kind` and `metadata.risk` for every executed tool plus `approved`/`denied` decisions. Blind spot 1: every permission/decision row is named **`unknown`** (`C5`, `C17`). Blind spot 2: a CLI-side refusal is invisible — `C9`'s `request_user_input` failure produced no row at all |
| Unattended mode (strip interactive tools + fail-fast approvals, T5) | ABSENT | NOT-EXERCISED | — | same blocker as AAP-1: this isolated home has no model provider, so a loop fails on provider resolution (`C16`) before any ACP worker turn |
| Dry-run replay (T9 observe mode) | ABSENT | NOT-EXERCISED | — | no dashboard entry point to drive as-a-user |
| OS sandbox wrap of the agent process | WIRED | NOT-EXERCISED | — | sandbox mode left at its default; no confinement boundary probed |
| Isolated CLI config hardening (`PERSONALCLAW_CC_ISOLATE`) | WIRED (opt-in) | **DIVERGED** | there is NO equivalent for codex — not opt-in, absent | `PersonalClawApps/codex-agent/provider.py` states the bundle deliberately applies no config isolation, and the measured consequences are `C12` (all 12 of the operator's MCP servers live in-session), `C4` (the operator's skills/plugins loaded — the CLI said so in its own warning), `C14` (31 descendant processes per session) and `C19` (7 conversation transcripts + 7 shell snapshots written into the operator's real `~/.codex`) |

### 4c. Tools — codex column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Filesystem/shell tools (cwd-confined + extra_tool_roots) | PARTIAL | **DIVERGED** — worse | PARTIAL, and NOT cwd-confined | `C4` — the CLI's `exec_command`/`apply_patch` run in `~/.personalclaw/workspace` regardless of the session's `workspace_dir`, and reached arbitrary absolute paths under `/private/tmp` freely (`C5`, `C17`) |
| Full native tool registry (knowledge/tasks/loops/inbox/memory/artifacts/workflows/subagents/web/schedule) | UNKNOWN | CONFIRMED (§5 gap 1 predicted "likely absent") | **ABSENT** | `C4` — the CLI's 14 tools are all its own; `knowledge_search` NO, `task_create` NO, `notify` NO, no `personalclaw-core` MCP server |
| Tool disable prefs (PT3/UT4 per-tool + per-provider) | ABSENT | NOT-EXERCISED | — | no tool-disable pref was set |
| Per-turn tool retrieval + progressive disclosure (`tool_search`/`tool_schema`) | ABSENT | CONFIRMED | ABSENT | `C4` — the CLI enumerated only its OWN tools, including its own `tool_search_tool`; no host-injected retrieval tools |
| Failure breaker (warn@3/block@5/circuit@30) | ABSENT | CONFIRMED | ABSENT | `C10` — six consecutive failing tool calls in one turn, zero warn/block/circuit output |
| Structural loop detection (no-progress/ping-pong) | ABSENT | CONFIRMED | ABSENT | `C10` — six identically-shaped failures, no steering injection or abort |
| Typed tool-result meta (content_type/raw_ref/truncated/recovery_hints/ok) | ABSENT | CONFIRMED | ABSENT (empty, not fabricated) | `C5` — every `tool_result` carries `content_type: ""`, `raw_ref: ""`, `truncated: false`, `original_length: null`, `recovery_hints: []` |
| Structured tool-input rendering (dict → schema-driven fields) | ABSENT | CONFIRMED | ABSENT — and for codex the structured payload IS on the wire | `C5` — `input: null` on every frame while `input_preview` holds flattened text; `gateway.log` at DEBUG shows the wire frame carrying `content: [{type: "diff", oldText: None, newText: "HELLO\n", path: "…", _meta: {kind: "add"}}]` |
| File-change diff chips (write/edit before-after) | ABSENT | CONFIRMED | ABSENT — with the raw material fully structured, not merely inferable | same DEBUG frame as above: codex-acp sends a real diff object per edit, and zero `diff`/`file_change`/`old_string`/`new_string` keys reach the frontend |
| AskUserQuestion card | UNKNOWN | CONFIRMED (audit: fires only if the CLI exposes an identically-named tool) | **ABSENT** | `C9` — codex *has* a `request_user_input` tool, and it fails CLI-side ("unavailable in Default mode"); no card, no SEL row, `0 tool calls` counted |
| Subagents (`subagent_run` + completion inject-back) | UNKNOWN | CONFIRMED | **ABSENT** — precondition holds | `C4` — no `subagent_run` tool. `C14` — the `session_pid_<pid>.txt` file the inject-back depends on IS written for codex sessions |
| MCP tools (external servers) | PARTIAL | CONFIRMED | PARTIAL — and the subset is the OPERATOR'S | `C12` — 12 servers, all from the operator's real `~/.codex/config.toml`; nothing from PersonalClaw's `mcp.json` |
| Queue-steering mid-turn (#37) | ABSENT | CONFIRMED | ABSENT — the message queues instead | `C10` — a mid-turn send returned `{"queued": true}` and ran as its own turn afterwards; no steering injection into the live turn |

### 4d. Learning / memory — codex column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Preference-facet capture (every turn) | WIRED | CONFIRMED | WIRED | `C13` — `activity_event` kind `learned`: "Learned: never more" (the same poor extraction as claude, from the same sentence shape) |
| Correction→lesson review | WIRED | CONFIRMED | WIRED | `C13`, `C14` — "Learned: User correction to honor: …" plus a `per_turn|lesson` row in `learning.db staging` and two rows in `semantic_memory` (one superseding the facet) |
| Procedural-outcome capture (M5d tool-outcome drain) | ABSENT | CONFIRMED | ABSENT | `C14` — after turns of 6 and 3 tool calls, `memory_events` was empty; the only rows appeared after the **0-tool** correction turn, and the self-model row that did land records `"tools": []` |
| Skill-ladder review (4-tier, propose-only) | WIRED | NOT-EXERCISED | — | needs a model provider, which this isolated home lacks |
| Memory consolidation on session end | WIRED | NOT-EXERCISED | — | same reason |
| Incognito/restricted no-write guarantees | WIRED | NOT-EXERCISED | — | no incognito/restricted session was driven |

### 4e. Session / conversation mechanics — codex column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Variants / regenerate (‹n/N› switcher) | WIRED | CONFIRMED | WIRED | `C15` — the regenerated assistant message carries `variants` (2) and `variant_idx: 1` |
| Edit & resend, branch continuation (fork) | WIRED | CONFIRMED with a caveat | WIRED, but the branch loses the runtime | `C15` — the fork carries all 14 messages with `acp_provider: ""` |
| Queued messages (merge/pop + live bubbles) | WIRED | CONFIRMED | WIRED end-to-end | `C10` — `queue_push` (with `queue_id`) during the turn, then `queue_pop` → `chat_user_message` → the queued message ran as its own turn. (This closes one of AAP-1's residuals) |
| Empty-turn auto-retry | WIRED | NOT-EXERCISED | — | no empty turn occurred across 17 turns; not forceable as-a-user |
| Auto-nudge re-arm (loops) | WIRED | NOT-EXERCISED | — | loop-only; blocked by the missing model provider |
| Context-% accounting | PARTIAL (UNKNOWN which backends emit) | **DIVERGED** | the chip is EMITTED but always reports a fabricated `0%` | `C4`, `C5`, `C10` — a `context_usage` frame with `pct: 0.0` and `Turn complete: … context 0%` on **every** one of 17 turns, including turns carrying 15 KB of injected context |
| Compaction | WIRED (CLI-owned `/compact`) | **DIVERGED** | ABSENT via the host | `C8` — `/compact` errors `-32601`; nothing compacts |
| Slash commands (via `stream_command`) | WIRED (protocol `commands/execute`) | **DIVERGED** | ABSENT — no plain-prompt fallback | `C8` — byte-identical failure to claude's, from a different adapter and a different CLI: `_vendor.dev/commands/execute` is not a method either adapter implements, so this is a host-side defect, not one adapter's gap |
| Session resume across gateway restarts (`session/load`) | PARTIAL (falls to `session/new` + compressed history) | **DIVERGED** — worse | ABSENT, and the runtime silently changes | `C1` (the adapter advertises `loadSession`), `C16` — after a restart **every** session's `acp_provider`/`mode`/`workspace_dir`/`reasoning_effort` is cleared while the pinned `model` survives, `resume_sid=None` (no `session/load`), and the next turn resolves on the native axis |
| Warm pool / instant start | WIRED | CONFIRMED (pool present, cold on this run) | WIRED-but-cold | `gateway.log` — `pool_size=0 pool_qsize=0` on every `Pool decision`, and a fresh `Spawned codex-acp` per turn |
| Concurrent sessions on one process (P9) | ABSENT (dialect False) | CONFIRMED | ABSENT | `C14` — three concurrently-bound codex sessions hold three DIFFERENT adapter PIDs |
| Pipe-death auto-retry / re-queue | WIRED | NOT-EXERCISED | — | no adapter process was killed mid-turn |
| Model override per session (composer picker) | WIRED | **DIVERGED** | applied on the session's FIRST turn only | `C12` — `ACP model: openai.gpt-5.4` on turn 1; `C13` — `ACP model: auto (from agent config)` on turn 2 of the same session, while the activity line kept printing `openai.gpt-5.4`. The CLI cannot self-report its model, so honoring was never verifiable from its side |
| Reasoning effort per turn | WIRED | **DIVERGED** | the axis does not exist on codex, yet the host accepts a value | `C2` — discovery returns `supported_efforts: []`; `C12` — the bind still accepted, stored and echoed `reasoning_effort: "low"`, and the CLI reports its effort is "not exposed" |
| Agent/persona selection | ABSENT (no persona axis) | CONFIRMED | ABSENT — and no dead UI | `C2` — exactly one agent with `provider_agent: ""` |
| Discovered-agent ephemeral binding (chat picker → `POST …/acp-agent`) | WIRED | CONFIRMED | WIRED, and *ephemeral* is literal | `C3` (the bind round-trips), `C15`/`C16` (lost on a fork and on a restart) |
| Turn telemetry (event/tool counts, tokens, cost estimate) | WIRED | CONFIRMED | WIRED (counts), context-% fabricated | `C5`, `C10` — `Turn complete: 103 events, 3 tool calls, context 0%` |

### Mark counts (codex, the same 63 audit cells)

| mark | count |
|---|---|
| CONFIRMED (runtime matched the audit's prediction) | 33 |
| DIVERGED (runtime contradicted it) | 10 |
| NOT-EXERCISED (no runtime observation obtained; reasons below) | 20 |

All four of the audit's literal `UNKNOWN` cells are now definite for codex (full native registry → ABSENT,
AskUserQuestion → ABSENT, subagents → ABSENT, context-% → emitted-but-fabricated), as are the two the
plan called out for codex specifically (compaction → ABSENT, slash commands → ABSENT-and-erroring).

### Residual not-exercised cells (codex, and why)

1. **Needs a model provider in the isolated home** (4): unattended mode, auto-nudge re-arm,
   skill-ladder review, memory consolidation. A loop/cron run fails on
   `no model provider resolves for use case 'chat'|'background'` (`C16`) before any ACP worker
   turn, so a wedge/no-wedge verdict would measure the wrong thing.
2. **Needs a fixture that was not built** (10): knowledge @-mention, attachment/paste, @prompt
   expansion, agent-profile system prompt, per-agent approval floor, PreToolUse hooks, the other
   five hook kinds, tool-disable prefs, Lumon persona injection, incognito/restricted.
3. **Needs timing/failure injection that did not land** (2): empty-turn auto-retry, pipe-death
   auto-retry.
4. **No as-a-user entry point** (2): dry-run replay, OS sandbox confinement probe.
5. **Blocked by codex's refusal to disclose its own context** (1): cancelled-turn preamble
   re-injection — the cancel was performed (`C18`) but the re-injection could not be read back.
   This is why `G26` below is filed as a methodology gap.
6. **No deny-listed command was driven** (1): the hard deny-list cell.

### Gap inventory — severity-ranked (codex findings, continuing AAP-1's numbering)

- **`G46` §2.1 prong B seeds nothing, and would not need to** (`K54`) — `acp/config_seed.py` is 237
  lines of receipt-recorded, reversible, SEL-audited machinery whose only production caller runs
  `if agent_config_dir:`, and **no ACP bundle passes that argument** (0 hits in all three). Measured
  end to end: no seed log line, no `acp_seeds.json`, nothing in `~/.kiro/agents/`, and kiro's
  discovery returns the same 27 agents as `K2`. Its premise is also false — with no `~/.kiro/mcp.json`
  on the machine and no agent file naming us, a kiro session still enumerated the whole
  `personalclaw-core` surface, i.e. kiro honours protocol-passed `mcpServers` (`K51`). So the prong-B
  path is both inert and unnecessary; `AAP-4` owns the decision to wire it or delete it, and deleting
  it is the clean-break option. Severity **P2** (dead machinery a doc describes as landed).
- **`G48` An artifact saved through the protocol MCP surface loses BOTH its project and its session**
  (`K59`) — the CLI's `artifact_save` on a session bound to project `p-14b92d4c` persisted
  `project_id: ""` and a created-event `session_id: ""`. The `project_id → artifact stamping` row was
  already ABSENT, but for the wrong reason (`K4` thought the tool unreachable), and the session half is
  a second loss nobody had recorded. Severity **P2**: an agent-authored artifact cannot be traced to
  the work that produced it, which is what the stamping was for.
- **`G49` A spawned subagent resolves an EMPTY originating session, so nothing can inject back**
  (`K59`) — `subagent_run` now spawns from an ACP session, and the gateway logs
  `_spawn_session_resolver: rid=spawn:6c6039b3 agent_id=6c6039b3 info=True session=`. No
  `[Subagent completion event]` arrived in ~4 minutes. The bundled `subagent-orchestration` snippet
  promises the CLI *"results are injected back automatically … don't poll, just wait"*, so the agent is
  told to wait for something that cannot arrive. Severity **P1**: the prompt-level contract and the
  runtime disagree, and the failure mode is an agent waiting forever.
- **`G47` A model call cannot be attributed to its caller, so a background pass is unfalsifiable**
  (`K56`) — supersedes `G44`. `model_calls.jsonl` and `/api/models/telemetry` both key on
  `(use_case, query_class)`; the skill ladder's success path broadcasts a transient WS chip and logs
  nothing; its failure path is `logger.debug`. Consequence, measured: a ladder pass that timed out at
  exactly 60,010 ms was invisible at the default log level, and a ladder pass that completed cannot be
  distinguished from any other `background` caller. Two one-line fixes make the cell measurable — a
  caller/subsystem field on the ledger row, and an INFO line carrying the ladder's verdict. Severity
  **P2** (an expensive learning pass can be silently dead in production and no surface would say so).

**Cross-provider confirmations first.** Eleven of AAP-1's sixteen findings reproduce on codex with
a different adapter, a different CLI and a different vendor, which promotes them from
"claude-code behavior" to **host-side defects**: `G1` (cwd/home escape — `C4`), `G3` (no
`personalclaw-core` surface — `C4`), `G4` (slash commands error, no fallback — `C8`, byte-identical
error text), `G5` (a restart silently changes the runtime — `C16`, and here it clears *all*
sessions), `G6` (no host-side brake — `C10`), `G7` (no procedural capture — `C14`), `G8` (fabricated
`0%` — every turn), `G13` (a fork loses the binding — `C15`), `G14` (the activity line names the
adapter: `via acp:codex-acp` — `C4`), `G15` ("Session created" on every turn — `C5`), `G16` (the
identical `Learned: never more` extraction — `C13`). `G2` does **not** reproduce in the same shape;
it is worse, and is refiled as `G17`.

**P0 — safety**

- **`G17` codex has no config-isolation lever at all — `PERSONALCLAW_CC_ISOLATE` has no equivalent.**
  Where the claude bundle ships an opt-in hardening flag that `AAP-5` can promote to a default, the
  codex bundle applies none *by design* (`PersonalClawApps/codex-agent/provider.py` docstring:
  "Codex manages its own configuration and auth, so this bundle does **not** apply the
  `CLAUDE_CONFIG_DIR` isolation the Claude bundle needs"). Measured consequences of that default:
  all **12** of the operator's own MCP servers are live inside every host-managed ACP session
  (`C12`) — including write-capable internal ones — the operator's skills/plugins load (`C4`), each
  session drags **31 descendant processes** (`C14`), and the CLI writes conversation transcripts of
  host-driven turns into the operator's real `~/.codex/sessions/` plus per-session shell snapshots
  (`C19`). So `AAP-5`'s "make the isolated config the default for host-managed sessions" has
  **nothing to flip** on codex: the mechanism must be built (codex reads `CODEX_HOME`), and until it
  is, every codex ACP session inherits an unbounded, operator-specific tool surface that the host
  neither declares nor gates. Owner: agent app bundle + core seam, `AAP-5`.

**P1 — capability-dead**

- **`G18` The approval card cannot name the tool it is approving on codex.** Every `approval` frame
  and every SEL decision row reads `tool: "unknown"` with `tool_kind: ""` (`C5`, `C17`), so the
  card shows only a raw payload and the audit trail records `unknown | approved`. Root cause is
  host-side and exact: `src/personalclaw/acp/translate.py:263` takes `title` from the
  `session/request_permission` frame's `toolCall`, and codex-acp's permission payload is only
  `{toolCallId, kind, status}` (`gateway.log` DEBUG: `Permission toolCall payload: {'toolCallId':
  …, 'kind': 'edit', 'status': 'pending'}`) — the human title lives on the *preceding* `tool_call`
  frame. The same function already correlates the *input* across those two frames via the
  `tool_call_inputs` cache, so the title and the `kind` (which **is** present on codex's permission
  payload) are one cache lookup away. This also **corrects AAP-1's `G10`**, which concluded the
  `kind` "lives on the `tool_call` frame, not the permission frame" — true for claude, false for
  codex. Not fixed here: `acp/translate.py` is shared by all three providers and sits outside this
  atom's fence. Owner: core seam, `AAP-8`.
- **`G19` A task-mode denial kills the whole codex turn; a host rejection does not.** When ask or
  plan mode denies a tool, the codex turn produces **no `tool_result`**, ends with the assistant
  message `*Conversation interrupted*`, and the model never receives a denial it could react to
  (`C6`, `C7`, `C17`) — nor does the user get claude's explanatory "(Ask mode — only read-only
  tools run…)" line. A *rejected approval card* on the same provider is graceful: the tool gets a
  `tool_result`, the model apologises and the turn completes (`C11`). So the automatic policy path
  is the destructive one, and on codex a plan-mode session cannot even answer a question if it
  decides to touch a tool first. Owner: core seam, `AAP-6`/`AAP-9`.
- **`G20` The per-session model pin silently stops applying after the first turn.** `ACP model:
  openai.gpt-5.4` on turn 1, `ACP model: auto (from agent config)` on turn 2 of the same session
  (`C12`, `C13`), while the activity line keeps printing the pinned id — the user is told a model
  that is no longer in force, with no restart required to reach that state. Same family as `G5`'s
  binding loss but reachable in normal use. Owner: core seam, `AAP-7`.

**P2 — fidelity**

- **`G21` The reasoning-effort axis is a no-op on codex and the host does not know it.** Discovery
  returns `supported_efforts: []` (`C2`), yet a bind with `reasoning_effort: "low"` is accepted,
  persisted and echoed back (`C12`). §2.6's "grey the pill on kiro" fix must key off
  `supported_efforts` and therefore covers codex too — otherwise the composer offers a control the
  provider cannot honor.
- **`G22` The host discards a fully structured diff that codex already sends.** Each edit's
  `session/update` carries `content: [{type: "diff", oldText, newText, path, _meta: {kind: "add"}}]`
  (`gateway.log` DEBUG, `C6`), and the frontend receives `input: null`, a flattened text
  `input_preview`, and zero diff keys. AAP-1's `G9` proposed inferring chips *by kind*; on codex no
  inference is needed — the before/after text is on the wire. Owner `AAP-8`; this raises §2.5's
  ceiling from "chip" to "real diff".
- **`G23` CLI/adapter notices are rendered to the user as assistant prose and persisted as
  messages.** Every fresh codex session's first assistant chunk is "Warning: Skill descriptions
  were shortened to fit the 2% skills context budget…" (`C4`, `C7`), stored in
  `sessions/*.jsonl` as an assistant message — so it also feeds compressed history and the
  auto-title prompt. Owner: core seam, `AAP-8`.
- **`G24` A stop during a tool execution surfaces as an error, not a cancellation.**
  `POST …/{s}/stop` returned `{"ok": true}` and emitted `stop_event` `state: stopping`; one second
  later the turn ended with `ACP prompt timed out` (`C18`) — the user asked to stop and was shown a
  timeout failure. Adjacent to the cancel-legibility work already on this branch. Owner: core seam.
- **`G25` `request_user_input` exists on codex but is CLI-refused, invisibly.** The tool is in the
  advertised list and returns "`request_user_input is unavailable in Default mode`"; the host counts
  `0 tool calls` and writes **no** SEL row (`C9`). Mirror of `G11` on a different mechanism, and the
  reason the AskUserQuestion cell is ABSENT rather than merely unreachable. Belongs in the §2.7
  parity doc as a per-provider residual.

**P3 — cosmetic / legibility / methodology**

- **`G26` Prompt-side context cells cannot be validated by self-report on codex.** It declines —
  "I can't provide hidden context or instructions" (`C18`) — which is why the task-mode framing,
  persona and cancelled-turn-preamble cells could not be closed the way AAP-1 closed them on
  claude. Validating those cells for codex (and probably kiro) needs a host-side way to dump the
  assembled prompt for one turn (a debug endpoint or a `--dump-context` flag), and Phase 1's
  "zero UNKNOWN" bar depends on it. Owner: whoever runs `AAP-3`; file as a harness need, not a
  product feature.

**Negative results worth keeping** (so nobody re-chases them): the read/safe auto-approve is **not**
spoofable by hiding a mutation behind a read — `cat X && rm Y` is classified `execute`/`destructive`
and gated (`C11`); session **trust does not** bypass task mode (`C17`); the adapter's descendant
tracking **does** reap the MCP fleet — after the gateway was killed, zero adapters and no orphaned
young MCP processes remained, and the ~120 `builder-mcp` processes on this machine are the
operator's own pre-existing baseline, not a leak from the sweep (`C14`, `C19`); and codex wrote
**nothing** into the real `~/.personalclaw` despite running with its cwd inside it (`C19`).

### Incidental bugs fixed in-session (codex)

**None.** The two host-side defects this sweep localised precisely — `G18` (the permission frame's
title/kind are discarded in `src/personalclaw/acp/translate.py:263`) and `G19` (a task-mode denial
returns no `tool_result`) — live in `acp/translate.py` and the chat-runner gate path, which are
shared by all three ACP providers and outside this atom's fence. Per the plan's own rule
("anything structural waits for Phase 2 so fixes land against the full three-provider picture")
they are filed at their measured severity with the exact line and wire evidence instead of being
half-fixed here.

## Phase 1 results — kiro-cli verified matrix (atom `AAP-3`)

**Swept:** 2026-08-17/18 · **Adapter:** none — `kiro-cli acp` speaks ACP natively (native binary, no
npm adapter, so no `npx`/durable-install step and no adapter version to pin) · **CLI:** `kiro-cli`
2.18.1 at `~/.toolbox/bin/kiro-cli` · **Dialect:** the core `default` dialect (the
`kiro-cli-agent` bundle ships no protocol code — `provider.py` resolves the binary and nothing else)
· **Host:** this repo at AAP-2's tip (`aa2610dc`), isolated
`PERSONALCLAW_HOME=/private/tmp/aap3-wt/.dev-home`, gateway on `:10451`,
`PERSONALCLAW_AUTH_MODE=none`, `PERSONALCLAW_FIRST_PARTY_APPS_DIR` pointed at the apps clone,
`PERSONALCLAW_ACP_NO_PROVISION=1` (a no-op for a native binary, set for symmetry with `AAP-2`).

**Auth precondition — the plan's FIRST clause — verdict: FRESH. No cell below is `ENV`.** Checked
before any capability probe, three ways: (1) `~/.midway/cookie` mtime `2026-08-17 21:45 PDT`, i.e.
**32 minutes** old at sweep start (`date` → `22:17 PDT`) and far inside a Midway cookie's lifetime;
(2) `kiro-cli whoami` → `Logged in with IAM Identity Center (https://amzn.awsapps.com/start)`,
`golani@amazon.com`, profile `KiroProfile-us-east-1`; (3) a **live model call** —
`kiro-cli chat --no-interactive "Reply with exactly the word AUTHOK…"` returned `AUTHOK`
(`Credits: 0.19 • Time: 5s`). So every failure recorded below is a capability observation, not an
auth artefact. **Worth recording for the next sweep:** the on-disk
`~/.aws/sso/cache/kiro-auth-token.json` carries `expiresAt: 2026-07-10T19:55:50Z` — **five weeks
expired** — beside a live `refreshToken`, and `whoami` still resolves. So *that file's expiry is not
a freshness signal*; a future sweep that reads it and concludes "stale" would mis-file working cells
as `ENV`. Only a live call settles it.

**Method.** Same shape as `AAP-1`/`AAP-2`: the `kiro-cli-agent` bundle was installed from the
first-party apps dir into the isolated home (`POST /api/apps`), registering the `acp:kiro-cli`
ProviderEntry; dashboard chat sessions were bound to it
(`POST /api/chat/sessions/{s}/acp-agent`) and driven through the audit §6 checklist over the same
HTTP/SSE + WebSocket surfaces the dashboard uses — `POST /api/chat` for the turn (SSE), a
`/api/ws` capture for the `tool_call`/`tool_result`, `approval`, `context_usage`, `queue_*` and
telemetry frames SSE does not carry, and the persisted state under the isolated home. Every mark
names the command or artifact that produced it. **Per the brief's `G1` warning, cwd was asserted
from `pwd` INSIDE the spawned CLI, never from a host-side `cwd=` log line** — and note this host's
log level never emitted the `cwd=`/`pool_cwd=` lines `AAP-2` read, so the in-CLI assertion was the
only available evidence anyway.

**Marks.** Same vocabulary as `AAP-1`/`AAP-2`: `CONFIRMED` — the runtime matches the audit's
predicted verdict. `DIVERGED` — it does not; the observed verdict replaces the predicted one.
`NOT-EXERCISED` — no runtime observation was obtained, with the reason stated; reading the code and
reasoning that a cell *should* work is explicitly **not** a mark.

### Observation ledger (kiro-cli)

Ledger ids are `K…` so they never collide with `AAP-1`'s `O…` or `AAP-2`'s `C…`.
`S1` = `chat-1-1787030505` (`workspace_dir` set to `…/.dev-home/scratch` **before** binding).

| id | what was run | what was observed |
|---|---|---|
| `K1` | `POST /api/apps` with the first-party `kiro-cli-agent` dir, then `GET /api/agent-providers` | `acp:kiro-cli` registered, `ready: true`, `state: ready`, `detail: "initialize OK (caps: auth, loadSession, mcpCapabilities, promptCapabilities, sessionCapabilities)"`. `loadSession` **is** advertised (as on claude and codex); unlike codex there is no `providers` cap and unlike claude no `_meta` cap. Install scan: `verdict: clean`, `tier: community`, `signature: unsigned` |
| `K2` | `GET /api/agent-providers/acp:kiro-cli/agents` | **27 agents** — a real persona axis (claude had one, codex had none). Only **3** are kiro's own built-ins (`kiro_default`, `kiro_planner`, `kiro_guide`); the other **24 are the operator's private AIM/MeshClaw/kirocrew fleet** (`amzn-builder`, `atlas`, `cr-review-agent`, `oe-report-agent`, `offer-health-agent`, `meshclaw*`, `kirocrew*`, `meetings-*`, `oncall-triage`, `stores-builder`, `code-review-sage-reviewer`, …), each carrying its AIM description verbatim. `models` = **21** live-discovered ids (`auto`, `claude-opus-5`, …, `agi-nova-beta-1m`), identical on all 27. **`supported_efforts` = `[]`** on all 27 (claude advertised five; codex also `[]`). **`permission_modes` = `[]`** — where claude and codex both returned the host-side list `default, acceptEdits, plan, dontAsk, bypassPermissions`, kiro returns **nothing**. `cached: true` |
| `K3` | `POST …/{S1}/workspace-dir {"/private/tmp/aap3-wt/.dev-home/scratch"}`, then `POST …/{S1}/acp-agent {provider:"acp:kiro-cli"}`, then `GET /api/chat/sessions` | both round-trip: `workspace_dir` = the scratch dir, `acp_provider: "acp:kiro-cli"`, `provider_agent: ""`, `model: ""`, `reasoning_effort: ""` |
| `K4` | S1 turn 1: "Run exactly one shell command: `pwd` … then list your available tool names verbatim, then YES/NO for `knowledge_search`, `task_create`, `notify`" | `pwd` → **`~/.personalclaw/workspace`** — **the escape reproduces on the third provider**, with `workspace_dir` set to the scratch dir *before* binding. `knowledge_search` **NO**, `task_create` **NO**, `notify` **NO**; no `personalclaw-core` server. The tool list is **57 tools, and it is the operator's own MCP fleet flattened into the session** — including write-capable and financially consequential internal ones: `get_aws_creds`, `configure_aws_access`, `use_aws`, `submit_expense_report`, `create_expense_report`, `add_expense`, `delete_report`, `switch_delegate`, `list_delegates`, `upload_receipt` — beside kiro's own natives (`shell`, `read`, `write`, `glob`, `grep`, `subagent`, `knowledge`, `todo_list`, `goal`, `introspect`, `web_fetch`, `web_search`, `code`, `parallel`). Telemetry: `Injected 10,471 chars of context (memory, lessons, history, episodic)`, `Session created · default · auto · via acp:kiro-cli`, `Turn complete: 282 events, 1 tool calls, context 0%` with one `context_usage` frame `pct: 0.0` |
| `K5` | the `pwd` call's own frames, captured on `/api/ws` and resolved with `POST …/{S1}/approve {action:"approved"}` | **`pwd` raised an approval card** — `risk: "safe"`, `is_read_only: "1"`, and the turn **blocked** until approved (codex auto-resolved its reads and its six `cat` calls with no card). The reason is visible in the frames: kiro's adapter titles the call honestly, `"Running: pwd"`, so the host classifies it `kind: "execute"`; codex's adapter titles an `exec_command` `"Read file '…'"`, which the host classifies `kind: "read"` and auto-approves. **The `approval` frame here carries a real `tool` title and the real `tool_input`** (`{"command": "pwd", "__tool_use_purpose": …}`) — so `AAP-2`'s `G18` (`tool: "unknown"`) does **not** reproduce on kiro; the frame instead omits `tool_kind` entirely and blanks `tool_purpose` while the sibling `tool_call` frame carries both `kind: execute` and `purpose`. `tool_result` again carries `content_type: ""`, `raw_ref: ""`, `truncated: false`, `original_length: null`, `recovery_hints: []` |
| `K6` | the `personalclaw.json` unknown, both halves: (a) `ls $PERSONALCLAW_HOME/agents/` + read the file; (b) `kiro-cli agent list`; (c) the tool list from `K4` | **RESOLVED — the file is generated and is NOT honored.** (a) the host **does** write a complete, correct `agents/personalclaw.json` into the isolated home at startup — `name: "personalclaw"`, `tools`/`allowedTools` = `["@personalclaw-core"]`, `mcpServers.personalclaw-core` = `<venv>/personalclaw mcp-core`, plus a `prompt` file URL. Nothing about it is malformed. (b) `kiro-cli agent list` prints its own discovery roots verbatim — `Workspace: <cwd>/.kiro/agents` and `Global: ~/.kiro/agents` — and lists **24 agents, none named `personalclaw`** (the only "personalclaw" substring in the output is the workspace path of the shell that ran it). `$PERSONALCLAW_HOME/agents/` is neither root, so the file is never read. (c) hence `K4`'s three NOs. **Also found in that file:** its `hooks.postToolUse` command is `… >> ~/.personalclaw/audit.log` — a **literal tilde path to the REAL home**, not `$PERSONALCLAW_HOME`. So the file Phase 2 §2.1 Prong B plans to symlink into `~/.kiro/agents/` would, the moment it is honored, make every isolated-home ACP session append to the operator's real `~/.personalclaw/audit.log` |
| `K7` | the concurrent-sessions unknown: `PATCH /api/config/personalclaw {path:"agent.acp_concurrent_sessions", value:true}` (verified `true` in `config.json`), then S2 + S3 both bound to `acp:kiro-cli` and **both driven at once** (two overlapping `POST /api/chat` counting turns), then `pgrep -f 'toolbox/bin/kiro-cli acp'` mid-flight and after | **DIVERGED — no process is shared.** The gate is genuinely ON (an in-process check under the same home returns `flag=True`, `get_dialect('default').supports_concurrent_sessions=True`, `concurrent_sessions_enabled('default')=True`), and `AcpPool._shared_connection` keys its cached connection on `runtime_id` alone — yet three bound sessions ran on **three distinct `kiro-cli acp` process trees** (`2883` for S1, `17853` for S2, `19208`→ later `19719` for S3, all children of the gateway PID). The plan's step 12 prediction — "two kiro chats, one PID serving both" — does not hold. Each tree is a 5-process stack (`kiro-cli acp` → `aim sandbox --client kiro-cli acp` → the AIM `sandbox/launcher` → the app-bundle `kiro-cli acp` → `kiro-cli-chat acp`), so the cost of not sharing is 5 processes per chat, not 1 |
| `K8` | S2 turn 2 (`"Reply with exactly: SEQ2"`) immediately after `K7`, then re-count the trees | **the process IS reused across a session's own turns** — no new tree appeared (`2883 17853 19719` before and after) and the reply came back on the existing one. This **differs from codex**, where `AAP-2` measured a new adapter process on *every* turn. So kiro pools per session; it just never pools *across* sessions |
| `K9` | `ls $PERSONALCLAW_HOME/session_pid_*.txt` with three sessions live and three CLI trees running | **only ONE pid file exists** — `session_pid_2883.txt` → `dashboard:chat-1-1787030505` (S1). S2 and S3 have live processes and no pid file at all, where `AAP-2` measured one file per session on codex (three sessions → three files). §2.1's acceptance criterion names this file as the mechanism subagent inject-back resolves through ("correct session inject-back via the `session_pid_<pid>.txt` + env resolution"), so that precondition holds for S1 and is **absent** for the other two — and nothing in the UI distinguishes them |
| `K10` | the effort/model/persona axes: S2 and S3 were bound with `{provider_agent:"kiro_default", model:"claude-haiku-4.5", reasoning_effort:"low"}` against a provider whose `supported_efforts` is `[]` (`K2`) | the bind returned `{"ok": true, …, "reasoning_effort": "low"}` — **the host accepts and persists an effort value for a provider that advertises none, silently**. Nothing rejects it, nothing warns, and the session then runs normally. So on kiro the effort pill is a **silent no-op that round-trips**, which is the precise failure Success Criterion 7 forbids ("greyed, not no-op"); `supported_efforts: []` is already on the wire in the discovered-agent payload, so the UI has what it needs to grey the control and does not use it. The `model` half **does** take effect: both sessions answered on the pinned model and the activity line read `Session created · default · auto · via acp:kiro-cli` |
| `K11` | S2: sent the literal message `/compact` | **identical failure to claude and codex** — `Prompt error: {'code': -32601, 'message': 'Method not found', 'data': '_vendor.dev/commands/execute'}`, no plain-prompt fallback, no compaction. So `G4` now holds on **all three providers** and is settled as a host bug. One difference worth the fix's attention: kiro returns a *well-formed* JSON-RPC error object with the method in `data`, where the Zed adapters fold it into the `message` string — so the host has a machine-readable "method not supported" to key a fallback off, on at least one provider. SEL audited it: `operation: slash_command`, `tool_kind: slash`, `outcome: bypass`, `metadata.command: "/compact"` |
| `K12` | S1, task mode `agent`: "read `…/scratch/probe.txt`, create `…/scratch/written.txt` = `HELLO`, `rm …/scratch/doomed.txt`", `/api/ws` captured, every card resolved `approved` by a poller | all three ran and the state changed as asked (`written.txt` created, `doomed.txt` gone). **Every one of the three raised a card — including the READ.** codex auto-approved its reads; kiro does not, because kiro's adapter titles calls honestly (`Reading probe.txt:1`, `Running: pwd`) and the host's classifier lands them `read`/`execute` without the effective-safe auto-approve firing. The **edit card's `tool_input` is a real unified diff** (`--- … +++ … @@ -0,0 +1 @@ +HELLO`), so §2.5's diff chip has its source on kiro as well as codex. The `rm` card's input additionally carries `working_dir: "…/.dev-home/scratch"` — the correct dir, on a session whose `pwd` is the escaped real home (`K4`), so the two disagree inside one turn. **`AAP-2`'s `G18` does NOT reproduce:** kiro's cards carry the real tool title everywhere — `approval` frame, `pending_approval_info` and SEL all name the tool — never `unknown` |
| `K13` | the same turn's eight `tool_call` frames vs its `approval` frames vs its SEL rows | **`AAP-1`'s `G2` (contingent gate coverage) is now MEASURED, not hypothetical.** kiro's native `todo_list` tool fired four times in that turn — `Creating task list: …`, `Completing #1`, `#2`, `#3` — each producing a `tool_call` frame and a SEL `invoked` row, and **not one of them produced an `approval` frame, an `approval_resolved`, or any decision row**. They simply executed. The host classified each of those four `tool_kind: "unknown"` with **`risk: "destructive"`** — so four tool calls the host itself labelled destructive ran with **no gate at all**, in the same turn where three others were gated. `AAP-2` concluded "across the whole sweep no ACP tool executed without passing the host gate"; on kiro that is false, because the host can only gate what the CLI *chooses* to ask about and kiro's todo tool never asks. Separately, the read's two SEL rows **disagree with each other**: `invoked` says `risk: safe`, the matching `approved` says `risk: caution` |
| `K14` | S3 with `POST /api/chat/task-mode {mode:"ask"}` set before the turn, then "use your write tool to create `…/scratch/ask-mode-probe.txt`" | **the write was blocked and the file never appeared** — and kiro handles it far better than codex did. The user is told why, inline, in the tool line itself: `Creating ask-mode-probe.txt (Ask mode — only read-only tools run (switch to Agent to make changes))`. The turn then **ended normally** (`[DONE]`, `Turn complete: 9 events, 1 tool calls`), where the identical denial on codex produced no `tool_result` and killed the turn with `*Conversation interrupted*`. SEL logged `Creating ask-mode-probe.txt | denied | {"reason": "task_mode:ask"}` with the real operation name, where codex logged `unknown`. So **`AAP-2`'s `G19` is codex-specific, not a host defect** — the shared gate path can and does return a legible denial, which makes `G19` a smaller, better-bounded fix than it looked with one provider |
| `K15` | S1: six `cat /nonexistent-aap3-probe-N` commands as one tool call each, "do NOT stop early"; the FIRST card resolved with `{action:"trust"}`; 30 s in, a second `POST /api/chat` sent while the turn ran | **trust works, the brake does not, the queue does.** After the one `trust` resolution, probes 2-6 each surfaced a `tool_call` with `"auto": true` and **no card** — session trust is live on kiro. All six commands ran and failed → **no warn, no block, no circuit abort, no steering injection** (`Turn complete: 196 events, 13 tool calls, context 0%`), so `G6` reproduces on the third provider. The mid-turn message returned `{"ok": true, "queued": true}`, emitted `queue_push` with a `queue_id`, and after the turn a `queue_pop` + `chat_user_message` ran it as its own turn (`Turn complete: 9 events, 0 tool calls`) — queueing drains end-to-end, as on codex. The 13 calls break down as 1 task-list + 6 `cat` + 6 `Completing #N`, i.e. **7 of 13 were the ungated todo tool of `K13`** |
| `K16` | S2 turn: a correction ("No - stop doing that. Always answer me in exactly one sentence from now on, never more.") | two `learned` activity events: **`Learned: never more`** and `Learned: User correction to honor: …` — the identical bad extraction `AAP-1` and `AAP-2` measured, byte-for-byte. `G16` now holds on all three |
| `K17` | `sqlite3 memory.db`/`learning.db` after turns carrying 13, 8 and 3 tool calls | `memory_events` has **4 rows and every one of them is from the 0-tool correction turn** (`facet_veto` + `after_turn_review` lesson rows, then a `user.selfmodel.pending…` row whose payload is `{"pattern": "PersonalClaw + no-tools", "route": "PersonalClaw", "tools": [], "succeeded": true}`). The tool-heavy turns produced **zero** procedural rows, and the self-model row asserts `tools: []` for a session that had just made 13 tool calls. `G7` now holds on all three |
| `K18` | `POST …/{S1}/fork` | the fork (`chat-4-…`) carries `acp_provider: ""` — **the branch loses the runtime**, as on codex, so `G13` holds on all three. It *does* inherit `workspace_dir`. Separately: the fork reports `messages: 8` from a parent with **42** — codex's fork carried all of its parent's messages, so what a fork copies differs by more than the binding (mechanism not established; recorded as measured) |
| `K19` | killed the gateway with three sessions live and three CLI trees running, then `pgrep -f 'toolbox/bin/kiro-cli acp'` | **all three trees were reaped — zero leaked processes.** kiro's 5-deep stack (`kiro-cli` → `aim sandbox` → AIM `launcher` → app-bundle `kiro-cli` → `kiro-cli-chat`) goes away with the gateway, so `AAP-1`'s two-orphan leak does not reproduce here |
| `K20` | restarted the gateway with the same env, then inspected every session, then sent one turn | **all four sessions came back with `acp_provider: null`, `reasoning_effort: null`, `workspace_dir: null`, `mode: null` and (this sweep's addition) `task_mode: null`**, while `model` survived — the same shape `AAP-2` measured, so `G5` holds on all three. The turn then resolved on the **native** axis and errored `no model provider resolves for use case 'chat'`. No `session/load` was attempted despite `loadSession` being advertised at `initialize` (`K1`) |
| `K21` | after that restart: `POST …/{s}/acp-agent` on each of the three pre-restart sessions, and on a brand-new one | the new session bound fine; **all three pre-restart sessions returned `{"error": "not found"}`** — even though `GET /api/chat/sessions` lists them. They become re-bindable only after a `POST /api/chat` touches them, and that touch is exactly the call that hard-errors (`K20`). So the restart's repair path is: send a message, receive an unexplained provider error, *then* re-bind — nothing surfaces that sequence. (This is why `AAP-2` was able to "re-bind S1": it had already sent a turn on it.) |
| `K22` | S5 = a FRESH session, `task_mode=plan` set **before** its first turn, then "state your approval mode, then use your write tool to create `…/scratch/plan-mode-probe.txt`" | **CONFIRMED, exactly as the audit predicted for kiro:** no native plan mode — the CLI called its write tool — and the host gate blocked it (`Creating plan-mode-probe.txt (Plan mode — inspection only, nothing is executed (switch to Agent to run it))`), file never created. The reply also carried the host's own `[SWITCH_TO_AGENT: …]` marker (a real host convention — `dashboard/chat_utils.py:107-115` instructs it, `web/src/pages/ChatPage.tsx:3495` strips it), so kiro honours the plan-mode instruction protocol end to end |
| `K23` | S5: `POST /api/chat/task-mode {mode:"agent"}` (verified `task_mode: agent` on `GET /api/chat/sessions`), then two further turns asking for a shell command, then "quote verbatim every context line containing 'Task mode'" | **P1 — the session is wedged in plan mode.** Both post-switch turns refused ("I'm in PLAN mode and cannot execute commands"), and the context dump proves it is not the model replaying its own refusals: the injected block literally reads **`## Task mode: Plan`**, on a session the API reports as `agent`. Worse, that same block asserts *"This posture is current as of THIS turn and supersedes any mode an earlier turn in this conversation mentioned — if a previous reply refused because it was in a different mode, re-evaluate against the mode stated here and don't carry that refusal forward"* — so the host tells the model to trust a value the host is shipping stale, which is precisely why no amount of re-asking recovers. Note the host *gate* was correctly on `agent` the whole time, so the user is blocked by a stale prompt rather than by policy |
| `K24` | the control for `K23`: the same session key after a gateway restart (fresh CLI process), `task_mode` set to `agent`, then "quote the line starting `## Task mode:`" | `"## Task mode: Agent\nYou are in AGENT mode -- full execution…"` — **correct.** So the framing is assembled correctly per turn *when the process is new*, and `K23`'s staleness is caused by kiro's per-session process **reuse** (`K8`): the task-mode block is fixed when the CLI process/session is created and never refreshed for its later turns. This is the one place where kiro's efficiency advantage over codex (which respawns every turn, `AAP-2`) is what creates the defect — claude and codex cannot show this bug because they never reuse a process |
| `K25` | S2: "run exactly `git -C /nonexistent-aap3-repo push origin main`" (a path chosen so that execution, if it happened, could only fail harmlessly) | **the hard deny-list DOES cover ACP tools — better than the audit predicted (ABSENT).** The call was blocked pre-execution and the reason was surfaced to the user in the tool line: `Running: git -C … push origin main (blocked: Blocked by security policy: *git*push*)`, matching `BUILTIN_DENY_PATTERNS`' `*git*push*` against the adapter's *title*. SEL logged `invoked` then `denied`. One asymmetry: the `denied` row's `metadata` is **empty `{}`** — the pattern that the UI names is absent from the audit trail |
| `K26` | `request_user_input`/AskUserQuestion reachability, from `K4`'s verbatim tool list | **ABSENT** — kiro exposes no `request_user_input`-style tool at all (codex had one that then failed CLI-side). kiro does ship its own `subagent`, `knowledge`, `todo_list` and `goal` natives, none of which are PersonalClaw's, so the platform's `subagent_run` inject-back has no reachable entry point here either |

### 4a. Prompt-side context — kiro-cli column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Memory recall injection (turn-0 context) | WIRED | CONFIRMED | WIRED | `K4`, `K22` — `Injected 10,471 / 7,283 / 6,971 / 4,868 chars of context (memory, lessons, history, episodic)` on each fresh session |
| Knowledge context (@-mention + picker `meta.knowledge`) | WIRED | **CONFIRMED** (follow-up) | WIRED | `K30` — with `meta.knowledge=[<item id>]` the CLI quoted the stored item verbatim: `The AAP3 KIWI PROTOCOL states: the secret sweep codeword is ZANZIBAR-7719 …` |
| Attachments/paste (extracted text prepended) | WIRED | **CONFIRMED** (follow-up) | WIRED | `K30` — `meta.files=[…/uploads/aap3-brief.txt]`; the CLI quoted the extracted text verbatim: `ATTACHMENT-CONTENT-MARKER-B83: the attached briefing says the sweep vehicle is a hovercraft.` |
| @prompt expansion (+ typed vars, snippets) | WIRED | **CONFIRMED** (follow-up) | WIRED, but composer-side — nothing on the ACP path expands it | `K30`/`K31` — a message carrying the literal `@aap3-prompt` reached kiro **unexpanded** (it answered `ABSENT`), while `POST /api/prompts/aap3-prompt/render` returns the body and the composer is its caller. So the cell is provider-independent by construction |
| Skills index in context + `skill_invoke`/`skill_search` execution | PARTIAL | CONFIRMED | PARTIAL — **execution works**, the index half is still unmeasured | `K57` — `skill_search` was CALLED and answered (*"No skills matched"*), so `K4`'s "neither appears in the tool list" was an enumeration artifact. The index half was **not** exercised: unlike the codex sweep, no prompt in this one matched a skill, so `gateway.log` carries no `Surfaced skills:` line and SEL has no `skill_surface` row at all |
| Session-live skill drafts (`skill_remember`) | PARTIAL | CONFIRMED | PARTIAL — the tool is REACHABLE, a live draft was not driven | `K51`/`K57` — `skill_remember` is in the CLI's enumeration and its siblings execute; `K4`'s "absent from the tool list" was an enumeration artifact |
| Task-mode framing (Agent/Ask/Plan/Build suffix) | WIRED | **DIVERGED** | the block IS injected and its VALUE goes stale on a reused process | `K23` — the framing quoted verbatim reads `## Task mode: Plan` on a session the API reports as `task_mode: agent`; `K24` — the same session on a fresh process reads `## Task mode: Agent`. This is the cell `AAP-2` could not close on codex (`G26`); kiro closes it and it is broken |
| Agent profile system prompt / voice layer | PARTIAL | **CONFIRMED** (follow-up) | WIRED | `K30` — a PersonalClaw profile carrying a distinctive `system_prompt` + `voice` was bound; the CLI quoted `MANDATORY PROFILE MARKER: you must include the exact token PROFILE-MARKER-QX41 in every reply.` verbatim |
| Project binding (context preamble + cwd) | WIRED | **DIVERGED** | the cwd half does not work | `K4` — `workspace_dir` set to the scratch dir **before** binding, and `pwd` inside the spawned CLI answered `~/.personalclaw/workspace`. The preamble half was not separately measured. `K12` shows the same turn's `rm` call carrying the *correct* `working_dir`, so the two disagree inside one turn |
| project_id → artifact stamping | ABSENT | CONFIRMED | ABSENT — and now for the RIGHT reason | `K57`/`K59` — `artifact_save` **is** reachable and saved `AAP3-STAMP-PROBE` from a session bound to project `p-14b92d4c`; the persisted artifact carries `project_id: ""` and its created event carries `session_id: ""` (`G48`). `K4`'s "not reachable at all" was an enumeration artifact |
| Persona injection (Lumon theme) | WIRED | **CONFIRMED** (follow-up) | WIRED | `K30` — `color_theme=lumon` on the turn; the CLI quoted `Use a Lumon-inspired persona. Keep responses technically useful and clear first.` |
| Cancelled-turn preamble re-injection | WIRED | **CONFIRMED** (follow-up) | WIRED | `K35` — a `sleep 40` tool call was stopped mid-turn (the stop resolved `outcome: soft`), and the NEXT turn quoted `[PREVIOUS TURN WAS CANCELLED BY THE USER -- context restore]` plus its following line verbatim. This is the cell the first sweep could not close because `G29` had made its stop targets tool-free |
| Compressed thread-history bootstrap (new process) | WIRED | CONFIRMED | WIRED | continuity held across 42 messages / many turns on S1, and after the restart a **fresh** process on a 12-message session still answered in context with a fresh `Injected …` line (`K24`). Note the mechanism differs from the Zed adapters: kiro reuses one process per session (`K8`), so most turns need no re-bootstrap at all |

### 4b. Approvals / permissions / safety — kiro-cli column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Interactive approval cards | WIRED | CONFIRMED | WIRED | `K5` (a card raised, resolved, the tool then ran), `K12` (three cards in one turn, all resolvable) |
| trust_reads (effective-safe auto-approve) | PARTIAL | **DIVERGED** | it does not fire on kiro at all — reads are carded | `K5` — `pwd` arrived `risk: "safe"`, `is_read_only: "1"` and **still blocked on a card**; `K12` — a plain file read did too. The auto-approve is title-driven, and kiro's honest `Running: pwd` / `Reading probe.txt:1` titles land as `execute`/`read` without triggering it, where codex's mislabelled `Read file '…'` title for a shell `exec_command` did |
| Trust (session) / YOLO (global) auto-approve | WIRED | CONFIRMED (session trust) | WIRED for session trust; YOLO not exercised | `K15` — one `{action:"trust"}` and the next five tool calls surfaced `"auto": true` with no card; `trust: true` persists on the session |
| Per-agent approval floor ("Always allow") | WIRED | **CONFIRMED** (follow-up) | WIRED | `K36` — a profile with `approval_mode: auto`, with the auto-approver **off**: `echo AUTOFLOOR-OK` executed with no card at all (`pending_approval_info: null`, no `permission` frame) and the session came back `trust: true` |
| Task-mode enforcement BEFORE approval (trust can't bypass) | PARTIAL | CONFIRMED | WIRED | `K14` (ask-mode write denied, file never created, SEL `denied | task_mode:ask`), `K22` (plan-mode write denied). The *trust-can't-bypass* half was established on codex (`C17`) and not separately re-driven here, because kiro's trust and its ask-mode probes ran on different sessions |
| Plan mode → native backend plan | WIRED | CONFIRMED | ABSENT — enforced only by the host gate, exactly as the audit predicted **for kiro** | `K22` — plan set before a fresh session's first turn; the CLI called its write tool anyway, the host blocked it, and the reply carried the host's `[SWITCH_TO_AGENT: …]` marker |
| Hard deny-list (`security.is_denied`) pre-execution | ABSENT | **DIVERGED** | WIRED — it covers ACP tools | `K25` — `git … push` was blocked pre-execution with the pattern named to the user (`blocked: Blocked by security policy: *git*push*`) and a SEL `denied` row. The first positive result for this cell across all three sweeps |
| PreToolUse hooks blocking execution | PARTIAL | **DIVERGED** (follow-up) | it blocks ONLY when the session's agent profile references the hook | `K39` — one hook, `exit 2`, two outcomes: *unreferenced*, it fired three times and the write still landed (`hooked.txt` contained `HOOKED`); *referenced by the bound profile*, the tool line read `(hook blocked: aap3-pretool:hook denied)` and `hooked2.txt` was never created |
| PostToolUse / Stop / SessionStart / UserPromptSubmit / Error hooks | WIRED | **DIVERGED** (follow-up) | 3 of the 5 fire on the ACP path | `K40` — over 25+ turns: `SessionStart` 1, `UserPromptSubmit` 17, `Stop` 15 fired — while `PostToolUse` fired **zero** times and `Error` fired **zero** times despite two real ACP errors (`-32601` on `/compact`, `-32603` model-unavailable) |
| SEL audit of every executed tool + effective risk | WIRED | CONFIRMED | WIRED — and better named than on codex, with one internal contradiction | `K12`, `K13`, `K15` — hash-chained `tool_invocation` rows for **every** executed tool including the ungated ones, each carrying the real operation title (`AAP-2`'s "every row is named `unknown`" blind spot does **not** reproduce). The contradiction: one read produced `invoked | risk: safe` and `approved | risk: caution` for the same call |
| Unattended mode (strip interactive tools + fail-fast approvals, T5) | ABSENT | **DIVERGED** (follow-up) | WIRED — the host fail-fast denies instead of parking | `K41` — a `cron:`-keyed session with the auto-approver off: `Running: @personalclaw-core/get_context (auto-denied: unattended run, no one to approve)`, turn over in 5s, no card left pending, the requested file never created. `AAP-6`'s mechanism, measured as-a-user on kiro |
| Dry-run replay (T9 observe mode) | ABSENT | **CONFIRMED** (follow-up) | ABSENT — no user-reachable entry point exists | `K46` — surface census: no route matches dry-run/observe beyond session-cleanup's unrelated `dry_run` flag, no config field, and the `dry_run` constructor argument exists only on the native runtime, which an ACP session never builds |
| OS sandbox wrap of the agent process | WIRED | **ENV** (follow-up) | not determinable on this host | `K47` — the gateway logs at boot `No OS-level sandbox available — app-level checks only`, so there is no host wrap to probe here. Recorded as ENV, never as a capability verdict. (kiro still brings its own `aim sandbox` layer, which is not the host's mechanism) |
| Isolated CLI config hardening (`PERSONALCLAW_CC_ISOLATE`) | WIRED (opt-in) | **DIVERGED** | there is NO equivalent for kiro either — and its leak is an identity leak, not only a tool leak | `K2` (24 of 27 offered personas are the operator's private AIM/MeshClaw/kirocrew agents), `K4` (57 tools, all the operator's, including `get_aws_creds`, `use_aws`, `configure_aws_access` and the Concur expense-write set), `K7` (5 processes per session) |

### 4c. Tools — kiro-cli column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Filesystem/shell tools (cwd-confined + extra_tool_roots) | PARTIAL | **DIVERGED** — worse | PARTIAL, and NOT cwd-confined | `K4` — the CLI's own `shell`/`read`/`write` work, but its cwd is the real `~/.personalclaw/workspace`, not the session's `workspace_dir` |
| Full native tool registry (knowledge/tasks/loops/inbox/memory/artifacts/workflows/subagents/web/schedule) | UNKNOWN | **DIVERGED** (re-drive) | **REACHABLE** — over the protocol, not the config | `K51`/`K53` — the earlier ABSENT was a naming artifact: `K4` scored two strings (`knowledge_search`, `task_create`) that exist in no provider's registry. Re-measured against the CLI's own enumeration, `knowledge`, `todo_list`, `memory_*`, `artifact_*`, `workflow_*`, `subagent_*` and `notify` are all present, delivered by protocol-passed `mcpServers` (`K54`) |
| Tool disable prefs (PT3/UT4 per-tool + per-provider) | ABSENT | **CONFIRMED** (follow-up) | ABSENT — the only per-tool surface cannot address an ACP CLI's tools | `K45` — `POST /api/mcp/toggle-tool {"server":"personalclaw-core","tool":"get_context","enabled":false}` → `{"error": "server 'personalclaw-core' not found"}`. The pref keys on *configured* MCP servers; neither kiro's natives nor the protocol-injected `personalclaw-core` is one |
| Per-turn tool retrieval + progressive disclosure (`tool_search`/`tool_schema`) | ABSENT | CONFIRMED | ABSENT | `K4` — all 57 tools were enumerated up front; kiro ships no `tool_search`-style tool (codex did) |
| Failure breaker (warn@3/block@5/circuit@30) | ABSENT | CONFIRMED | ABSENT | `K15` — six consecutive failing tool calls in one turn, zero warn/block/abort |
| Structural loop detection (no-progress/ping-pong) | ABSENT | CONFIRMED | ABSENT | `K15` — six identically-shaped failures, no steering injection |
| Typed tool-result meta (content_type/raw_ref/truncated/recovery_hints/ok) | ABSENT | CONFIRMED | ABSENT (empty, not fabricated) | `K5`, `K12` — every `tool_result` carries `content_type: ""`, `raw_ref: ""`, `truncated: false`, `original_length: null`, `recovery_hints: []` |
| Structured tool-input rendering (dict → schema-driven fields) | ABSENT | CONFIRMED | ABSENT — and the structured payload IS on the wire | `K12` — `input_preview` carries real JSON (`command`, `working_dir`, `operations[].mode/path`, `__tool_use_purpose`) while `input` is `null`, so the host has the fields and drops them |
| File-change diff chips (write/edit before-after) | ABSENT | CONFIRMED | ABSENT — with the raw material already present | `K12` — the edit card's payload is a **unified diff** (`--- … +++ … @@ -0,0 +1 @@ +HELLO`), rendered as prose |
| AskUserQuestion card | UNKNOWN | CONFIRMED | **ABSENT** | `K26` — kiro exposes no `request_user_input`-style tool at all (codex had one that failed CLI-side) |
| Subagents (`subagent_run` + completion inject-back) | UNKNOWN | **DIVERGED** (re-drive) | **REACHABLE and it SPAWNS**; the inject-back half does not land | `K57`/`K58` — the tool is protocol-delivered, not absent: it first failed with `<urlopen error [Errno 61] Connection refused>` (the gateway-port defect this commit fixes) and then answered `Spawned 1 subagent(s) … 6c6039b3`. `K59` — no `[Subagent completion event]` arrived in ~4 min and the gateway logged `_spawn_session_resolver: … session=` (empty), which is the inject-back's precondition (`G49`). `K4`/`K26`'s "no `subagent_run`" was an enumeration artifact |
| MCP tools (external servers) | PARTIAL | CONFIRMED | PARTIAL — and the subset is the OPERATOR'S entire fleet | `K4` — 57 tools, none from PersonalClaw, all from the operator's real `~/.kiro` MCP configuration |
| Queue-steering mid-turn (#37) | ABSENT | CONFIRMED | ABSENT — the message queues instead | `K15` — a mid-turn send returned `{"queued": true}`, ran after the turn, and injected nothing into the running one |

### 4d. Learning / memory — kiro-cli column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Preference-facet capture (every turn) | WIRED | CONFIRMED | WIRED | `K16` — `activity_event` kind `learned`: `Learned: never more` (the same bad extraction as on both other providers) |
| Correction→lesson review | WIRED | CONFIRMED | WIRED | `K16`, `K17` — `Learned: User correction to honor: …` plus `facet_veto` + `after_turn_review` rows in `memory.db` |
| Procedural-outcome capture (M5d tool-outcome drain) | ABSENT | CONFIRMED | ABSENT | `K17` — after turns of 13, 8 and 3 tool calls, every `memory_events` row came from the **0-tool** correction turn, and the self-model row asserts `tools: []` |
| Skill-ladder review (4-tier, propose-only) | WIRED | NOT-EXERCISED (re-drive: still, new reason) | — | `K56` — the gate is drivable (a correction signal OR >=4 tool calls) and the ladder DOES run: with a 60 s provider timeout its pass dies as `provider_error` at 60,010 ms, logged only at DEBUG; with 900 s the background passes complete and `proposals` stays `[]`. What blocks the cell now is ATTRIBUTION — no surface ties a model call to its caller and the success path emits only a transient chip (`G47`), superseding `G44`'s "no forced-run surface" |
| Memory consolidation on session end | WIRED | **CONFIRMED** (follow-up) | WIRED | `K42` — `POST /api/memory/consolidate {"key": "dashboard_<session>"}` on a kiro session: `last_consolidated` 0 → 33, `semantic_memory` 3 → 5, `episodic_memories` 2 → 5. The per-turn cadence has a 30-message threshold (`src/personalclaw/history.py:38`), which is why thirteen short turns had not tripped it |
| Incognito/restricted no-write guarantees | WIRED | **CONFIRMED** (follow-up) | WIRED | `K33` — an `incognito` session ran the SAME correction turn that wrote three rows on a persistent session and wrote **zero** (`memory_events` 3→3, `semantic_memory` 3→3, `episodic` 0→0); the CLI itself knew ("for the rest of this incognito session"); the transcript persists with `memory_mode: incognito` by design, and the session is not restored after a gateway restart |

### 4e. Session / conversation mechanics — kiro-cli column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Variants / regenerate (‹n/N› switcher) | WIRED | **CONFIRMED** (follow-up) | WIRED | `K34` — `POST …/regenerate` on a kiro session produced a second answer and persisted BOTH: `variants` with two entries plus `variant_idx` on the assistant row. The re-answer still carried the injected knowledge and the profile marker |
| Edit & resend, branch continuation (fork) | WIRED | CONFIRMED with a caveat | WIRED, but the branch loses the runtime | `K18` — the fork carries `acp_provider: ""` and inherits `workspace_dir`; it also copied 8 of the parent's 42 messages, where codex's fork copied all of its parent's |
| Queued messages (merge/pop + live bubbles) | WIRED | CONFIRMED | WIRED end-to-end | `K15` — `queue_push` with a `queue_id` during the turn, then `queue_pop` + `chat_user_message` + its own `Turn complete` after it |
| Empty-turn auto-retry | WIRED | **CONFIRMED** (re-drive) | WIRED | `K55` — an empty turn IS producible as-a-user (a prompt demanding zero characters), so `K48`'s "needs stream injection" was wrong. The host re-queued the prompt silently once (the duplicated user row) and surfaced `Empty response — please retry.` on the second consecutive empty, exactly as `chat_runner:3623-3652` specifies |
| Auto-nudge re-arm (loops) | WIRED | **CONFIRMED** (follow-up) | WIRED | `K43` — `POST /api/autonudge {"idle_secs": 20, "max_cycles": 2}` on a kiro session: the nudge fired, the timer **re-armed**, fired again, and the loop deactivated at the cap (`cycle_count: 2, active: false`); the transcript carries both injected turns and kiro's `NUDGED` replies. Each nudge also fired the `UserPromptSubmit` hooks |
| Context-% accounting | PARTIAL (UNKNOWN which backends emit) | **DIVERGED** | the chip is EMITTED but always reports a fabricated `0%` | `K4`, `K12`, `K15` — `context_usage {pct: 0.0}` and `context 0%` on every turn, including one carrying 13 tool calls and 196 events |
| Compaction | WIRED (CLI-owned `/compact`) | **DIVERGED** | ABSENT via the host | `K11` — `/compact` errors `-32601`; nothing compacts |
| Slash commands (via `stream_command`) | WIRED (protocol `commands/execute`) | **DIVERGED** | ABSENT — no plain-prompt fallback | `K11` — the same `_vendor.dev/commands/execute` failure as claude and codex, though kiro returns it as a well-formed error with the method in `data` |
| Session resume across gateway restarts (`session/load`) | PARTIAL (falls to `session/new` + compressed history) | **DIVERGED** — worse | ABSENT, and unrecoverable without an undocumented sequence | `K20` — every session returns with `acp_provider: null` (+ effort, workspace_dir, mode and task_mode null) and the next turn errors on the native axis, with no `session/load` attempted despite `loadSession` advertised; `K21` — re-binding then 404s until a `POST /api/chat` touches the session |
| Warm pool / instant start | WIRED | CONFIRMED | WIRED, and demonstrably warm | `K8` — a second turn on the same session reused the live process (`pgrep` identical before and after) and answered immediately. The first runtime demonstration of a warm reuse across the three sweeps; codex was cold on every turn |
| Concurrent sessions on one process (P9) | WIRED for this dialect (`supports_concurrent_sessions = True`) | **DIVERGED** | ABSENT — nothing is shared | `K7` — flag on, gate returns `True`, `_shared_connection` keys on `runtime_id` alone, and three bound sessions still ran on three separate 5-process trees |
| Pipe-death auto-retry / re-queue | WIRED | **DIVERGED** (follow-up) | no retry — the turn dies with a timeout error | `K38` — `kill -9` on the session's `kiro-cli acp` tree mid-turn: the stream ended `ACP prompt timed out`, nothing was retried or re-queued, and no replacement process appeared for that turn. The NEXT turn respawned transparently (fresh tree, answer in 2.5s) — but its `session_pid_<pid>.txt` still names the **dead** pid |
| Model override per session (composer picker) | WIRED | CONFIRMED | WIRED, and legible | `K10` — the bind echoed `claude-haiku-4.5`, the activity line named it (`Session created · kiro_default · claude-haiku-4.5 · via acp:kiro-cli`) and both sessions answered on it. codex's `G20` (the pin lapsing after turn 1) could **not** be re-tested here: kiro does not self-report its model id, so per-turn re-application is unobservable from the CLI side |
| Reasoning effort per turn | WIRED | **DIVERGED** | the axis does not exist on kiro, yet the host accepts, persists and echoes a value | `K2` — `supported_efforts: []` on all 27 agents; `K10` — a bind with `reasoning_effort: "low"` returns `{"ok": true, …, "reasoning_effort": "low"}`. This is `AAP-2`'s `G21` measured on the provider §2.6 named it for |
| Agent/persona selection | WIRED (kiro has agents) | CONFIRMED | WIRED — and the axis is populated from the operator's private fleet | `K2` — 27 agents, only 3 of them kiro's built-ins; `K10` — binding `kiro_default` round-trips and the activity line names it |
| Discovered-agent ephemeral binding (chat picker → `POST …/acp-agent`) | WIRED | CONFIRMED | WIRED, and *ephemeral* is literal — worse than on codex | `K3` (the bind round-trips), `K20` (a restart clears it), `K21` (and you cannot simply set it again) |
| Turn telemetry (event/tool counts, tokens, cost estimate) | WIRED | CONFIRMED | WIRED (counts), context-% fabricated | `K4`, `K15` — `Turn complete: 282 events, 1 tool calls, context 0%` / `196 events, 13 tool calls, context 0%` |

### Mark counts (kiro-cli, the same 63 audit cells)

| mark | first sweep | after the follow-up sweep | after the 2026-08-19 residual re-drive |
|---|---|---|---|
| CONFIRMED (runtime matched the audit's prediction) | 31 | 44 | **43** |
| DIVERGED (runtime contradicted it) | 12 | 16 | **18** |
| ENV (environment limit, never a capability verdict) | 0 | 1 | **1** |
| NOT-EXERCISED (no runtime observation obtained; reasons below) | 20 | 2 | **1** |

Every column is counted from the rows above rather than carried in prose. The follow-up sweep resolved
**18 of the 20** first-sweep NOT-EXERCISED cells; the residual re-drive then resolved one of the two
survivors and moved one CONFIRMED cell to DIVERGED:

* **empty-turn auto-retry → CONFIRMED** (`K55`). It was called unreachable-without-stream-injection;
  a prompt demanding zero characters produces one, so that judgment was simply wrong.
* **full native tool registry → DIVERGED** (`K53`). Its ABSENT verdict came from scoring two tool
  names that exist in no registry; the capabilities are present and protocol-delivered.
* **skill-ladder review → still NOT-EXERCISED**, but `G44`'s reason is superseded by `G47` (`K56`):
  the gate is drivable and the ladder runs — what is missing is caller attribution for a model call.
* **subagents → DIVERGED** (`K57`-`K59`). `subagent_run` is not absent, it is protocol-delivered; it
  spawns once the gateway-port defect this commit fixes is out of the way, and only the inject-back
  half is missing (`G49`).

**One root cause explains four of those rows.** `K4` enumerated the CLI's tools without the
protocol-delivered `personalclaw-core` surface, and four verdicts were then built on that
enumeration — the native registry, the two skills rows and subagents. `K51`'s 151-name census plus
`K57`'s four live calls replace all four. A capability row must be scored against a censused name
list and, where the row claims a capability rather than a listing, against an actual CALL.

All four of the audit's literal `UNKNOWN` cells are now definite for kiro (full native registry →
ABSENT, AskUserQuestion → ABSENT, subagents → ABSENT, context-% → emitted-but-fabricated), as are
the two the plan flagged for kiro specifically (compaction → ABSENT, slash commands →
ABSENT-and-erroring) **and the three unknowns this atom was scoped to close**:
`personalclaw.json` discovery → **NOT honored** (`K6`), the effort pill → **a silent no-op that
round-trips** (`K10`), concurrent sessions → **declared and absent** (`K7`).

### Residual not-exercised cells (kiro-cli, and why)

**First sweep (20 cells) — all but two are now closed; kept for provenance.** The groups were:
needs a model provider (4: unattended mode, auto-nudge re-arm, skill-ladder review, memory
consolidation); needs a fixture that was not built (10: knowledge @-mention, attachment/paste,
@prompt expansion, agent-profile system prompt, per-agent approval floor, PreToolUse hooks, the
other five hook kinds, tool-disable prefs, Lumon persona, incognito/restricted); needs
timing/failure injection (2: empty-turn auto-retry, pipe-death auto-retry); no as-a-user entry point
(2: dry-run replay, OS sandbox probe); blocked by `G29` (1: cancelled-turn preamble); deliberately
not re-driven (1: variants/regenerate).

**After the 2026-08-19 residual re-drive, exactly ONE cell carries no runtime observation:**

1. **Skill-ladder review (4-tier, propose-only)** — and the reason has changed. `G44` said "no
   forced-run surface"; the gate is in fact a documented pair of conditions, both drivable as-a-user
   (`LearningGate._worthwhile(PER_TURN)`: a correction signal **or** `tool_calls >= 4`). Driven, the
   ladder RUNS: with the ollama bundle's 60 s default read timeout its pass dies as a
   `provider_error` at 60,010 ms visible only in `model_calls.jsonl` and one DEBUG line; with
   `timeout_secs=900` the background passes complete and `proposals` stays `[]` (`K56`). The cell is
   still unexercised because **nothing attributes a model call to its caller** — the ledger and
   `/api/models/telemetry` both key on `use_case`, and the ladder's success path emits only a
   transient WS chip — so "the ladder declined" and "some other background pass ran" remain
   indistinguishable from outside. Filed as `G47`, which supersedes `G44`: the fix is one attribution
   field or one INFO line, not a new surface.

~~2. **Empty-turn auto-retry**~~ — **CLOSED as CONFIRMED** (`K55`). The claim that producing an empty
   turn "requires stream injection" was wrong: a prompt demanding zero characters produces one, the
   host silently re-queues once, and the second consecutive empty raises the card. A cell called
   unreachable by construction stayed shut for three sweeps because nobody tried the cheapest input.

One further cell is `ENV` rather than a verdict: **OS sandbox wrap** — the host itself reports
`No OS-level sandbox available — app-level checks only` on this platform, so there is nothing to
confine and nothing to probe (`K47`).

### Gap inventory — severity-ranked (kiro-cli findings, continuing `AAP-2`'s numbering)

**Cross-provider confirmations first.** Ten of `AAP-1`'s sixteen findings reproduce on a provider
with **no adapter at all** (kiro speaks ACP natively), a third vendor and a third auth model, which
settles them as **host-side defects** rather than adapter or CLI behavior: `G1` (cwd/home escape —
`K4`), `G3` (no `personalclaw-core` surface — `K4`, `K6`), `G4` (slash commands error with no
fallback — `K11`, and now on a provider that reports the failure as a *structured* JSON-RPC error,
so a fallback is keyable), `G5` (a restart silently changes the runtime — `K20`, and here it also
clears `task_mode`), `G6` (no host-side brake — `K15`), `G7` (no procedural capture — `K17`), `G8`
(fabricated `0%` — every turn), `G13` (a fork loses the binding — `K18`), `G15` ("Session created"
on every turn — `K8` plus the per-session counts, and on kiro the line is not merely noisy but
**false**: S1 claimed it four times on a process that was created once), `G16` (the identical
`Learned: never more` extraction — `K16`). `AAP-2`'s `G17` (no config-isolation lever) also
reproduces, in a distinct and worse shape filed below as `G28`.

**Three of the prior sweeps' findings do NOT reproduce, which narrows their fixes:** `G18` (the
permission frame's title/kind discarded — kiro's cards, `pending_approval_info` and SEL rows all
carry the real tool title, never `unknown`); `G19` (a task-mode denial killing the turn — on kiro
the denial is graceful, legible and the turn completes normally, `K14`, so `G19` is
codex-specific); and `G26` (a provider that will not quote its own context — kiro quotes it
verbatim, `K23`, which is precisely how this sweep proved `G29`). `G14` is not a defect here: the
activity line's `via acp:kiro-cli` names the provider, and for kiro that *is* the CLI. And `G2`
(contingent gate coverage) does more than reproduce — it is finally **measured**, as `G27`.

**Status changes from the follow-up sweep (2026-08-18, host `252c944f` = this column's tip plus
`AAP-4`/`AAP-5`/`AAP-6`) — read this before sequencing Phase 2.** `G3` (no `personalclaw-core`
surface) is **closed**: kiro spawned and called the server from the protocol field with no seeded
config (`K32`). `G33` (a pid file for only some sessions) **does not reproduce** — four sessions,
four files (`K37`) — but `G42` replaces it with a staleness window. `G1` is **half fixed**: gone on a
directly bound session (`K28`), alive on an agent-profile-bound one, re-filed at P0 as `G39`
(`K50`). Still reproducing unchanged: `G4` (`/compact` → `-32601`, four sweeps now), `G5` (a restart
nulls `acp_provider`, `workspace_dir`, `task_mode` and `trust`), `G30` (a pre-restart session cannot
be re-bound — `{"error": "not found"}`), `G16` (and worse: the extractor recorded an *injected
knowledge block* as "User correction to honor…", `K49`). `G27`'s ungated-tool residue was addressed
by `AAP-5` after this column was written, so this sweep did not re-measure it.

**P0 — safety**

- **`G27` The host approval gate is provably not universal: seven of thirteen tool calls in one
  turn executed with no permission request, and the host itself labelled each of them
  `risk: "destructive"`.** kiro's native `todo_list` tool (`Creating task list: …`,
  `Completing #1/#2/#3`) produces a `tool_call` frame and a SEL `invoked` row and **never** a
  `session/request_permission`, so nothing gates it — in the same turns where the read, the write
  and the `rm` each raised a card (`K13`, `K15`). `AAP-1` filed `G2` as a *contingent* risk and
  `AAP-2` concluded "across the whole sweep no ACP tool executed without passing the host gate";
  on kiro that conclusion is false. The severity is structural rather than about `todo_list`
  specifically: host safety on ACP is opt-in **by the CLI**, so every provider's ungated tool set
  is whatever that CLI chooses not to ask about, and the host cannot enumerate it. Combined with
  `G28`'s tool surface, the ungated set on this machine is drawn from the operator's own MCP fleet.
  Success Criterion 3's "never silently executed" is not met. Owner: core seam — and it needs a
  *positive* mechanism (deny-by-default for un-permissioned `tool_call`s, or an explicit
  per-provider enumeration of the not-gateable set) rather than another gate on the asking path.
- **`G39` (follow-up sweep) `G1`'s real-home escape is fixed on the plain path and ALIVE on the
  agent-profile path.** Two sessions, same host, same isolated home, both with `workspace_dir`
  explicitly set to a temp dir and confirmed set on `GET /api/chat/sessions`: bound directly to
  `acp:kiro-cli`, the CLI's own `pwd` answers the temp dir (`K28`) — the first sweep's escape (`K4`)
  is gone. Bind a PersonalClaw **agent profile** to the session and the same `pwd`, asserted inside
  the spawned CLI, answers **`~/.personalclaw/workspace`** — the operator's real home
  (`K50`). The profile carried no `default_dir`, so the empty value wins over the session's explicit
  one instead of falling through to it. Severity stays P0 because the consequence is unchanged from
  `G1`: an isolated-home session writes into the real home, and every agent-profile-bound ACP
  session on any provider takes this path. It is also the shape a fix can miss — a sweep that only
  drives the plain binding now measures the escape as gone. Owner: core seam.
- **`G28` kiro has no config-isolation lever either, and its leak is an identity leak on top of a
  tool leak.** Like codex (`G17`) the bundle applies no config isolation — but where codex leaked
  the operator's 12 MCP servers, kiro leaks (a) **24 of the 27 personas the host offers in its
  agent picker**, which are the operator's private AIM/MeshClaw/kirocrew fleet complete with their
  internal descriptions (`K2`), and (b) a **57-tool** surface that includes `get_aws_creds`,
  `configure_aws_access`, `use_aws` and the entire Concur expense-write set
  (`submit_expense_report`, `create_expense_report`, `delete_report`, `switch_delegate`) (`K4`).
  Every host-managed kiro session can therefore mint cloud credentials and submit expense reports,
  and per `G27` the host cannot guarantee it will even be asked. kiro reads `AWS_*`/`KIRO_*`
  environment and its own `~/.kiro` tree, so — as with codex — `AAP-5` has nothing to flip and the
  mechanism must be built. Owner: agent app bundle + core seam.

**P1 — capability-dead**

- **`G29` A kiro session that has ever been in plan mode is permanently wedged, and the stale
  framing asserts its own freshness.** The task-mode block is fixed when the CLI process is created
  and never refreshed for that process's later turns, so after `POST /api/chat/task-mode
  {mode:"agent"}` the API reports `task_mode: agent`, the host gate *enforces* agent, and the model
  still receives `## Task mode: Plan` and refuses every tool request (`K23`). The injected text
  makes recovery impossible by instruction: it tells the model *"This posture is current as of THIS
  turn … if a previous reply refused because it was in a different mode, re-evaluate against the
  mode stated here and don't carry that refusal forward"* — so the model correctly trusts the wrong
  value and no amount of re-asking helps. The control (`K24`: the same session on a fresh process
  reads `## Task mode: Agent`) locates the bug precisely in per-turn re-injection for a **reused**
  process. This is reachable by a single UI mode toggle, the user's only escape is a gateway
  restart, and it is invisible to claude and codex because they respawn every turn — i.e. kiro's
  one efficiency advantage (`K8`) is what exposes it. Owner: core seam.
- **`G30` After a gateway restart a persisted ACP session cannot be re-bound.** `POST
  …/{session}/acp-agent` returns `{"error": "not found"}` for every pre-restart session while `GET
  /api/chat/sessions` lists them; the session becomes re-bindable only after a `POST /api/chat`
  touches it, and that touch is exactly the call that hard-errors on the native axis because the
  binding is gone (`K21`, `K20`). So the repair sequence is "send a message, receive an unexplained
  provider error, then re-bind", and nothing surfaces it. This is why `AAP-2` was able to re-bind
  after its restart — it had already sent a turn. `G5` loses the binding; `G30` is why the user
  cannot simply put it back. Owner: core seam.
- **`G31` `personalclaw.json` is generated correctly and stored where kiro never looks — and the
  planned fix would make isolated sessions write to the real home.** The host writes a complete
  `$PERSONALCLAW_HOME/agents/personalclaw.json` (`tools`/`allowedTools` = `["@personalclaw-core"]`,
  `mcpServers.personalclaw-core` = `<venv>/personalclaw mcp-core`); kiro's only agent roots are
  `<cwd>/.kiro/agents` and `~/.kiro/agents`, which it prints in `kiro-cli agent list`, and
  `$PERSONALCLAW_HOME/agents/` is neither, so `personalclaw` never appears among its 24 discovered
  agents (`K6`). This closes the audit's "kiro's discovery … is unverified" as **not honored** and
  confirms §2.1 Prong B is required. **But Prong B as written ("symlink or copy … into
  `~/.kiro/agents/`") would activate a real-home write:** that file's `hooks.postToolUse` command is
  `… >> ~/.personalclaw/audit.log` with a **literal tilde**, not `$PERSONALCLAW_HOME`, so the moment
  the file is honored every isolated-home ACP session appends to the operator's real home. Fix the
  path in the generator in the same change. Owner: core seam (generator) + agent app bundle
  (placement).
- **`G32` Concurrent sessions are declared, gated on, and do not happen.** `agent.acp_concurrent_
  sessions` was set true and verified in `config.json`, `default` dialect declares
  `supports_concurrent_sessions = True`, an in-process check returns
  `concurrent_sessions_enabled('default') == True`, and `AcpPool._shared_connection` caches its
  connection on `runtime_id` alone — yet three bound sessions ran on three separate CLI trees
  (`K7`). Since kiro's tree is five processes deep (`kiro-cli` → `aim sandbox` → AIM `launcher` →
  app-bundle `kiro-cli` → `kiro-cli-chat`), the cost of the miss is 5 processes per chat, and the
  P9 spike that set `supports_concurrent_sessions = True` is the only evidence the path ever ran.
  Either the dashboard chat path never reaches `_open_shared_acp_session` or it fails into its
  silent `except Exception: return None` — both shapes are invisible at every surface a user or
  operator can see. Owner: core seam; needs a log line on the fallback before anything else.
- **`G33` `session_pid_<pid>.txt` is written for only some sessions, so subagent inject-back
  silently holds for some chats and not others.** Three sessions with three live CLI processes
  produced **one** pid file (`K9`), where codex produced one per session. §2.1's acceptance
  criterion names this file as the resolution mechanism for subagent completion inject-back, so
  that criterion would pass or fail per-chat with nothing distinguishing them in the UI. Owner:
  core seam. **Follow-up sweep: does NOT reproduce** — four live sessions produced four pid files,
  one each (`K37`). What replaced it is narrower and filed as `G42`: the file survives its process.
- **`G40` (follow-up sweep) A PreToolUse hook blocks or is ignored depending on which agent
  references it — same hook, same exit code, no surface says which you have.** With six lifecycle
  hooks installed through the supported `POST /api/triggers` path, a `PreToolUse` hook exiting 2
  fired three times and the write still landed while no agent profile referenced it; once the hook
  ids were bound to the session's agent profile, the identical hook blocked the tool and the tool
  line named it (`hook blocked: aap3-pretool:hook denied`) (`K39`). Both behaviours are deliberate
  in isolation — `chat_runner._fire` is agent-scoped ("there is no global firing path") and
  `fire_tool_hooks` is documented as informational — but together they mean a user who installs a
  blocking safety hook and does not also attach it to the agent they are chatting with gets a hook
  that *runs*, *logs*, and *does not block*. That is a safety control with two indistinguishable
  states. Owner: core seam (make the non-blocking path say so, or make installation attach it).

**P2 — fidelity**

- **`G34` The read/safe auto-approve is decided by adapter prose, so the honest provider is the one
  that gets carded.** kiro's `pwd` arrives with `risk: "safe"` and `is_read_only: "1"` and still
  blocks on a card, because the classifier keys off the adapter's title and kiro titles it
  `Running: pwd` → `kind: execute` (`K5`); codex's identical shell `exec_command` titled `Read file
  '…'` → `kind: read` and auto-approved (`AAP-2`'s `C5`, `C10`). One provider's users approve every
  `ls`; the other's shell calls sail through on a mislabel. The classification input should be the
  protocol's own read-only hint, which kiro already sends and the host ignores. Owner: core seam.
- **`G35` One tool call produces two SEL rows that disagree about its risk.** The same read logged
  `invoked | {"risk": "safe"}` and `approved | {"reason": "interactive", "risk": "caution"}` (`K13`),
  so the audit trail contains two different effective risks for one event and any consumer that
  aggregates by risk is wrong depending on which row it reads. Owner: core seam.
- **`G36` The deny-list's reason reaches the user and not the audit log.** The blocked `git push`
  showed `blocked: Blocked by security policy: *git*push*` in the tool line, while its SEL `denied`
  row carries `metadata: {}` — the pattern that fired is absent from the only durable record
  (`K25`). Compare the task-mode denial, which *does* record `{"reason": "task_mode:ask"}`. Owner:
  core seam.
- **`G37` A card is two different shapes depending on which surface reads it.** The `approval`
  frame carries `id` + `risk` and no `tool_kind`; `pending_approval_info` on `GET
  /api/chat/sessions` carries `request_id` + `tool_kind` and **no `risk`** (`K5`, `K12`). So a
  REST-polling consumer cannot show risk, a WS consumer cannot show kind, and the id field is named
  differently in each — a client that resolves cards by `id` from the REST shape sends an empty
  string. Owner: core seam; one payload, both surfaces.
- **`AAP-2`'s `G21` is confirmed on the provider §2.6 named it for.** `supported_efforts: []` on all
  27 kiro agents, and a bind with `reasoning_effort: "low"` is accepted, persisted and echoed back
  (`K2`, `K10`) — so "grey the pill on kiro" has its measurement, and keying the fix off
  `supported_efforts` covers both providers as `G21` predicted. No new number.
- **`G41` (follow-up sweep) Two of the six script-hook kinds never fire on the ACP path — including
  `Error`, with real errors available to fire it.** Over 25+ turns with all six kinds installed and
  attached: `SessionStart` 1, `UserPromptSubmit` 17, `Stop` 15, and `PostToolUse` **0**, `Error`
  **0** (`K40`). The Error miss is measured against two genuine failures in the same sessions — the
  `-32601` `/compact` error and a vendor `-32603 MODEL_TEMPORARILY_UNAVAILABLE`. `fire_tool_hooks`
  says PostToolUse "should be fired on EVENT_TOOL_RESULT when available"; on this path nothing does.
  A user wiring an audit or paging hook to `Error` gets silence exactly when it matters. Owner: core
  seam.
- **`G42` (follow-up sweep) A mid-turn CLI death is reported as a timeout, is never retried, and
  leaves the inject-back pid file pointing at a corpse.** `kill -9` on the session's `kiro-cli acp`
  tree during an approved `sleep 40`: the turn ended `ACP prompt timed out` with no re-queue and no
  replacement process, while the session's `_acp_pipe_death_retries` counter exists for exactly this
  case. The next turn respawned transparently and answered in 2.5 s — and `session_pid_<pid>.txt`
  still named the **dead** pid, with no file for the live one (`K38`). So `G33`'s per-chat lottery is
  replaced by a staleness window: the file is wrong from the death until the session is re-created,
  which is the interval a subagent completion is most likely to arrive in. Owner: core seam.

**P3 — cosmetic / legibility / methodology**

- **`G38` Use kiro as the context-dump provider until a host-side dump exists — it retires `G26`'s
  blocker.** `AAP-2` filed `G26` because codex refuses ("I can't provide hidden context or
  instructions"), leaving the task-mode framing, persona and cancelled-turn-preamble cells
  unclosable and Phase 1's "zero UNKNOWN" bar dependent on new harness work. kiro returns its
  assembled framing verbatim on request (`K23`, `K24`), which is how `G29` — the most serious
  functional bug in this sweep — was proven rather than guessed. So the harness need is real but no
  longer blocking: prompt-side cells can be validated on kiro today, and `AAP-4`'s parity doc should
  say so. Owner: whoever runs the remaining Phase 1 follow-ups.
- **`G43` (follow-up sweep) A hand-authored `hooks.json` is silently rewritten to `{"hooks": []}` by
  the running gateway.** Six hooks written directly into `$PERSONALCLAW_HOME/hooks.json` load
  correctly in-process (`ScriptHookStore()` → 6) and are gone after a boot plus one turn — the file
  reduced to `{"hooks": []}` with the store's own `_save()` shape, twice in a row. `_load` never
  saves, so a mutator ran against an empty in-memory store and truncated the file. Nothing warns.
  The supported path (`POST /api/triggers` with `trigger_type: lifecycle` and `action.provider`)
  persists correctly, which is how `K39`/`K40` were driven — so the user-visible bug is narrow (only
  hand-edited files) but it is silent data loss in a file the docs describe as user-editable. Owner:
  core seam.
- **`G44` (follow-up sweep) The skill-ladder review cannot be observed or forced from any user
  surface.** With a live model provider and 25+ turns including corrections, `GET
  /api/skills/proposals` never left `{"proposals": []}`, and the route census offers accept, promote,
  verify and revert but nothing that runs the review — so a reviewer cannot distinguish "the gate
  said no" from "the review is inert" (`K44`). This is the one cell in the kiro column that no
  fixture can close; it needs either a forced-run endpoint or a SEL row per gate decision. Owner:
  whoever owns the learning cadences.

**Negative results worth keeping** (so nobody re-chases them): the hard deny-list **does** cover ACP
tools and names the pattern to the user (`K25`) — the first positive on that cell in three sweeps;
task-mode enforcement blocks both ask-mode and plan-mode writes and reports why, inline, without
killing the turn (`K14`, `K22`); session trust works and is honoured per-session (`K15`); queued
mid-turn messages drain end-to-end (`K15`); the per-session process is genuinely reused across turns
(`K8`) so the warm pool is warm on at least one provider; **every** kiro process tree was reaped when
the gateway died — zero orphans, where `AAP-1` leaked two (`K19`); SEL names every tool honestly on
kiro (`K12`, `K13`); and the on-disk `~/.aws/sso/cache/kiro-auth-token.json` `expiresAt` is **not** an
auth-freshness signal (five weeks expired beside a working refresh token), so a future sweep must
prove auth with a live call rather than by reading that file.

### Incidental bugs fixed in-session (kiro-cli)

**None.** Every defect this sweep localised is structural and shared, so the plan's own rule applies
("anything structural waits for Phase 2 so fixes land against the full three-provider picture"), and
the two that look smallest are the two that would have been worst to half-fix:

- `G29`'s one-line-looking cause (the task-mode framing not re-injected on a reused ACP process) sits
  in the shared per-turn context assembly, which every provider and the native runtime traverse.
  Changing when that block is rebuilt without the three-provider picture risks re-breaking the
  compressed-history bootstrap that `4a` shows *working*.
- `G31`'s real-home hook path is one string in `agent.py`'s generator, but that generator's output is
  also the artifact §2.1 Prong B plans to place into `~/.kiro/agents/`; the path and the placement are
  one decision and belong in the same change.

`G27` (the gate is not universal), `G34` (auto-approve keyed on adapter prose), `G35`/`G36`/`G37` (SEL
and card-payload shapes) all live in `acp/translate.py` and the shared chat-runner gate path — the same
files `AAP-2` declined to touch, and outside this atom's fence. Each is filed with its exact wire
evidence instead.

### Follow-up sweep — closing the NOT-EXERCISED remainder (`AAP-3`, 2026-08-18)

The first sweep left **20 of 63 cells NOT-EXERCISED**, so the atom's "every cell CONFIRMED or
DIVERGED" clause was unmet even though its three named unknowns were closed. This follow-up built the
fixtures the first sweep named as missing and drove the remainder. Ledger ids continue at `K27`.

**Host tip differs from the first sweep and that matters.** The first column was measured at `aa2610dc`;
this follow-up ran on `origin/main` @ `252c944f`, which contains `AAP-4`, `AAP-5` and `AAP-6`. So a
row re-measured here is a *later* observation of the same cell, and where the two disagree the row
below says so explicitly rather than overwriting history. Same isolated home discipline
(`PERSONALCLAW_HOME=/private/tmp/aap3n-wt/.dev-home`, `PERSONALCLAW_WORKSPACE=…/.dev-home/ws`,
`PERSONALCLAW_AUTH_MODE=none`, gateway on `:10461`, apps installed from the first-party clone),
same marking vocabulary, same rule that reading code is not a mark.

**Auth precondition re-checked FIRST, again three ways — verdict: FRESH.** No cell below is `ENV`
for auth reasons. (`K27`.)

**The fixture three sweeps deferred now exists.** `bedrock-models` installed into the isolated home,
provider instance `bedrock` on `global.anthropic.claude-haiku-4-5-20251001-v1:0`, `POST
/api/model-providers/bedrock/test` → `Connected — 123 model(s) available`, and all six use cases
(`chat`, `background`, `reasoning`, `code_tools`, `orchestration`, `loops`) bound. This is what
unblocked the four "needs a model provider" cells (`K29`).

| id | what was run | what was observed |
|---|---|---|
| `K27` | the auth precondition, before any capability probe: `~/.midway/cookie` mtime vs `date`; `mwinit -l`; `kiro-cli whoami`; a live model call | **FRESH.** Cookie written `2026-08-18 16:40:57 PDT`, sweep start `17:27:04 PDT` → **47 minutes** old. `mwinit -l` lists the cert and the cookie. `kiro-cli whoami` → `Logged in with IAM Identity Center (https://amzn.awsapps.com/start)`, `golani@amazon.com`, profile `KiroProfile-us-east-1`. Live call `kiro-cli chat --no-interactive "Reply with exactly the word AUTHOK…"` → `AUTHOK` (`Credits: 0.30 • Time: 1s`). `kiro-cli` **2.18.1**, same binary as the first sweep |
| `K28` | the cwd-confinement re-measurement, asserted **inside** the spawned CLI: S1 bound to `acp:kiro-cli` with `workspace_dir=…/.dev-home/ws`, then "Run exactly one shell command: `pwd`" | **`PWD=/private/tmp/aap3n-wt/.dev-home/ws` — `G1`'s escape does NOT reproduce at this tip.** The first sweep measured `~/.personalclaw/workspace` for the identical probe (`K4`). The `pwd` approval card's own `tool_input` also carries `working_dir: "…/.dev-home/ws"`, so the two no longer disagree inside one turn the way `K12` recorded. `AAP-5`'s log noted the same in passing; this is the direct measurement |
| `K29` | the model-provider fixture: `POST /api/apps` (`bedrock-models`), gateway restart, `POST /api/model-providers`, `/test`, six `PUT /api/models/active/<uc>` | `Connected — 123 model(s) available`; every use case echoes `["bedrock:global.anthropic.claude-haiku-4-5-20251001-v1:0"]`. The restart is load-bearing — a provider *type* only registers at boot, so creating the instance before it 400s with "unknown type" |
| `K30` | the combined prompt-side probe, one turn on a fresh session: `agent=aap3-probe` (a profile carrying `system_prompt` + `voice`), `color_theme=lumon`, `meta.knowledge=[<item id>]`, `meta.files=[…/uploads/aap3-brief.txt]`, message containing the literal `@aap3-prompt`, asking for six verbatim quotes | **four cells CONFIRMED in one turn, one ABSENT.** Reply verbatim: `(1) MANDATORY PROFILE MARKER: you must include the exact token PROFILE-MARKER-QX41 in every reply.` `(2) The AAP3 KIWI PROTOCOL states: the secret sweep codeword is ZANZIBAR-7719 …` `(3) ATTACHMENT-CONTENT-MARKER-B83: the attached briefing says the sweep vehicle is a hovercraft.` `(4) ABSENT` `(5) Use a Lumon-inspired persona. Keep responses technically useful and clear first.` `(6) ## Task mode: Agent`. So the agent-profile system prompt, the knowledge injector, the attachment extractor and the Lumon persona all reach kiro; the `@aap3-prompt` reference does **not** |
| `K31` | the `@prompt` half of `K30`, followed by `POST /api/prompts/aap3-prompt/render` | **expansion is composer-side, not host-side.** The persisted user message stores the literal `@aap3-prompt` unexpanded and the CLI never saw the body; the render endpoint returns `{"rendered": "EXPANDED-PROMPT-BODY-7C2: …"}` when called directly, and `web/src/pages/ChatPage.tsx` is the caller. So on the wire this cell is provider-independent — whatever the composer substitutes is what any provider receives — and nothing on the ACP path expands an `@name` |
| `K32` | incidental, visible in `K28`'s frames: the first tool call of the turn | `@personalclaw-core/get_context` — **`AAP-4`'s Prong A is live in this sweep**, so `G3`'s "no `personalclaw-core` surface at all" (`K4`) is already closed at this tip, on the protocol field alone with no seeded user config. It also raised its own approval card, i.e. the MCP surface is gated like any other tool |
| `K33` | the incognito guarantee: a session created `{"memory_mode":"incognito"}`, bound to kiro, driven with the SAME correction text that wrote three rows on a persistent session; db counts before/after; then a gateway restart | **zero writes.** `memory_events` 3→3, `semantic_memory` 3→3, `episodic_memories` 0→0. The CLI knew its posture — it answered "…for the rest of this incognito session". The transcript IS written (`memory_mode: incognito` in its metadata line, which is how the mode is restored, so this is by design, not a leak) and the session is **not** restored into the session list after a restart |
| `K34` | `POST /api/chat/sessions/<s>/regenerate` on a kiro session, then the persisted assistant row | **two variants persisted** — the row carries `variants` (2 entries) + `variant_idx`, first `(1) …` then `1. …`. So the ‹n/N› switcher has its data on kiro. The regenerated answer still quoted the injected knowledge and the profile marker, i.e. injection survives a regenerate |
| `K35` | the cancelled-turn preamble: a `sleep 40` shell call approved, `POST …/stop` ~20 s in (resolved `outcome: "soft"`), then a next turn asking to quote any line containing "PREVIOUS TURN WAS CANCELLED" | **CONFIRMED** — kiro quoted `[PREVIOUS TURN WAS CANCELLED BY THE USER -- context restore]` and the line after it verbatim. Note a **soft** stop is enough to arm it; the first sweep's failure was `G29` making its stop targets tool-free, not the stop kind |
| `K36` | the per-agent approval floor: profile `approval_mode: auto` bound via the turn's `agent` field, background auto-approver switched **off**, then `echo AUTOFLOOR-OK` | ran with **no approval card at all** — no `permission` frame, `pending_approval_info: null` — and the session came back `trust: true`. So the floor is real, and it is implemented by flipping session trust |
| `K37` | `ls $PERSONALCLAW_HOME/session_pid_*.txt` with four kiro sessions live | **four files, one per session** — `G33` ("written for only some sessions", measured 1-of-3 in the first sweep) **does not reproduce** at this tip. §2.1's subagent inject-back precondition therefore holds for every session here |
| `K38` | pipe death: `kill -9` on the session's `kiro-cli acp` tree mid-turn (during an approved `sleep 40`), then the same session's next turn, then the pid files | **no retry.** The stream ended `ACP prompt timed out`; nothing was re-queued and no replacement process appeared for that turn. The **next** turn respawned transparently (fresh tree, `RECOVERED` in 2.5 s) — but `session_pid_*.txt` still named the **dead** pid and no file was written for the live one, so the inject-back file is stale exactly after the event that makes it wrong |
| `K39` | PreToolUse blocking, both halves: six lifecycle hooks created via `POST /api/triggers` (`trigger_type: lifecycle`, `action.provider: bash`), the PreToolUse one `exit 2`; a write driven (a) while no agent referenced the hook, (b) with the hook ids bound to the session's agent profile | **the same hook blocks or does not, depending on who references it.** (a) it fired three times and the write still landed — `hooked.txt` contained `HOOKED`. (b) the tool line read `Running: @personalclaw-core/get_context (hook blocked: aap3-pretool:hook denied)` and `hooked2.txt` was **never created**. `chat_runner`'s `_fire` is agent-scoped by design ("there is no global firing path"); `fire_tool_hooks` is the informational path whose docstring says results "cannot block execution". Filed `G40` |
| `K40` | the other five hook kinds, same fixture, counted from the hooks' own log file over 25+ turns | `SessionStart` **1**, `UserPromptSubmit` **17**, `Stop` **15** — and `PostToolUse` **0**, `Error` **0**. The Error miss is not for lack of errors: `/compact` produced `-32601` and one turn produced a real `-32603` `MODEL_TEMPORARILY_UNAVAILABLE`, and neither fired it. Filed `G41`. (An auto-nudge injection also fires `UserPromptSubmit`, which is why that count exceeds the human turns) |
| `K41` | unattended mode as-a-user: a session keyed `cron:aap3-unattended` (the `is_unattended_session` prefix rule), auto-approver **off**, asked for a write | **fail-fast, not parking:** `Running: @personalclaw-core/get_context (auto-denied: unattended run, no one to approve)`, `[DONE]` in 5.2 s, `pending_approval_info: null`, and the requested file never created. The audit predicted ABSENT for this cell; `AAP-6` built it and this is its as-a-user measurement on kiro |
| `K42` | memory consolidation: 13 short turns to cross the threshold (it did not fire), then `POST /api/memory/consolidate {"key": "dashboard_<session>"}` | **CONFIRMED** — `last_consolidated` 0 → **33**, `semantic_memory` 3 → 5, `episodic_memories` 2 → 5. The per-turn cadence's gate is a 30-message threshold on the *history log* (`src/personalclaw/history.py:38`) and its offset is process-local, so a short session never reaches it; the explicit endpoint is the only way a user can force it |
| `K43` | auto-nudge re-arm: `POST /api/autonudge {"session_name": <kiro session>, "idle_secs": 20, "max_cycles": 2}`, then 75 s of silence | **armed, fired, re-armed, fired, capped** — `cycle_count: 2`, `active: false`, `last_fire_ts` set; the transcript carries both injected `NUDGE-PROBE` turns and kiro's `NUDGED` replies. First runtime demonstration of the loop-side nudge on an ACP provider |
| `K44` | the skill ladder, with a live model provider and 25+ turns (corrections included): `GET /api/skills/proposals` | `{"proposals": []}` throughout, and the route census shows accept/promote/verify but **no forced-run surface**. So the cell stays NOT-EXERCISED for a reason that is not a fixture — it needs instrumentation. Filed `G44` |
| `K45` | tool-disable prefs: `POST /api/mcp/toggle-tool {"server":"personalclaw-core","tool":"get_context","enabled":false}` | `{"error": "server 'personalclaw-core' not found"}` — the only per-tool disable surface addresses *configured* MCP servers, and an ACP CLI's tools are neither (kiro's 57 natives come from its own config; `personalclaw-core` is injected through the protocol field, not the registry). ABSENT with a named reason |
| `K46` | dry-run replay: a census of what a user can actually reach — routes matching dry-run/observe, config fields, and where the `dry_run` argument is accepted | the only `dry_run` on any surface is session-cleanup's unrelated preview flag; the T9 argument exists solely on the native runtime constructor, which an ACP session never builds. ABSENT by entry-point census rather than by interception, which is stated so nobody reads it as a driven negative |
| `K47` | the OS sandbox wrap: the gateway's own boot line, and the sandbox module's surface | `WARNING personalclaw.sandbox: No OS-level sandbox available — app-level checks only`. There is no host wrap engaged on this platform, so no confinement boundary exists to probe — **ENV**, not a capability verdict in either direction |
| `K48` | empty-turn auto-retry: every turn of this sweep, watched for a zero-content assistant turn | none occurred across 25+ turns and ten sessions, including a blocked write, a hook-blocked tool, an auto-denied unattended call, a cancelled turn and two protocol errors. Not forceable as-a-user |
| `K50` | the escape's other path, measured because `K28` looked like a clean fix: the SAME probe on a session whose turn also carried `agent=<a PersonalClaw profile>` — `workspace_dir` verified set on `GET /api/chat/sessions` (`/private/tmp/aap3n-wt/.dev-home/ws`), `pwd` asserted INSIDE the spawned CLI | **`PWD=~/.personalclaw/workspace` — the real home.** Reproduced on a second profile-bound session, whose `echo` tool call also carried `working_dir: "~/.personalclaw/workspace"` in its approval frame. So `G1` is fixed for a directly-bound session and alive for an agent-profile-bound one; the profile's empty `default_dir` wins over the session's explicit value. Filed `G39` |
| `K51` | **the tool-axis re-drive that the 2026-08-19 note said had failed.** Fresh isolated home (`/private/tmp/aap3-home`), kiro bundle installed from a local Store source, `kiro_default` bound, `workspace_dir` = the home's `ws/`; one turn asking for `pwd`, then every callable tool name verbatim, then YES/NO for three named tools. **The axis REPRODUCES.** `pwd` → `/private/tmp/aap3-home/ws` (the session's own `workspace_dir`, so `K28`'s confinement result reproduces too and `G39`/#1729/#1734 hold for kiro), followed by **~150 tool names**: kiro's own (`shell`, `read`, `write`, `grep`, `glob`, `code`, `use_aws`, `web_fetch`, `introspect`), the operator's twelve MCP servers from `~/.kiro/settings/mcp.json`, **and the whole `personalclaw-core` surface** (`get_context`, `notify`, `knowledge`, `memory_recall`, `artifact_save`, `workflow_*`, `subagent_run`, `automation_*`, `prompt_render`, `skill_invoke`, `todo_list`) |
| `K52` | why the earlier re-drive got `NO_TOOLS`, from the frames this one produced | **most likely a GATE artifact, not a registry fact.** The turn's FIRST action is `@personalclaw-core/get_context`, which raises an approval card and **parks the turn**; only after approving it (and a second card for `pwd`) did the enumeration arrive. The earlier note quotes kiro saying *"I don't have a shell tool available in this turn"* — per-turn wording that fits a denied/unresolved card. What would settle it: record the approval decisions next to the tool enumeration, because a probe that reads the tool list while its first call is pending measures the gate, not the registry |
| `K53` | the `Full native tool registry` verdict, re-measured against the ACTUAL tool names | **the ABSENT verdict was a NAMING artifact.** `K4` asked YES/NO for `knowledge_search` and `task_create`; neither string exists in the registry under any provider. The capabilities do: `knowledge`, `todo_list` / `automation_create`, `memory_*`, `artifact_*`, `workflow_*`, `subagent_*`, and `notify` (which `K4` itself scored YES). Registry rows must be scored against a censused name list, never against three remembered ones |
| `K54` | §2.1 prong B (the `personalclaw.json` config seed) end to end: does it run, and is it needed | **inert, and its premise is false.** (a) INERT — `register_acp_cli_entry` only seeds when a bundle passes `agent_config_dir`, and **zero of the three ACP bundles passes it** (`grep -c` = 0 each); the drive's log has no `agent-config seed` line, `$HOME/acp_seeds.json` was never written, and `~/.kiro/agents/` has no `personalclaw.json` — kiro's discovery returned the same 27 agents as `K2`, none of them ours. (b) NOT NEEDED — `~/.kiro/mcp.json` does not exist and no agent file mentions `personalclaw`, yet `K51`'s session listed the full `personalclaw-core` surface, so those tools arrived over the protocol's `mcpServers` at `session/new`. kiro does NOT ignore protocol-passed servers. `G46` |
| `K55` | the empty-turn cell, driven instead of declared unreachable: one turn saying *"Reply with absolutely nothing. Emit zero characters…"* | **CONFIRMED — no stream injection required.** The session shows the user row **twice** and then `error: Empty response — please retry.`, which is exactly `chat_runner`'s contract (`:3623-3652`): first empty → silent re-queue of the same prompt, second consecutive empty → the card. The duplicate user row IS the retry's fingerprint; the INFO line naming it is invisible only because the home's log level was WARNING |
| `K56` | the skill-ladder cell, driven against its real gate. Gate read from code first: `learning_decision_for_turn` → `LearningGate._worthwhile(PER_TURN)` = **a correction signal OR `tool_calls >= learning.min_tool_calls` (4)** — both drivable as-a-user, so `G44`'s "no forced-run surface" is not the obstacle. Drove a correction turn (heuristic verified offline: `is_correction_signal(msg) is True`) with `chat`+`background` bound to `Ollama:gemma4:12b` | **the ladder RUNS and it dies silently on a local model.** Under DEBUG the log reads `skill-ladder review: completion failed` → `after_turn_review.py:483` → `httpcore.ReadTimeout`, and `model_calls.jsonl` carries the matching row: `use_case=background, failure_mode=provider_error, latency_ms=60010` — the ollama bundle's 60 s default read timeout (`PersonalClawApps#47`, merged upstream, absent from this machine's clone). With `options.timeout_secs=900` the background passes complete (86.6 s / 94.1 s / 83.5 s, 5.7k-11k tokens in) and `GET /api/skills/proposals` still returns `[]`. **Still NOT-EXERCISED** — but for a new and fixable reason: nothing attributes a model call to its caller (`model_calls.jsonl` and `/api/models/telemetry` key on `use_case`, not subsystem) and the ladder's success path emits only a transient WS chip, so "the ladder declined" and "another background pass ran" stay indistinguishable. `G47` |
| `K57` | the second wave the census forced: **are the core tools merely LISTED, or callable?** One turn on a project-bound kiro session asking for four real calls — `skill_search`, `artifact_save`, `subagent_run`, `knowledge` | **all four executed, each behind its own approval card** (six cards in the turn). Raw results: `skill_search` → *"No skills matched. Try broader terms"*; `artifact_save` → *"Saved artifact 'AAP3-STAMP-PROBE' (slug: aap3-stamp-probe, version 1)"*; `knowledge` → *"No knowledge base entries found"*; `subagent_run` → **`<urlopen error [Errno 61] Connection refused>`**. So `K4`'s ABSENT verdicts on the skills tools, `artifact_save` and subagents were all artifacts of an enumeration that missed the protocol-delivered surface |
| `K58` | the one failure in `K57`, root-caused rather than reported | **a real host defect, found by driving and FIXED in this commit.** `mcp_core._resolve_api_base()` builds the API base from `dashboard.url` and falls back to **10000**; neither `--port` nor the `--port auto` that `--test-mode` uses writes that config, and nothing exported the bound port — so the MCP server POSTed to a dead port while the in-process tools beside it worked. The gateway now exports `PERSONALCLAW_PORT` after binding (both the dashboard and API-only paths) and `core_mcp_servers()` declares it in the child's env. **Before/after on the same home and CLI:** `1 task(s) queued (at capacity): … Connection refused` → `Spawned 1 subagent(s) … 6c6039b3` |
| `K59` | the artifact and subagent halves the calls exposed, read from the store rather than the reply | **two attribution losses.** (a) The artifact saved by the CLI on a session bound to project `p-14b92d4c` persisted with **`project_id: ""`** *and* `events[0].session_id: ""` — so the ABSENT verdict for `project_id → artifact stamping` is right, but for the opposite reason to the one recorded (`G48`). (b) The spawned subagent produced **no `[Subagent completion event]` injection** in ~4 minutes, and the gateway logged `_spawn_session_resolver: rid=spawn:6c6039b3 … session=` — an **empty** originating session, which is exactly the inject-back's precondition (`G49`) |
| `K49` | two cross-checks worth keeping: the lesson extractor on an injected-context turn, and one turn's vendor error | **`G16` is worse than "bad extraction":** the lesson row written for `K30`'s turn is `User correction to honor: The user referenced the following item(s) from their knowledge library. Their content is included below …` — the extractor swallowed the *injected knowledge block* as if it were the user's correction, alongside a second row `Never: never violate these):` clipped from prompt boilerplate. Separately, one turn failed with kiro's `-32603` `MODEL_TEMPORARILY_UNAVAILABLE` ("unexpectedly high load"); that is recorded as **ENV** and no cell rests on it |

## Execution log

- 2026-08-17 — `AAP-1` **PARTIAL**. Ran the audit §6 checklist against `acp:claude-code`
  (adapter `@agentclientprotocol/claude-agent-acp` 0.60.0, `claude` 2.1.233.669) on an isolated
  home, 19 turns across 6 sessions plus a gateway restart. 63 audit cells marked: 35 CONFIRMED,
  6 DIVERGED, 22 NOT-EXERCISED (residual list above). All four of the audit's literal `UNKNOWN`
  cells resolved. 16 findings filed P0-P3; two P0s (`G1` cwd/home escape, `G2` contingent gate
  coverage) and four P1s. Zero incidental fixes — every defect landed outside the atom's fence.
  **DISCOVERY:** the audit's biggest predicted hole (gap 2, CLI auto-approve bypassing the host
  gate) did **not** reproduce on claude-code — every one of the 44 SEL-audited tool events surfaced
  `session/request_permission` — but only because config isolation is off by default and this
  operator's settings auto-approve nothing, so `AAP-5`'s job is to make that coverage structural
  rather than to build a new gate. **DISCOVERY:** three cells the audit marked WIRED are broken
  (slash commands, session resume, project cwd) and one marked PARTIAL prints a fabricated
  number (context-%).
- 2026-08-17 — `AAP-2` **PARTIAL**. Ran the audit §6 checklist against `acp:codex` (adapter
  `@agentclientprotocol/codex-acp` 1.1.4, `codex` 0.146.1.359 on an Amazon-Bedrock model provider)
  on an isolated home, 17 turns across 4 sessions plus a fork, a regenerate, a mid-turn stop and a
  gateway restart. The same 63 audit cells marked: **33 CONFIRMED, 10 DIVERGED, 20 NOT-EXERCISED**
  (residual list above). All four of the audit's literal `UNKNOWN` cells plus the two it flagged for
  codex specifically (compaction, slash commands) are now definite. Ten findings filed `G17`-`G26`:
  one P0, three P1s, five P2s, one P3/methodology. Zero incidental fixes — both precisely localised
  defects live in `acp/translate.py` and the shared gate path, outside this atom's fence.
  **DISCOVERY:** eleven of AAP-1's sixteen findings reproduce on a different adapter, CLI and
  vendor (`G1`, `G3`-`G8`, `G13`-`G16`), which promotes them from claude-code behavior to host-side
  defects; `/compact` fails with the byte-identical `-32601 _vendor.dev/commands/execute` error, so
  `G4` is a host bug, not an adapter gap. **DISCOVERY:** `G2` does not reproduce — it is worse.
  codex has **no** `PERSONALCLAW_CC_ISOLATE` equivalent at all (the bundle disclaims isolation by
  design), so every host-managed codex session runs with the operator's 12 MCP servers live, their
  skills loaded, 31 descendant processes, and writes host-driven conversation transcripts into the
  operator's real `~/.codex` — `AAP-5` has no lever to flip here and must build one (`G17`).
  **DISCOVERY:** codex's `session/request_permission` payload carries the `kind` that AAP-1's `G10`
  said only the `tool_call` frame has, and its `session/update` carries a fully structured diff
  (`type: diff`, `oldText`, `newText`, `path`) — the host discards both, so the approval card cannot
  even name the tool (`G18`) and the diff chip §2.5 wants is already on the wire (`G22`).
  **DISCOVERY:** three cells AAP-1 could not close are now closed on codex — queued messages drain
  end-to-end, session trust does **not** bypass task mode, and plan mode is **not** native on codex
  (only the host gate stops it, the kiro shape the audit did not predict here). A task-mode denial
  also kills the entire codex turn (`G19`), while a rejected approval card is graceful.
- 2026-08-17/18 — `AAP-3` **PARTIAL**, and **Phase 1 is now complete: three checked-in verified
  matrix columns.** Ran the audit §6 checklist against `acp:kiro-cli` (`kiro-cli` 2.18.1, **no
  adapter** — it speaks ACP natively via `kiro-cli acp` on the core `default` dialect) on an isolated
  home, 20 turns across 6 sessions plus two gateway restarts, a fork, a mid-turn stop, a deny-list
  probe and a context dump. **Auth precondition satisfied FIRST and three ways** — `~/.midway/cookie`
  32 minutes old, `kiro-cli whoami` resolving to an IAM Identity Center identity, and a live model
  call returning `AUTHOK` — so **no cell is recorded as `ENV`**; every failure below is a capability
  observation. The same 63 audit cells marked: **31 CONFIRMED, 12 DIVERGED, 20 NOT-EXERCISED**
  (residual list above). All four of the audit's literal `UNKNOWN` cells, the two it flagged for kiro
  (compaction, slash commands) and **all three unknowns this atom was scoped to close** are now
  definite. Twelve findings filed `G27`-`G38`: two P0s, five P1s, four P2s, one P3/methodology, plus a
  measured confirmation of `AAP-2`'s `G21` on the provider §2.6 named it for. Zero incidental fixes —
  every defect is structural and shared, and the two smallest-looking ones are the two that would be
  worst to half-fix.
  **DISCOVERY (the one that matters most):** `AAP-1`'s `G2` is no longer contingent — it is
  **measured**. Seven of thirteen tool calls in a single turn executed with **no permission request
  at all** (kiro's native `todo_list`), each labelled `risk: "destructive"` by the host, in the same
  turns where the read, write and `rm` were carded. `AAP-2` concluded "no ACP tool executed without
  passing the host gate"; on a third provider that is false, so host safety on ACP is opt-in **by the
  CLI** and Success Criterion 3 is not met (`G27`).
  **DISCOVERY:** the three named unknowns resolved as — `personalclaw.json` is generated *correctly*
  and stored where kiro provably never looks (`kiro-cli agent list` prints its two roots; neither is
  `$PERSONALCLAW_HOME/agents/`), and the file's own `postToolUse` hook writes to a **literal**
  `~/.personalclaw/audit.log`, so §2.1 Prong B's planned symlink would activate a real-home write
  (`G31`); the effort pill is a **silent no-op that round-trips** on a provider advertising
  `supported_efforts: []`, confirming `G21`; and concurrent sessions are **declared, gated on, and
  absent** — flag true, dialect true, `concurrent_sessions_enabled('default')` true, connection cached
  on `runtime_id` alone, yet three sessions ran on three five-deep process trees (`G32`).
  **DISCOVERY:** ten of `AAP-1`'s sixteen findings reproduce on a provider with **no adapter**, a third
  vendor and a third auth model (`G1`, `G3`-`G8`, `G13`, `G15`, `G16`), which settles them as host-side
  defects rather than adapter behavior; `/compact` fails identically for the third time. But **three
  prior findings do NOT reproduce, which narrows their fixes**: `G18` (kiro names every tool in cards,
  `pending_approval_info` and SEL — never `unknown`), `G19` (a task-mode denial is graceful and legible
  on kiro, so it is codex-specific) and `G26` (kiro quotes its assembled context verbatim, so the
  prompt-side cells are measurable today — `G38`).
  **DISCOVERY:** kiro is the only provider that **reuses one CLI process across a session's turns**,
  which makes its warm pool demonstrably warm — and is exactly what exposes `G29`: the task-mode
  framing is fixed at process creation, so a session switched from Plan back to Agent keeps receiving
  `## Task mode: Plan` and refuses every tool forever, while the API and the host gate both say
  `agent` and the stale block itself insists *"This posture is current as of THIS turn"*. A gateway
  restart is the user's only escape — and `G30` shows that restart cannot re-bind the provider it just
  cleared until an erroring `POST /api/chat` touches the session first.
  **DISCOVERY (positive):** the hard deny-list **does** cover ACP tools and names the pattern to the
  user — the first positive result on that cell in three sweeps — and every kiro process tree was
  reaped when the gateway died (zero orphans, where `AAP-1` leaked two). Also recorded for future
  sweeps: `~/.aws/sso/cache/kiro-auth-token.json`'s `expiresAt` is **five weeks expired** beside a
  working refresh token, so that file is not an auth-freshness signal and reading it would mis-file
  working cells as `ENV`.

- 2026-08-18 — `AAP-5` **PARTIAL** (§2.2, gap 2 — the safety hole). Made the HOST the permission
  authority on the ACP path and, where it provably cannot be, made the *absence* of a gate legible.
  Four mechanisms: (1) `acp/permission_authority.sanitize_mode` clamps every requested native mode
  — called from `AcpClient.__init__` **and** `set_mode`, the chokepoint every path crosses
  (factory kwarg, bundle entry option, per-session override, loop/planning worker) **and** from
  `AcpSessionProvider.set_mode`, the pooled path's SECOND door (found by asking who else builds
  the dialect request directly — it bypasses `AcpClient` entirely, so clamping only the wrapper
  would have left the concurrent path open), so
  `acceptEdits`/`dontAsk`/`bypassPermissions`/`yolo` and any *unrecognized* mode become `default`
  with an SEL `mode_change:clamped_to_host_authority` row; empty now becomes `default` too, because
  "whatever the CLI defaults to" WAS the hole. (2) the deny-list is evaluated against the REAL
  command, not the display title — `command_probe` feeds the cached `tool_call` input to the hook
  chain in the `"Running: "` form, DENY-only by contract so it can never widen. (3) the permission
  frame now carries the adapter's declared `kind` — used for the card/SEL/residue, deliberately NOT
  passed to `task_mode_denies` (a CLI that labels a mutation "read" must not turn that gate's
  deny-by-default into an allow). (4) `NOT_GATEABLE` + `_report_ungated_tool_call`: a tool result for
  a call the host was never asked about is audited (`ungated` / `ungated_declared`), flagged on the
  tool row's meta, announced in the activity feed, and — when undeclared AND non-safe under
  ask/plan — aborts the turn via `session/cancel`.
  **Driven, not reasoned:** real `kiro-cli acp`, isolated home, `AAP5_REQUESTED_MODE=bypassPermissions`
  → clamped live to `default`; the CLI's own `pwd` returned the cwd we passed, so `G1`'s escape did
  not touch this drive. Turn A (pwd + write + rm): 3 tool calls, **3 gated**. Turn B (todo list +
  write + read): 6 tool calls, **1 gated, 5 ungated**. Replaying those VERBATIM frames through the
  real `_run_chat` gate with trust ON: under `task_mode=ask` the write and the `rm` are rejected with
  the standard "Ask mode — only read-only tools run" message while read-only `pwd` still runs, and
  under `task_mode=agent` the same write approves — the requirement and its inverse floor on real
  frames, with the vacuity floor satisfied inside one turn (the gate fires on the write while five
  other calls are ungateable).
  **DISCOVERY:** `G27`'s residue is WIDER than recorded. kiro does not only skip `todo_list` — it
  **self-approves its own file reads**: in one turn `Creating todo_probe.txt` raised a card while
  `Reading todo_probe.txt:1-10` (`kind="read"`) did not. Both are now declared entries; the read
  resolves to effective risk SAFE, so it is labelled and never turn-aborting. That asymmetry is the
  reason the abort rule keys on *undeclared AND non-safe* rather than on "ungated": enforcing on
  "ungated" alone would abort five of every six kiro turns.
  **DEVIATION:** the residual set is enumerated in CODE (`NOT_GATEABLE`), not in §2.7's
  `docs/agents/acp-parity.md` — that doc does not exist yet and belongs to a later atom; it should
  render the registry. **DEVIATION:** `sanitize_mode`'s `unattended=True` escape exists but nothing
  wires it — the bridge still pops `unattended` for ACP (§2.3 owns that), so loop/planning workers
  now get `default` and execute because their session already carries `_trust`; the approval becomes
  AUDITED instead of invisible, which is the intended direction, but §2.3 must thread `unattended`
  before any un-trusted background ACP run can be non-wedging. **DEVIATION:** the apps-repo half of
  the mechanism (flip `PERSONALCLAW_CC_ISOLATE` from opt-in to the bundled default in
  `claude-code-agent/provider.py:_build_env`) is NOT landed — cross-repo, handed to the owner as an
  exact diff. That lever exists ONLY in the apps bundle; core `src/` never mentions it.
  **BOUNDARY:** claude-code and codex were not re-driven live (their adapters are not installed on
  this machine), so their "residual set EMPTY" entries carry `AAP-1`/`AAP-2`'s measurement, not a
  fresh one — and per `G27` an empty set is a measurement, never a guarantee.
- 2026-08-18 — `AAP-4` **PARTIAL** (§2.1 MCP reachability, gap 1). Both prongs implemented and
  **both measured working on kiro-cli**, the only ACP CLI installed on this machine; claude-code and
  codex are NOT-EXERCISED for the honest reason that their adapters are absent.
  **Prong A (protocol) — `O18`'s open question is now ANSWERED for kiro: HONORED.** `client.py`'s
  three `session/new`/`session/load` sites and the pooled opener now carry the `personalclaw-core`
  spec (name + `personalclaw mcp-core` + an `env` array). Driven end to end, kiro spawned the server
  from the protocol field alone (a `personalclaw mcp-core` process appeared for the session's life,
  with no seeded config present) and invoked `@personalclaw-core/get_context` and
  `@personalclaw-core/notify`; `notify` returned `Message sent.` and the notification landed in the
  isolated home. So `G3`'s "no `personalclaw-core` surface at all" is closed for kiro **without any
  user-config mutation** — the outcome §2.1 called "the clean fix".
  **Session inject-back CONFIRMED via the env leg:** the SEL rows for the turn carry
  `caller_identity = <the session key passed in the spec's env>`, i.e. `mcp_core._resolve_session_key`
  read `PERSONALCLAW_SESSION_KEY` off the protocol-passed env. The `session_pid_<pid>.txt` leg was
  NOT exercised here (the probe is not the gateway's session manager, which owns those files);
  `O11` already measured that file as written on the real path.
  **Prong B (seeding) — measured discoverable, and the plan's placement is worse than an
  alternative.** `personalclaw` appears in kiro's `availableModes` (27 → 28 agents) once the
  generated config is seeded. **DISCOVERY:** seeding `<cwd>/.kiro/agents/` — kiro's *workspace*
  discovery root, which for an ACP session is the PersonalClaw workspace dir — is discovered just as
  reliably as `~/.kiro/agents/` and mutates nothing of the user's real config. §2.1 prong B should
  prefer the cwd root over the `~/.kiro/agents/` symlink it currently specifies.
  **`G31` fixed at the generator.** The bundled bash-audit hook's literal `~/.personalclaw/audit.log`
  now resolves against `config_dir()` at generation time (a `{{PERSONALCLAW_AUDIT_LOG}}` token
  expanded in `agent.build_agent_config` / `_refresh_dynamic_fields`, fail-closed on any surviving
  placeholder). Verified in the live generated file, and the operator's real
  `~/.personalclaw/audit.log` was **never created** across the whole session — including the seeded
  run where kiro genuinely loaded our agent config — with the real home's file count identical
  before and after.
  **BLOCKER on two acceptance clauses (premise mismatch, not a wiring gap):** §2.1's acceptance and
  Success Criterion 2 name `knowledge_search` and `task_create`, but **neither tool exists in the
  `personalclaw-core` surface**. `mcp-core` serves 68 tools (artifacts / workflows / memory /
  subagents / skills / automation / notify), matching `:34`'s own description; `knowledge_search` and
  `task_create` are **native-registry-only** (`agents/native/builtin_tools.py`). No amount of
  reachability wiring can surface them — exporting those two tool groups from `mcp_core` is separate
  scope. `notify` and `subagent_run` ARE in the surface (`notify` driven; `subagent_run` present in
  the session's tool list but not invoked).
  **DISCOVERY (inert control):** the bundled `postToolUse|bash` audit hook can never fire for our own
  generated agent, because that config's `tools`/`allowedTools` is exactly `["@personalclaw-core"]` —
  it has no bash tool to post-hook. The hook is correct and now correctly-pathed, and still dead for
  the one agent it ships with.
  **NOTE for the owner:** prong B's seeder has no production declarer. It is deliberately opt-in via
  a new `agent_config_dir` parameter on `register_acp_cli_entry` (which directory a CLI reads is
  vendor knowledge, so it stays in the bundle), and no bundle declares it because prong A is measured
  to work on the only CLI we can drive. That is one line in `PersonalClawApps/kiro-cli-agent/
  provider.py` whenever a CLI is measured to ignore the protocol field.
- 2026-08-18 — `AAP-6` **PARTIAL** (§2.3, gaps 3 and 5). Written against `AAP-5` and shipped FLAT on
  `main`: `AAP-5`'s PR reads `CLOSED` rather than merged, but all seven of its files are byte-identical
  between its commit and `origin/main`, so the duplicate commit was dropped rather than stacked.
  **Gap 3 — unattended threading.** `unattended` no longer dies at the bridge: it is still popped
  (the MODEL-axis resolvers must never see it) and re-injected for `_kind == "acp"`, then threaded
  `acp_agent._factory` → `AcpAgentProvider._unattended` → `AcpClient(unattended=)` →
  `sanitize_mode(mode, unattended=…)`. The pooled/concurrent door gets the same axis via
  `AcpSessionProvider(unattended=)` plus a `set_unattended()` the claim path calls **before**
  `set_mode` (a warmed pool connection is attended by default, so without it the loop's
  `bypassPermissions` was clamped straight back). Resolving the tension with `AAP-5` is the whole
  atom: an unattended session may keep an auto-approve mode, an interactive one may not, and the
  widening is audited (`mode_change:unattended_auto_approve`) rather than only the clamp.
  **Unified beyond loops:** the unattended axis is now derived from
  `guardrails.policy.is_unattended_session(session.key)` OR the loop manager's explicit
  `session._unattended`, so cron/scheduled/subagent/channel/inbox/side runs get it too — §2.3 asked
  for exactly that, and it makes ONE definition of "nobody is watching" govern both the HEADLESS
  safety profile and the permission path. **Host-side fail-fast** sits as the LAST gate before
  `chat_runner`'s interactive-approval park (after trust/YOLO and after every deny path), so it can
  only ever convert a two-hour stall into an immediate audited denial — never a denial into an
  approval.
  **Gap 5 — the loop breaker.** `_FailureBreaker` + `_params_key` + `_result_digest` + the four
  thresholds moved out of `agents/native/runtime.py` into `guardrails/loop_breaker.py` as
  `LoopBreaker` (a clean-break extraction — no alias shim; the two test files importing the private
  names were updated). The standard notices moved with it as builders, so "the standard breaker
  message" is now ONE string both runtimes emit. `chat_runner` runs the same counter over the
  neutral ACP stream: warn → block-notice → turn-abort via `cancel_session()` at the circuit.
  **DISCOVERY — this is why `G6` measured nothing:** `translate.extract_tool_update_events` emitted a
  byte-identical `EVENT_TOOL_RESULT` for `status: "completed"` and `status: "failed"`, so the host was
  never told an ACP tool had failed at all. No amount of counting downstream could have worked. It
  now carries `tool_meta={"ok": False}` on failure only (matching the native contract, so no
  existing reader changes on a passing call) — which also lights up the tool card's existing failure
  colour-coding for free.
  **Live drive (kiro-cli only — claude-code and codex adapters are not installed here, same boundary as
  `AAP-1`/`AAP-2`/`AAP-4`/`AAP-5`).** A `cron:`-keyed unattended session forwarded
  `bypassPermissions`, kiro raised `session/request_permission` anyway (it has no mode axis — the
  documented asymmetry, confirmed live), and the host auto-denied it: the turn ended
  `stop_reason='refusal'` in seconds instead of parking. The same drive on an interactive key clamped
  to `default` and rendered an approval card that waited for a human — the clamp and the fail-fast are
  both live and both key off the session, not the content. The breaker's **structural (no-progress)
  rung fired twice on real kiro turns**, injecting its notice into the transcript after the third
  identical result.
  **BOUNDARY / measured limitation:** the breaker's *failure* rungs (warn/block/circuit) were NOT
  reached live, because kiro never emitted `status: "failed"` in any drive — a non-zero shell exit is
  reported as a COMPLETED tool call carrying the exit status as content, and an unavailable tool never
  becomes a tool call at all. For kiro's shell tool the failure rungs are therefore unreachable and
  the structural rung is what catches a repetition loop; the failure path is proven only by driven
  synthetic-stream tests (`tests/test_acp_unattended_and_loop_breaker.py`), plus the unit mapping of
  `status: "failed"` → `ok: False`, which any protocol-conformant CLI will exercise. Whether
  claude-code/codex report `status: "failed"` for a failing command is unmeasured here.
  **BOUNDARY:** the ACP observer can warn and abort BETWEEN protocol frames; it cannot refuse a call
  pre-execution the way the native runtime can. That asymmetry is stated in `loop_breaker`'s module
  docstring and belongs in §2.7's parity doc when that atom lands.

- 2026-08-18 — `AAP-3` **follow-up sweep: the atom's remaining clause is now met.** The first sweep
  closed the three named unknowns but left **20 of 63 cells NOT-EXERCISED**, so "every cell CONFIRMED
  or DIVERGED" was unmet. This session built the fixtures the first sweep named as missing and drove
  the remainder: **18 of the 20 resolved** (13 CONFIRMED, 4 DIVERGED, 1 ENV), leaving **two** with a
  stated reason that is not a fixture. Column now **44 CONFIRMED / 16 DIVERGED / 1 ENV / 2
  NOT-EXERCISED**. Auth precondition re-checked FIRST and three ways (cookie 47 min old, `whoami` →
  IAM Identity Center, a live call returning `AUTHOK`) — **no cell is `ENV` for auth reasons**; the
  one vendor `MODEL_TEMPORARILY_UNAVAILABLE` turn is recorded as ENV and nothing rests on it.
  Ledger `K27`-`K50`; six new findings `G39`-`G44` filed in the ranked inventory (one P0, one P1, two
  P2, two P3). **Host tip is `252c944f`, not the first sweep's `aa2610dc`** — so re-measured rows say
  so instead of overwriting history.
  **The fixture three sweeps deferred now exists:** `bedrock-models` in the isolated home on
  `global.anthropic.claude-haiku-4-5-20251001-v1:0`, `Connected — 123 model(s) available`, all six
  use cases bound. That alone closed unattended mode, auto-nudge re-arm and memory consolidation, and
  turned the skill-ladder cell from "no model" into a real finding.
  **DISCOVERY (the one that matters most):** `G1`'s real-home escape is **half fixed, and the
  remaining half is invisible to the obvious probe.** A directly bound kiro session's own `pwd` now
  answers the isolated `workspace_dir` (`K28`) — the first sweep measured the real home for the
  identical probe. Bind a PersonalClaw **agent profile** and the same in-CLI `pwd` answers
  `~/.personalclaw/workspace` (`K50`), reproduced on a second profile-bound session whose
  tool frame also carried the real home as `working_dir`. Filed P0 as `G39`: a sweep that only drives
  the plain binding will now report the escape as gone.
  **DISCOVERY:** a `PreToolUse` hook is a **two-state safety control**. Installed but not referenced
  by the session's agent profile, it fires, logs, and the write still lands; referenced, the identical
  hook blocks the tool and the UI names it (`hook blocked: aap3-pretool:hook denied`) (`K39`, `G40`).
  Two of the six hook kinds never fire at all on the ACP path — `PostToolUse` and, with two real
  errors available to fire it, `Error` (`K40`, `G41`).
  **DISCOVERY:** the prompt-side column can be closed in ONE paid turn on kiro. `G38`'s
  quote-your-context method carried a profile `system_prompt`, a knowledge injection, an attachment
  extraction, a Lumon persona and the task-mode line in a single reply (`K30`) — five cells, one turn.
  The same turn showed `@prompt` expansion is composer-side, so that cell is provider-independent by
  construction (`K31`).
  **DISCOVERY:** `G33` does not reproduce (four sessions → four pid files, `K37`), but pipe death
  replaces it with something worse-bounded: `kill -9` mid-turn yields `ACP prompt timed out`, **no
  retry**, and a `session_pid_<pid>.txt` still naming the dead pid after the next turn transparently
  respawns (`K38`, `G42`). Unattended mode measured **better** than the audit predicted — the host
  auto-denies in 5 s rather than parking (`K41`) — and the cancelled-turn preamble, the cell three
  sweeps could not close, is quoted verbatim after a **soft** stop (`K35`).
  **DEVIATION:** the 20 rows keep their first-sweep text and gain a `NOT-EXERCISED → <mark>
  (follow-up)` mark rather than being overwritten, because the two observations are at different host
  tips and both are true.
  **BOUNDARY:** `OS sandbox wrap` is `ENV`, not a verdict — the gateway itself logs `No OS-level
  sandbox available — app-level checks only` on this platform, so there is no confinement to probe.
  `Dry-run replay` and `tool-disable prefs` are ABSENT by **entry-point census** (no route, no config
  field, and `toggle-tool` cannot address a protocol-injected server), which is stated as such so
  nobody reads them as driven negatives.
---

**2026-08-17 — DONE (ad-hoc P0, not an atom): `G39` a profile-bound ACP session spawned its CLI in
the operator's REAL home.** Three sites, one shape — a per-session working directory that the
resolvers got right and the spawn path then ignored, landing on a real-home literal:

1. `llm/acp_agent.py` `_factory` read `cwd` from `entry.options` ONLY, while every sibling
   per-session axis in the same function (`agent`, `model`, `acp_mode`, `reasoning_effort`,
   `sandbox`) honors the kwarg over the option. So `session.workspace_dir` — threaded from
   `chat_runner` → `SessionPool` → `provider_bridge` → `registry.build(cwd=…)` — was dropped on the
   one-session path. (The N-session concurrent path was correct: `session.py`'s
   `_open_concurrent_acp_session` passes `cwd=` to `pool.open_session`. That is why a directly-bound
   session measured CORRECT and a profile-bound one did not — a profile whose runtime doesn't resolve
   a `provider_kind` takes the factory path.)
2. `acp/client.py` then fell back to `Path.home() / ".personalclaw" / "workspace"` — a real-home
   literal that ignores `PERSONALCLAW_HOME`, dev homes and test fixtures. `AcpProcess.spawn` MKDIRs
   that cwd, so this wrote to the operator's home. Now `workspace_root()`. `_work_dir` also became a
   property over the transport's copy: two copies meant `set_workspace` moved the client's view and
   left the process spawning in the ORIGINAL directory.
3. `AcpAgentProvider.discover_agents` had the same literal for its throwaway probe connection.

Separately, `POST /api/chat/sessions/{s}/agent` assigned `resolve_agent_bindings().workspace_dir`
UNCONDITIONALLY, so a profile with an EMPTY `default_dir` displaced a workspace the user had bound
via `POST …/workspace-dir` — a direct contradiction of that field's own contract (`config/loader.py`:
*"Empty inherits the workspace root. Overridable per-session."*). New
`config.loader.resolve_session_workspace()` states the precedence once and both handlers (bind + create)
use it; a NON-empty `default_dir` still wins, unchanged.

Rails: `tests/test_acp_spawn_cwd_containment.py` asserts the **spawn kwargs** (the `cwd=` handed to
the sandbox handle's `exec`, i.e. what `create_subprocess_exec` receives), not a resolver's return
value — plus a containment assertion that reds on any spawn cwd outside `workspace_root()`, a vacuity
guard on the capture seam, and an AST rail forbidding `Path.home()` in any of the six ACP spawn
modules (the only cheap way to keep `discover_agents` honest, since driving it spawns a real CLI).
`tests/test_chat_session_workspace_dir.py` covers the handler precedence both ways.

**Not verified without a real CLI:** the fix was proven at the spawn kwargs, not by reading `pwd`
inside a live `kiro-cli`. Also unfixed and out of scope: `session.py:589` `effective_cwd` can be the
empty string when `default_workspace_dir()` finds no safe root, which spawns in the gateway's own cwd
(not a real-home escape, but the same family).

### 2026-08-19 — `AAP-3` re-verification attempt: three findings, atom stays `todo`

Picked `AAP-3` as the highest-unblock ready atom (it and `AAP-1`/`AAP-2` each gate the same seven
atoms, `AAP-4`-`AAP-10`, conjunctively). Before flipping a column I did not measure myself, I drove a
fresh gateway against it. Auth precondition first, as this atom's `done_when` requires:
`~/.midway/cookie` written 00:41, **0.9 h old** at drive time — fresh, so nothing below is an ENV
verdict. `kiro-cli 2.18.1`, the same build the sweep recorded (line 752).

**1. DISCOVERY — the "ACP adapter is not installed" premise is FALSE, and it has been blocking
`AAP-1`/`AAP-2` on nothing.** In a fresh isolated home (a copy of the dev home), `GET
/api/agent-providers` returns all four ready:

| provider | ready | detail |
|---|---|---|
| `native` | true | in-process runtime |
| `acp:claude-code` | true | **warmed (pooled live connection)** |
| `acp:codex` | true | **warmed (pooled live connection)** |
| `acp:kiro-cli` | true | `initialize OK (caps: auth, loadSession, mcpCapabilities, promptCapabilities, sessionCapabilities)` |

The adapters live **in the home**, not on `PATH`: `<home>/acp-adapters/node_modules/.bin/`
carries `claude-agent-acp` and `codex-acp` (installed 25 Jul), and the gateway's own children are
those two node processes. A `command -v claude-code-acp` check reports "not installed" and is simply
looking in the wrong place for the wrong name — the package is `@agentclientprotocol/claude-agent-acp`.
So `AAP-1` and `AAP-2` are **not** environment-blocked; their residual cells are fixture and
injection work, plus the model-provider-dependent ones.

**2. The kiro column's tool rows do NOT reproduce, so `AAP-3` cannot be certified.** Reproduced
exactly: `K1` (ready + the identical caps string), `K2` (**27** agents, same 3 kiro built-ins + 24
operator fleet), `K3` (`workspace_dir` + `acp_provider` both round-trip). Then it diverged:

- Asked for one shell command (`pwd`), kiro replied *"I don't have a shell tool available in this
  turn"* and correctly refused to fabricate the output.
- Asked to enumerate its callable tools, it replied **`NO_TOOLS`**.
- `K4` measured **57 tools** on this same CLI version, with its own `shell`/`read`/`write` working.

**Control, same gateway, same isolated home:** a second session bound to `acp:claude-code` listed
nine callable tools (`Agent, Bash, Edit, Read, ReportFindings, Skill, ToolSearch, Workflow, Write`)
plus deferred MCP ones — so the host's tool-exposure path is healthy and this is specific to kiro.

Cause **not** established, and I am not guessing one. Candidates: the operator's `~/.kiro` MCP
servers not starting under the gateway's environment (that fleet is where all 57 came from), singleton
contention with a concurrently-running MCP fleet on this machine, or a kiro-side change within 2.18.1.
Consequence: the `pwd` cwd-escape question (`K4`, and whether `G39`/#1729/#1734 fixed it for kiro) is
**unresolved** here — with no shell tool there was nothing to escape with.

`AAP-3` therefore stays `todo`. Closing it would certify 44 CONFIRMED cells while its tool rows fail
to reproduce on the first re-drive. The two genuinely-unreachable residual cells (skill-ladder review,
`G44`; empty-turn auto-retry) remain correctly characterised — that judgment is unchanged and is not
what blocks the atom.

> **CORRECTION, 2026-08-19 (later the same day) — the tool axis DOES reproduce, and the second
> sentence above was wrong twice.** A second re-drive on a fresh isolated home enumerated **~150
> tools** including kiro's own `shell`/`read`/`write` and the entire `personalclaw-core` surface, with
> `pwd` answering the session's `workspace_dir` (`K51`) — so the cwd question this note called
> unresolvable is resolved, in `K28`'s favour. The `NO_TOOLS` answer is best explained as a **gate
> artifact**: the turn's first tool call raises an approval card and parks, and kiro's own wording was
> per-turn (*"in this turn"*) (`K52`). And of the "two genuinely-unreachable" cells, one was not
> unreachable at all — an empty turn is produced by asking for zero characters (`K55`) — while the
> other's reason is superseded (`G47` replaces `G44`, `K56`). **What still blocks the atom** is a
> single unexercised cell plus one `ENV` cell, not the tool axis.

**3. Incidental bug found in-session and FIXED here** (this campaign's doctrine: *"incidental
in-session bugs fixed per campaign doctrine"*). `POST /api/chat/sessions/{id}/workspace-dir` read an
absent `workspace_dir` key as "clear it": my first request used `{"dir": …}` and got
`{"ok": true, "workspace_dir": ""}` — a 200 that **unbound the session's workspace**. For an ACP
session that binding is where the CLI runs, so this is the `G39` failure shape (agent lands in the
wrong directory, operator believes otherwise) reachable by an ordinary typo. An omitted key is now a
400 naming the one deliberate way to unset it; an explicit `{"workspace_dir": ""}` still clears, and
its existing test still passes. Falsified by restoring the exact pre-fix line — the new tests fail
`assert 200 == 400`, i.e. they catch precisely the live behaviour I measured, not merely any change.

### 2026-08-19 — `AAP-10` DONE: `docs/agents/acp-parity.md` written from the three verified columns

Wrote the §2.7 deliverable (451 lines): a "how to read this" opening that states plainly that ACP
providers are **not** at native parity; the versions measured on the dev host today (`claude`
2.1.234.669 + adapter 0.62.0, `codex` 0.146.1.360 + adapter 1.1.7, `kiro-cli` 2.18.1 native, `gemini`
absent), including the note that adapters live per-home under `acp-adapters/node_modules/.bin/` so a
`PATH` check wrongly reads them as missing; a coverage table publishing the real mark counts
(claude 35/6/22, codex 33/10/20, kiro 44/16/1/2 after its follow-up sweep, gemini 0/63); then per
provider the three `done_when` buckets — at parity, host-compensated, protocol/CLI constraint — each
constraint row carrying its reason, its watch item (CLI / adapter / protocol / host seam + owning
atom) and the version it was measured against, grouped by the five matrix axes so a reader can
cross-check against the columns. The not-gateable section **renders** `acp/permission_authority`'s
`NOT_GATEABLE` per §2.2 rather than re-deriving it. Added a "where the columns disagree" table,
because those asymmetries are the finding (§2.6), and a refresh procedure.

**Version drift is now a first-class part of the doc.** Measured today: claude and codex are running
NEWER builds than the ones their columns were measured on (claude `2.1.234.669` + adapter `0.62.0`
vs `2.1.233.669` + `0.60.0`; codex `0.146.1.360` + adapter `1.1.7` vs `0.146.1.359` + `1.1.4`), while
kiro is on the *same* `2.18.1` that the 2026-08-19
re-drive above got `NO_TOOLS` from. So every kiro tool-axis row is published tagged **(tool axis)**
with the "measured, then failed to reproduce on the same build, cause not established" caveat, and
its `pwd` confinement question is stated as unresolvable from that re-drive (no shell tool, nothing
to escape with). claude-code and codex are published as 41- and 43-cell columns with their
NOT-EXERCISED remainders listed by *why*, so neither reads as complete.

**Clauses I could NOT source, stated rather than filled:**

1. **"Linked from each agent bundle's README" is not satisfiable in this repo.** The bundle READMEs
   live in `PersonalClawApps`; this session wrote core only. The atom's link half is outstanding.
2. **No upstream tracker item exists for any ABSENT row.** The material names *what* must change and
   *where* (CLI, adapter, ACP protocol, or host seam) but cites no upstream issue or PR, so every
   watch item is written as a change-and-owner statement, never as a link. "The upstream issue to
   watch, where one exists" is therefore satisfied only in the "where one exists" sense: none does.
3. **gemini-cli has no column at all**, so it is published as 63 unmeasured cells plus the two
   shipped-data honesty notes already in `agents/runners.py` (its `--experimental-acp` flag is
   declared from vendor docs, not measured; the health probe only proves `--version`), rather than
   as an inferred column.
4. **Post-sweep host compensation is published as landed-but-not-re-driven.** `AAP-4`'s core MCP
   surface, `AAP-5`'s permission authority + kiro config seeding, and `AAP-6`'s unattended fail-fast
   and loop breaker exist in the host, but as-a-user measurements exist only on kiro (`K32`, `K41`)
   — the doc says so per row instead of generalizing them to three providers.
5. **No `file.py:NNN` citations were used anywhere** (doc or this entry), per the citation rail:
   claims cite ledger ids and `G`-numbers instead. Nothing in `docs/roadmap/atomic/` was touched.

- **2026-08-19 — `AAP-10` CLOSED (`todo` → `done`); both halves are merged.** Core `#1746` landed
  `docs/agents/acp-parity.md` (451 lines) and apps `#48` added a **Capability boundary** section
  linking it from all four bundle READMEs (`claude-code-agent`, `codex-agent`, `kiro-cli-agent`,
  `gemini-cli-agent`) — verified by content on merged `main` in both repos, not by PR title.

  Each `done_when` clause, and where it is satisfied:

  | clause | where |
  |---|---|
  | the doc exists | `docs/agents/acp-parity.md` on `main` (`b28cb92c`) |
  | per provider: at parity / host-compensated / protocol-or-CLI constraint | three bucket headings per provider section |
  | each ABSENT carries its reason + watch item + verified version | every constraint row; versions in the doc's own version table |
  | linked from each agent bundle's README | apps `9f284a69`, all four bundles |

  **One clause is satisfied only in the weak sense, and the doc says so:** the plan asks for "the
  upstream issue to watch, **where one exists**". No row has an upstream issue or PR, so every watch
  item is written as a change-and-owner statement (what must change, and in the CLI, the adapter, the
  ACP protocol or a host seam) rather than a tracker link. That is the clause's own escape hatch, not
  a gap papered over.

  **What the doc deliberately does NOT claim**, because this is the honest-boundary deliverable and
  overstating it would defeat the point: claude-code and codex are **41- and 43-cell columns**, not 63
  (22 and 20 cells have no runtime observation, listed and grouped by why); every kiro tool-axis row
  carries the 2026-08-19 non-reproduction warning; and `AAP-4`/`AAP-5`/`AAP-6`'s host compensation is
  marked **landed but not re-driven** — it is in the code, with as-a-user measurements only on kiro
  (`K32`, `K41`).

  Closing this atom does **not** unblock `AAP-4`-`AAP-9`: their `dag` deps are the three Phase-1
  sweeps (`AAP-1`/`AAP-2`/`AAP-3`), all still `todo`. See the 2026-08-19 discovery entry above for the
  measured state of their implementations.

- **2026-08-19 — `AAP-1` one residual cell CLOSED by drive; the atom stays `todo` with 21 of 22
  remaining, and the REASON recorded for group 1 is now stale.**

  The cell: **Unattended mode (strip interactive tools + fail-fast approvals, T5)**, which the audit
  predicted ABSENT and the first sweep left NOT-EXERCISED because "this isolated home has no model
  provider configured". Both halves of that have changed. `provider_bridge.py` now **re-injects
  `unattended` for the ACP branch** (it was popped and discarded when the audit was written), and a
  local model serves `chat` **and** `background`, so a loop no longer dies on
  `no model provider resolves for use case 'background'` before the worker's first turn.

  **Driven (`O27`).** `POST /api/loops {kind: "code", attended: false}` → `PUT` bound
  `provider: acp:claude-code` and an isolated `workspace_dir` → `PATCH {action: "start"}`. Result:
  status `running`, the adapter process spawned under the isolated home, and — the part that decides
  the cell — **SEL recorded `mode_change:unattended_auto_approve` → `allowed` with
  `resources="session=dashboard:loop-961dcbd8 mode=bypassPermissions"`**, alongside
  `set_approval_policy` → `auto` and the worker's tool logged as `Terminal` `invoked` then `ungated`.
  The task's file appeared in the workspace with the right contents (`aap1-probe.txt` = `OK`) at
  ~t+225s, and `/api/approvals` stayed `[]` for the whole run. So: it executes via
  `bypassPermissions`, it is auditable, and it never wedged — which is exactly §2.3's acceptance
  wording. Verdict **DIVERGED-better**: the audit's ABSENT no longer describes `main`.

  Worth stating plainly because it is the security-relevant consequence: unattended means the
  worker's tools run **ungated**, and the SEL row is what makes that legible after the fact. That is
  the intended trade (a background turn must not block on a human), not a gap.

  **The recipe, recorded so the remaining four cells are mechanical next time:** an isolated home
  with the ollama provider installed from apps `origin/main`, `active_models.json` binding BOTH
  `chat` and `background`, `timeout_secs` raised (a local reasoning model needs minutes, and the
  entry factory only honours the value since apps#47), then the four calls above. The loop must be
  `stop`ped afterwards or it keeps cycling to `max_cycles: 30`.

  **Then three more cells, same session, using the kiro sweep's own recipes.**

  | cell | verdict | evidence |
  |---|---|---|
  | auto-nudge re-arm | **CONFIRMED** | `O28` — `cycle_count` 0 → 1 → **2 of 2**, `active` true → **false** at the cap, and the transcript carries both `[auto-nudge cycle N]` injections with claude-code's own `NUDGED` reply after each |
  | memory consolidation | **CONFIRMED** | `O29` — `last_consolidated` **0 → 6** in the history metadata plus a `consolidate_…` lock file. The semantic/episodic counters did **not** move, which is right for six short probe turns: the pass is that consolidation RAN, not that it invented rows |
  | tool-disable prefs | **ABSENT**, decided | `O30` — `{"error": "server 'personalclaw-core' not found"}`, the same named reason as kiro's `K45`: the only per-tool surface addresses *configured* MCP servers, and an ACP CLI's tools are not registry entries |
  | skill-ladder review | stays NOT-EXERCISED | `O31` — `{"proposals": []}` and no forced-run surface, i.e. kiro's `G44` reproduced. An INSTRUMENTATION gap, not a fixture or provider gap |

  I read `last_consolidated` out of the store rather than trusting the route's `{"ok": true}` — the
  reply is not evidence that anything moved, and the stats endpoint I checked first showed no change
  at all, which would have read as a silent no-op.

  **Then the prompt-side group, five more cells, in two turns.**

  | cell | verdict | evidence |
  |---|---|---|
  | agent-profile system prompt | **CONFIRMED** | `O32` — the marker line quoted verbatim, and the token emitted at the top of the reply: it OBEYED the profile prompt |
  | knowledge @-mention | **CONFIRMED** | `O32` — the `ZANZIBAR-8821` sentence quoted verbatim from the linked item |
  | attachment/paste | **CONFIRMED** | `O32` — `ATTACHMENT-CONTENT-MARKER-A77` quoted verbatim from `meta.files` |
  | Lumon persona injection | **CONFIRMED** (+ a finding) | `O33` — both persona lines reached the CLI; it quoted both and called the clash *"a genuine conflict"* (`G45`) |
  | `@prompt` expansion | **ABSENT**, decided | `O34` — `/render` returns the body, the persisted message keeps the literal `@aap1-prompt`, the CLI replied `ABSENT` |

  Two method notes. The `@prompt` cell was driven **twice**: my first fixture's prompt body was empty
  (the create route 409s on an existing name, so `content` never landed), which would have produced a
  vacuously-ABSENT answer. `PUT` + `/render` established the body server-side first, so the second run
  proves the strong form — the body exists and still never reaches the CLI. And `O33`'s conflict was
  not something I probed for: the CLI volunteered it, which is the kind of finding a six-quote probe
  buys that six separate probes would not.

  **Two bookkeeping defects found while closing the count, both in work I had just written.**
  (a) My earlier commit on this branch updated the residual PROSE for `O28`/`O29`/`O30` but left the
  three matrix ROWS reading `NOT-EXERCISED` — so the plan asserted 18 in one place and 21 in another.
  I now count the marks mechanically off the rows (43 CONFIRMED / 7 DIVERGED / 13 NOT-EXERCISED = 63)
  instead of maintaining a number beside them. (b) Re-deriving the grouping that way exposed two
  errors in the ORIGINAL 22-cell list that had cancelled out in its total: it counted the
  failure-breaker's *loop half* as a cell — it is a sub-clause of a row `O24` already decided — and it
  omitted *incognito/restricted no-write*, a real unexercised row. A grouping maintained beside the
  rows instead of derived from them is exactly the shape that hides this.

  **Residual: 22 → 13**, now composed as 1 + 4 + 5 + 3. Group 1 = **1** (skill-ladder — instrumentation,
  `G44`). Group 2 = **4** (per-agent approval floor `K36`, PreToolUse hooks `K39`, the other five hook
  kinds `K40`, incognito `K33`) — every one with a proven recipe. Groups 3 and 4 are untouched at 5 and
  3. `docs/agents/acp-parity.md` was updated in the same commit: its coverage table, its "not a complete
  column" caveat, the shared `@prompt` constraint row (`O34` is the *stronger* form of kiro's `K31` —
  the body was proven server-side before the turn), seven new at-parity rows, and the same corrected
  grouping. **The atom does not close on 13 cells**, and `dag.json` stays untouched.

### 2026-08-19 — `AAP-3` PARTIAL: the kiro tool axis reproduces, and its residual is 2 → 1

**Started from my own blocking claim** at the top of this log: *"The kiro column's tool rows do NOT
reproduce, so `AAP-3` cannot be certified."* Re-drove it on a fresh isolated home
(`/private/tmp/aap3-home`, gateway `:10051`, kiro bundle installed from a local Store source,
`kiro_default` bound), after checking the clause the atom asks for first — **mwinit was fresh**
(`~/.midway/cookie` stamped earlier the same day) and every kiro call succeeded, so nothing here is
`ENV`.

| cell / claim | before | after | evidence |
|---|---|---|---|
| tool axis reproducibility | "does NOT reproduce; `AAP-3` cannot be certified" | **reproduces** | `K51` — ~150 tool names, kiro's own plus the operator's twelve MCP servers plus the whole `personalclaw-core` surface |
| `pwd` confinement (the note called it unresolvable) | unresolved | **confined** | `K51` — `pwd` = the session's `workspace_dir`; `K28` reproduces and `G39`/#1729/#1734 hold for kiro |
| why the earlier drive said `NO_TOOLS` | cause "not established" | **gate artifact** (best-supported) | `K52` — the first tool call raises a card and PARKS the turn; kiro's wording was *"in this turn"* |
| Full native tool registry | CONFIRMED as ABSENT | **DIVERGED — reachable** | `K53` — the ABSENT verdict scored two names (`knowledge_search`, `task_create`) that exist in no registry |
| Empty-turn auto-retry | NOT-EXERCISED, "needs stream injection" | **CONFIRMED** | `K55` — a prompt demanding zero characters; duplicated user row = the silent re-queue, then the card |
| Skill-ladder review | NOT-EXERCISED, "no forced-run surface" (`G44`) | **still NOT-EXERCISED, new reason** (`G47`) | `K56` — the gate is drivable and the ladder runs; a 60,010 ms `provider_error` is DEBUG-only, and no surface attributes a call to its caller |
| §2.1 prong B config seeding | published as landed | **inert, and premise false** (`G46`) | `K54` — no bundle passes `agent_config_dir`; no receipt, nothing in `~/.kiro/agents/`; and kiro honours protocol-passed `mcpServers` anyway |

**A second wave, because the census forced it.** Correcting one row on the strength of a 151-name
enumeration meant every OTHER row scored off `K4`'s tool list was suspect. Four were: the native
registry, both skills rows and subagents. So I stopped correcting rows and CALLED the tools instead —
one turn, four real calls, six approval cards (`K57`): `skill_search`, `artifact_save` and `knowledge`
all executed; `subagent_run` failed with `<urlopen error [Errno 61] Connection refused>`.

**That failure was a host defect, and it is FIXED in this commit** (`K58`, campaign doctrine: incidental
in-session bugs get fixed). `mcp_core._resolve_api_base()` derives the gateway's API base from
`dashboard.url` and falls back to **10000**; neither `--port` nor the `--port auto` that `--test-mode`
uses writes that config, and nothing exported the bound port — so a gateway on any other port spawned an
MCP server that POSTed where nothing listened, and every HTTP-bridged core tool returned its raw
`urlopen` error as result text while the in-process tools beside it worked. The gateway now exports
`PERSONALCLAW_PORT` after binding (both the dashboard and the API-only path) and `core_mcp_servers()`
declares it in the child's env. **Driven before/after on the same home and CLI:**
`1 task(s) queued (at capacity): … Connection refused` → `Spawned 1 subagent(s) … 6c6039b3`. Four tests,
each falsified by mutating the live line and observing the specific red.

Two further findings fell out of those calls (`K59`): an artifact saved by the CLI on a project-bound
session persists `project_id: ""` **and** `session_id: ""` (`G48`), and the spawn resolver logs an
**empty** originating session, which is precisely the inject-back precondition (`G49`) — the bundled
`subagent-orchestration` snippet meanwhile tells the agent to *"just wait"* for a completion event.

**Marks (counted off the rows, not carried): 43 CONFIRMED / 18 DIVERGED / 1 ENV / 1 NOT-EXERCISED =
63.** One row was rendering as five cells because it never had a closing pipe; fixed in passing.

**Three lessons worth more than the cells.** First, *a probe that reads a tool list while its own first
call is pending measures the gate, not the registry* — three sweeps of tool rows rest on turns whose
approval decisions were never recorded next to the enumeration. Second, *"unreachable by construction"
deserves one cheap attempt before it is written down*: the empty-turn cell was declared out of reach
for three sweeps and fell to a one-sentence prompt. Third, *one bad instrument contaminates every row
that read it* — `K4`'s enumeration produced four ABSENT/PARTIAL verdicts, and correcting only the row I
happened to notice would have left three wrong. When a measurement is replaced, re-score everything
that cited it.

**`AAP-3` stays `todo`** on one unexercised cell (skill-ladder) and one `ENV` cell (OS sandbox — the
host reports no sandbox on this platform, `K47`), because its `done_when` asks for every cell
CONFIRMED-or-DIVERGED. What closes it is now small and named: land `G47`'s attribution field or INFO
line, then re-drive a single correction turn. `dag.json` is untouched — the atom does not flip, and
PR #1772 is concurrently editing that file.

**Incidental, recorded not fixed** (the gateway-port defect above WAS fixed; these two are outside
this atom's reach): the ollama bundle's
timeout defect is `PersonalClawApps#47`, **merged upstream** but absent from this machine's clone,
which is 19 commits behind — so a local-only install still silently loses every background pass
longer than 60 s. And `POST /api/model-providers` accepted a body with `settings` (the route reads
`options`) with `{"ok": true}` and dropped it: an unknown key on a config-writing route should be a
400, not a silent no-op.

### 2026-08-21 — `AAP-1` READ-ONLY AUDIT: the atom is SHORT by 13 cells, and the "zero UNKNOWN" half is MET

A repository-only audit of `AAP-1` against its own `done_when`, run without driving any CLI (the atom
is owner-gated on authenticated CLIs; spawning `claude` spends the owner's authentication). No code was
read for the purpose of *predicting* a cell — every claim below is a file:line or a commit. Audited at
`origin/main` = `e25a6ffa`.

**Verdict: the atom does NOT close. Its clause splits, and the two halves disagree.**

| `done_when` clause | status | evidence |
|---|---|---|
| "zero UNKNOWN cells" | **MET** | zero rows in the claude-code matrix carry `UNKNOWN` in the *mark* column; all 25 `UNKNOWN` string hits in this file are prior-column values, prose, or legend (table below) |
| "every audit cell re-marked CONFIRMED or DIVERGED at runtime" | **UNMET** | 13 of 63 rows are `NOT-EXERCISED`, a third mark the legend at line 198 defines and the clause does not admit |
| "checked-in verified matrix column for `claude-code`" | **MET** | §"Phase 1 results — claude-code verified matrix" line 179; 63 rows across §4a-4e |
| "findings entered in the severity-ranked (P0/P1/P2/P3) gap inventory" | **MET** | §"Gap inventory — severity-ranked (claude-code findings)" line 383; all four tiers present; `G1`-`G16` filed |
| "incidental in-session bugs fixed per campaign doctrine" | **MET (vacuously)** | line 478 declares **None**, and both AAP-1 commits are docs-only — nothing was claimed, so nothing can be missing |

This is not a new finding so much as a confirmation: the 2026-08-19 residual entry above already wrote
**"The atom does not close on 13 cells"**. The audit's contribution is that the count and the marks are
now verified mechanically rather than read, and that the *reason* the atom reads as "maybe done" is
identified — the `done_when` names two bars and only the narrower one is satisfied.

**1. The 25 `UNKNOWN` hits, classified per line.** A count alone cannot separate a live cell from prose,
and the matrix header is `| Feature | audit said | mark | runtime verdict | evidence |` — so `UNKNOWN`
in a row is the *pre-runtime prediction*, never the mark. The legend at line 198 says so explicitly:
"For the audit's `UNKNOWN` cells the prediction is the §5 gap text and the mark records the now-definite
verdict."

| line | classification |
|---|---|
| 46 | methodology prose — Phase 1's validation-first directive |
| 66 | methodology prose — the deliverable definition ("UNKNOWN cells … become definite") |
| 169 | methodology prose — Success Criteria §1 |
| 198 | **legend** — the Marks vocabulary |
| 286, 294, 295 | **claude-code**, prior "audit said" column (registry / AskUserQuestion / subagents); marks are `CONFIRMED` |
| 319 | **claude-code**, prior column as a parenthetical (`PARTIAL (UNKNOWN which backends emit)`); mark is `DIVERGED` |
| 343, 346 | prose — the claude-code self-assessment ("All four … are now definite"; "PARTIAL against `AAP-1`'s …") |
| 595, 603, 604, 628 | **codex (`AAP-2`)**, prior column — a different provider's atom |
| 649 | prose — codex self-assessment |
| 794 | prose — a codex-inventory methodology note addressed to `AAP-3` |
| 932, 940, 941, 965 | **kiro-cli (`AAP-3`)**, prior column — a different provider's atom |
| 1007 | prose — kiro self-assessment |
| 1239 | prose — kiro inventory, harness-need note |
| 1355, 1369, 1397 | prose — the `AAP-1` / `AAP-2` / `AAP-3` execution-log entries |

**Live `UNKNOWN` cells in the claude-code column: 0.** Live `UNKNOWN` cells in any provider's column: 0.
Twelve of the 25 are other atoms' rows or other atoms' prose; thirteen are this file's own methodology.

**2. The column is complete in shape, and honest.** Counted mechanically off the rows (not from the
prose beside them): **63 rows, 43 `CONFIRMED` / 7 `DIVERGED` / 13 `NOT-EXERCISED`** — exactly the
"after the re-drive" column of the mark-counts table at line 332, so that table is derived, not
maintained. Every `CONFIRMED`/`DIVERGED` row cites at least one `O`-id; **zero bare marks**. The ledger
defines `O1`-`O34` (lines 205-242) and the matrix cites 32 of them with **zero dangling citations**.
The 13 `NOT-EXERCISED` rows each state a reason and are grouped 1 + 4 + 5 + 3 at line 349.

**3. The gap inventory holds the claude-code findings.** Line 383, tiers at `**P0 — safety**` /
`**P1 — capability-dead**` / `**P2 — fidelity**` / `**P3 — cosmetic / legibility**`, carrying `G1`-`G16`.
One placement defect: **`G45` (two persona injection sites, `O33`) is filed under the
"Incidental bugs fixed in-session" heading at line 487, not under `**P3**`**, though its own text
declares "Severity **P3**". It is a claude-code finding from the re-drive appended to the wrong section.
A reader scanning the P3 tier does not see it. Cheap to move; does not change the verdict either way.

**4. No recorded incidental fix is missing from `main`, because none was recorded.** Line 478 says
**"None."** and names why (every defect landed outside the atom's fence). Verified against history
rather than the claim: `a29fcef9` ("AAP-1 claude-code end-to-end validation sweep") is
**+298 lines, this file only**; `19c97db2` ("AAP-1 residual 22 -> 13") touches only this file,
`docs/agents/acp-parity.md` and `docs/roadmap/atomic/dag.json`. Zero source files in either. So the
clause is satisfied vacuously — there is no recorded-but-unlanded fix, which is the failure mode that
would have mattered most.

Two bookkeeping notes found while checking this. (a) `19c97db2`'s log line says "`dag.json` stays
untouched", but the commit *does* edit it — it flips the **plan-level** `ACP-AGENT-PARITY` status
`todo` → `in_progress`, not the `AAP-1` atom row. The claim is true of the atom and false of the file.
(b) `AAP-1`'s atom row in `docs/roadmap/atomic/dag.json` reads `status: "todo"` on `e25a6ffa` today, so
the mirrored surface already agrees with this audit; nothing needs flipping, and this audit touches
neither the file nor the row.

**5. Staleness: the column is NOT wholesale stale, but seven merges land inside it — and one is a
provenance defect, not a staleness one.**

The header at line 183 declares a single host SHA, `b01cb76e`. That is accurate for `O1`-`O26` and
**wrong for `O27`-`O34`**: those eight observations are the 2026-08-19 re-drive, and `O27` measures
`provider_bridge.py` re-injecting `unattended` for the ACP branch — which is `8091f285`
("AAP-6 thread unattended sessions"), merged 2026-08-18, *after* `b01cb76e`. The re-drive commit did not
update the header (verified: `git show 19c97db2` touches no `Host:`/`Swept:` line). **So this is a
mixed-SHA column presented as a single-SHA one.** Fixing the header is a one-line edit and would make
the rest of this section unnecessary for future readers.

Seven commits in `b01cb76e..e25a6ffa` touch the ACP surface: `56696462` (`AAP-5` host permission
authority), `7be677fb` (`AAP-4` MCP reachability), `8091f285` (`AAP-6` unattended + loop breaker),
`b6d54e28` (`G39` profile-bound session spawned in the real home), `6c54c4c1` (refuse a concurrent spawn
with no workspace), `ada342f6` (the gateway port never reached its MCP children), plus `ddc5eb20`
(`fix(chat)`: a mistyped workspace-dir key silently unbound the session).

**Re-verification candidates, named and reasoned — not "the column is stale".**

- **The nine rows whose evidence is `O4`** ("no `mcp__personalclaw*` server"). `O4`'s finding was a
  *reachability* finding, and `AAP-4` landed `src/personalclaw/acp/mcp_servers.py`, whose own docstring
  says "Before this module every live `session/new` sent `"mcpServers": []`, so an ACP session had none
  of it — the single largest capability cliff in the ACP parity audit (gap 1)". Highest-confidence
  flips: **full native tool registry** (line 286, `ABSENT`) and **subagents** (line 295, `ABSENT` — both
  halves are addressed, the tool by the server and the inject-back by the explicitly-declared
  `PERSONALCLAW_SESSION_KEY`, `mcp_servers.py:34-40`). Also **skills index / `skill_invoke`**,
  **`skill_remember`**, **`project_id` → artifact stamping** (`artifact_save` was "not reachable"),
  **`tool_search`/`tool_schema`**, and **MCP tools (external servers)**, whose verdict text — "the
  subset is the OPERATOR'S, not PersonalClaw's" — is the exact sentence `AAP-4` was written to falsify.
  **AskUserQuestion** (line 294) is a weaker candidate: one of its two prongs (the CLI's own tool list)
  is untouched, so it flips only if `personalclaw-core` supplies such a tool.
- **Tool-disable prefs** (`O30`, `{"error": "server 'personalclaw-core' not found"}`). The named reason
  was that `personalclaw-core` is not a configured server; `AAP-4` makes it one for ACP sessions. Note
  this row was decided in the *re-drive*, and still on the pre-`ada342f6` tree.
- **The §4b approvals rows.** `56696462` added `src/personalclaw/acp/permission_authority.py` (+307)
  and +203 in `dashboard/chat_runner.py`. `AAP-1`'s own log says "`AAP-5`'s job is to make that coverage
  structural rather than to build a new gate" — so `G2` (host gate contingent on the operator's
  `~/.claude`) and the `tool_kind: ""` observation (`O5`, and `translate.py` +11 in the same commit) are
  candidates. `G2` in particular was filed as *contingent*; it should now be re-scored as structural.
- **The cwd rows** (`O8`/`O17`, "NOT cwd-confined"), i.e. the P0 `G1`. **`G1`'s code defect is fixed on
  `main`** and the inventory text saying "**Not fixed here — outside this atom's fence**" now reads as
  open when it is not: `src/personalclaw/llm/acp_agent.py:826` reads
  `str(kwargs.get("cwd") or "").strip() or options.get("cwd")` — the per-call kwarg the audit found
  dropped — and `src/personalclaw/acp/client.py:137` records that the hardcoded
  `Path.home()/".personalclaw"/"workspace"` fallback was replaced. `b6d54e28` and `6c54c4c1` are the
  landings. This is the most misleading row in the file today.
- **`S2`-cited observations.** `S2` is defined at line 204 as "workspace-dir set *before* binding", and
  `ddc5eb20` fixed a mistyped workspace-dir key that "silently unbound the session". Any `S2` row is
  suspect for the mechanical reason that the binding it was exercising may not have taken.
- **Explicitly NOT a candidate: context-% accounting** (line 319, `DIVERGED`, fabricated `0%`). The
  *consumer* churned — `dashboard/chat_runner.py:3713` and `:3812` are inside `AAP-5`/`AAP-6`'s diffs —
  but the *producer* did not: `src/personalclaw/acp/session.py:418` still returns
  `self.last_prompt_stats.context_pct`, and `git log b01cb76e..origin/main -- src/personalclaw/acp/session.py`
  is **empty**. The number's ACP source is untouched, so the `DIVERGED` mark stands. Recording this
  because the naive read (chat_runner churned, therefore the chip is stale) is wrong.
- **Also not candidates: the 13 `NOT-EXERCISED` rows.** Staleness does not apply to a cell that was
  never marked; they are simply open. Likewise `O27`-`O34` were driven post-`AAP-6` and are the
  *freshest* rows in the column despite the header's SHA.

**What actually remains, and what needs the owner.** 13 cells, composed 1 + 4 + 5 + 3 at line 349:
skill-ladder review (instrumentation, `G44` — provider-independent); per-agent approval floor,
PreToolUse hooks, the other five hook kinds, incognito no-write (fixtures, and **all four have a proven
recipe** — `K36`/`K39`/`K40`/`K33`); queued messages, queue-steering, cancelled-turn preamble,
empty-turn auto-retry, pipe-death auto-retry (timing/failure injection); dry-run replay, OS sandbox
confinement, trust/YOLO auto-approve (no as-a-user entry point).

**Of these, 12 cannot be settled from the repository — they require a live authenticated `claude`
drive, which is the owner gate.** Reporting that as a finding rather than performing it. The one
exception is **skill-ladder review** — but ⚠️ **this paragraph's original diagnosis was itself stale
and is corrected here.** It read "its blocker is a missing forced-run surface (`O31`)". That is `G44`,
and **`G44` is already superseded by `G47` at line 694 of this same file** (`K56`, the 2026-08-19
re-drive): the gate is in fact a documented pair of drivable conditions (a correction signal OR >= 4
tool calls), the ladder DOES run, and `skill_promotion.promote()` — the forced-run surface — has existed
all along, reachable as the `_skill_promote` MCP tool (`mcp_core.py:1666`), which AAP-4's
`acp/mcp_servers.py` now renders into every `session/new` where the pre-AAP-4 wire sent
`"mcpServers": []`. Verified against `origin/main`, not read. **The real blocker is ATTRIBUTION**
(`G47`, P2): `model_calls.jsonl` and `/api/models/telemetry` key on `(use_case, query_class)`, the
ladder's success path broadcasts a transient WS chip and logs nothing, and its failure path is
`logger.debug` — so a pass that timed out at exactly 60,010 ms was invisible at the default log level,
and a completed pass cannot be distinguished from any other `background` caller. `G47` names the close:
a caller/subsystem field on the ledger row, and an INFO line carrying the ladder's verdict. Still
zero-drive and still provider-independent, so it also serves `AAP-3`'s identical residual cell — but it
is a telemetry fix, not the instrumentation-build this paragraph first described.

This correction is itself an instance of the defect this audit exists to catch: an earlier statement in
a long document, contradicted by a later entry in the same document, repeated forward as current. **Two zero-drive edits are also available now and would remove the two most misleading
statements in the column:** correct the line-183 host SHA to name the re-drive's tree for `O27`-`O34`,
and move `G45` from line 487 into the `**P3**` tier.

**No `dag.json` change, no code change, no push.** The atom stays `todo` on the record that already
said so; this entry only makes the reason auditable.
### `G47` closed in code — what a background pass now costs to observe (2026-08-21)

**DONE — `G47` (P2).** Both halves landed. A guarded model attempt now carries the SUBSYSTEM that
asked for it, and a skill-ladder pass reports its own verdict at a level a default install shows.

**The caller vocabulary already half-existed, and none of the three candidates could carry this.**
Checked before minting anything: `routing/policy.py:457`'s `caller="user"` is the **SEL's** actor
field (`sel.log_api_access`, stored as `caller_identity`) — "who invoked this API", values like
`user` / `system` / `autonomy` / a remote address, on a security record, not on the attempt row.
`model_call.py` already passes `caller=f"model_call:{use_case}"` into that same SEL call, which is
the axis again, not the asker. `usage_ledger.TurnUsage.source` and `routing.usage.PURPOSES` are the
TURN ledger's axes (one record removed from an attempt), and `PURPOSES` is deliberately coarse —
**every** value we need maps to its single `background` purpose, which is precisely the collapse
`G47` reports. So this is the FIRST vocabulary for "which subsystem asked", not a second, and it is
**closed**: `guardrails/audit.CALLERS` is the one definition, `set_current_caller` raises
`ValueError` on an unlisted value (`routing.policy.set_mode`'s posture), and `record_attempt` drops
an out-of-vocabulary caller to `""` with a WARNING so a hand-built record cannot smuggle a fifth
spelling into the file either. Four members, each with a live binder: `skill_ladder`,
`inbox_triage`, `nl_to_cron`, `conflict_merge` — a ratchet test fails if any member has no
production `caller_scope("…")` call site.

**Writer → reader, both live.** Written at `guardrails/model_call.py:472` (`_audit`, the constructor
of every `AttemptRecord` the guard writes) from a ContextVar, for the reason `_CURRENT_RUN_KEY` is
one: the guard is built by `provider_bridge` from provider config and never sees its caller, and
threading a parameter would touch the same 33 bridge call sites S153 measured. Read at
`guardrails/health.py:_caller_rollup` → `GET /api/models/health` → the Guardrails settings panel's
`CallerRow`. The read model `G47` names — `routing/telemetry.py`'s `(use_case, query_class)` keying —
was **deliberately not touched**: filtering its rows by caller would mix a fold that has no caller
dimension with a JSONL tail that does, producing rows whose `n` counts every caller and whose p50
counts one. Additive elsewhere instead.

**DEVIATION from the gap's prescription: the line is not INFO-only, and an INFO-only line would
have shipped INERT.** Measured while wiring it: the shipped default log level is **WARNING**, not
INFO — `AgentConfig.log_level` defaults to `"WARNING"` (`config/loader.py:786`) and `cli.py:966`
applies it whenever `--verbose` is absent. So `G47`'s literal "an INFO line" would still have been
invisible on a default install, which is the exact property it reports. Instead every ladder pass
emits **exactly one** terminal line: WARNING for the verdicts that mean the pass produced nothing
and the money is spent (`provider_error`, `unparsable`, `incomplete_decision`, `enqueue_failed`,
`internal_error`), INFO for the verdicts that mean it worked — including `no_action`, the common
one. Spam bound: one line per pass, and a pass only runs on a learning-worthy turn
(`learning_decision_for_turn` + the `skill_ladder` cadence flag), so a healthy install is silent at
the default level and a dead provider warns once per gated turn. An unmapped verdict logs at
WARNING, deliberately — an unrecognised outcome must get louder, not quieter.

**The pass had EIGHT silent exits, not one.** The gap named the DEBUG failure path; the dominant
`action == "none"` exit logged nothing at all, so a live ladder and a ladder that was never
scheduled were the same observation (none). `run_skill_ladder_review` is now a wrapper that binds
the caller scope and owns the single log line; the body moved to `_ladder_pass`, which returns
`(verdict, detail, summary)` so no exit is nameless. The line is emitted from a `finally`, so an
exception escaping the pass is still reported as a verdict.

**What it costs to observe now.** A silently-dead learning pass produces (1) one WARNING per gated
turn naming the verdict, the elapsed ms and the session — the measured 60,010 ms timeout reads as
`skill-ladder review: provider_error in 60010 ms (session=…) — TimeoutError` at the shipped level,
where before it was a DEBUG line; and (2) a `skill_ladder` row on `GET /api/models/health` and in
Settings → Guardrails → Provider health reading `6 calls · 0% ok · provider error`. Neither existed
before. The per-provider rollup could not show this by construction: with one caller healthy and
one dead the provider row reads `50% ok`, which is asserted directly in
`tests/test_model_call_attribution.py` so the two views cannot be conflated later.

**Falsifications (mutate the live line, confirm the enclosing function by AST, restore from a file
copy).** (a) `caller=current_caller()` → `caller=""` in `_audit` (line 472, enclosed by `_audit`
436-484): 2 tests red with `KeyError: 'inbox_triage'` — both callers collapse into
`(unattributed)`, i.e. the red is on *telling callers apart*, not on the field existing. (b) the
verdict line's level → `logging.DEBUG` (enclosed by `_log_ladder_verdict`): 3 tests red with
`exactly one line per pass, got []` — the red is on *invisibility at the shipped default level*,
which the test reads from `AgentConfig.log_level`'s default rather than hardcoding. (c) the closed-set
check removed from `set_current_caller`: `DID NOT RAISE ValueError`. (d) one binder's literal
changed to `""`: the census ratchet reds with `unbound caller(s): ['nl_to_cron']`, so it is not
vacuous.

**The seven other `proposals.enqueue` callers: mostly a non-question, and it was already answered.**
`learning.proposals.enqueue` takes `source_cadence`, and every one of those seven passes its own
(`skill_promotion`, `outcome_resolver`, `consumer_liveness`, `refiner_tools`, `self_model_observer`,
`attribution`, `project_context_review`) — so on the PROPOSAL record, callers were already
distinguishable and no new field was needed. The unanswered half was only ever the MODEL-CALL
ledger, and of those seven only the skill ladder makes a model call at all (`skill_promotion` is
explicitly "deterministic, no model here"). Widening the vocabulary to name subsystems that make no
guarded call would have declared members nothing binds.

**Cells NOT flipped.** The skill-ladder review row stays **`NOT-EXERCISED`** in both `AAP-1` (`O31`)
and `AAP-3` (`K56`): this change makes the cell *measurable*, it does not measure it. Confirming it
needs an owner-gated live drive of an authenticated CLI (one correction turn, then read the
`skill_ladder` row on `/api/models/health` and the verdict line in `gateway.log`), which is
deliberately out of a code-only session's reach. `dag.json` untouched; no matrix cell changed.

**Gates.** `make lint` clean (mypy 958 files); targeted pytest 300+ green across
telemetry/routing/stats/usage, guardrails model-call/query-class/profiles, after-turn-review,
skill-promotion, learning-proposals, inbox-service, nl-to-cron, durability-conflicts and the
inert-surface baseline; `make test` full suite; `gate_report.py` **6/6**; flat wire-error census
**1507/1507** (no new flat envelope — nothing here adds an error path); web typecheck, `test:web`
and build. One pre-existing test updated, not weakened: `test_health_empty` asserted the exact
`provider_health()` dict, so it now names the new empty `callers` list.
### 2026-08-21 — `G8` closed: the context-% chip stops stating a number it was never given

**DONE (code-only; no CLI drive, no authenticated session).** `G8`/`O7`: a `context_usage` frame was
emitted every turn with `pct: 0.0` and `Turn complete: … context 0%` printed on all ~14 audited turns,
including ones carrying 18 KB of injected context. The root cause was representational, not a bad
computation: **the producer could not express "unknown".** `AcpPromptStats.context_pct` and
`AgentEvent.context_usage_pct` were bare defaulted `float`s, so "the adapter told us nothing" and "the
context is genuinely empty" were the same value — and every consumer rendered the second.

**Shape (following `local_models/fit.py`'s precedent):** the measurement is now `float | None` end to
end, `None` = unmeasured. `fit.py` needs BOTH explicit `memory_measured`/`disk_measured` flags AND
`None` on its derived values, because its *collector* aggregates several independently-measurable facts
into one dataclass — one flag per fact is the only way to say "memory known, disk not". Here there is
exactly one fact travelling five hops (ACP metadata frame → `AcpPromptStats` → `AcpEvent` →
`AgentEvent` → provider getter → WS frame → chip), so a parallel bool would have to be threaded through
every hop and could desync from the number it describes. `None` on the one field is the whole answer,
matching `fit.py`'s own derived shape (`budget_bytes: int | None`, `usable_memory_bytes() -> int | None`).

**Producers** — `acp/types.py` (`AcpPromptStats.context_pct`, `AcpEvent.context_usage_pct`),
`acp/session.py:context_usage_pct`, `llm/events.py:AgentEvent.context_usage_pct`,
`llm/base.py:context_usage_pct` (the contract), `llm/acp_agent.py`, `llm/acp_session_provider.py`,
`guardrails/model_call.py`, `agents/provider.py` (default was `0.0`, now `None`).
`acp/translate.py:extract_context_pct` already returned `float | None` — the wire decoder could always
say "absent"; the collapse was one layer in. `llm/openai.py` and `llm/anthropic.py` report `None` until
their first usage report and are otherwise untouched (their real arithmetic is unchanged).

**Consumers, all now omitting rather than printing zero** — `dashboard/chat_runner.py:506`
`_turn_complete_line` (the observed surface: the whole `, context N%` fragment is dropped),
`chat_runner.py:3713`/`:3812` (WS `context_usage` broadcasts `pct: null`), `dashboard/state.py:933`
(post-compaction broadcast was a hardcoded `0.0`), `session.py:context_info` (`/api/sessions/context`
emits JSON `null`), `session.py:check_context_usage` (unknown neither compacts nor logs a percentage),
`cli_chat.py:98`, and `web/src/pages/ChatPage.tsx` + `ui/composer/controls.tsx`.

**Two decisions, made deliberately.**

1. **The cross-turn carry at `session.py:306-307` is KEPT**, and now carries `None` as faithfully as it
   carries a number. It exists because the metadata frame is per-adapter and optional: an adapter that
   reports once and goes quiet should keep showing its last real measurement rather than dropping the
   chip mid-conversation. What changed is that the carry no longer *manufactures* a starting value —
   propagating `None` from a session that was never measured is exactly the ~14-turn case, and it now
   propagates as "unknown" instead of as "0%".
2. **The frontend's `contextPct > 0` guard is now REDUNDANT AND WRONG, so it was removed** rather than
   left as a second guard. It was load-bearing while Python could not say "unknown" — it was the only
   thing hiding the fabricated ring. Once absence is expressible as `undefined`, `> 0` becomes the
   *inverse* defect: it folds a legitimately empty context into "unmeasured" and hides a real answer.
   Falsified as such (MUTANT-D below).

**Two inverse-direction bugs found and fixed in passing**, both instances of the sentinel collision this
gap is about: `session.py:_maybe_recycle_background` keyed its blind fallback on `pct == 0.0`, so a
background session measured at a genuine 0% was recycled as though no reading had ever arrived; and
`agents/native/runtime.py:796` gated its gauge update on `if usage.context_usage_pct:` (truthiness), so
a provider reporting a real 0% was ignored.

**Falsifications — five mutations, each applied to the LIVE statement, re-read after writing, and
AST-probed for its enclosing function before running anything** (a mutation that lands in a docstring
reds nothing and reads as "unenforced"). All restored from `cp` file copies, never `git checkout`.

| # | Mutation | AST-confirmed site | Observed red |
|---|---|---|---|
| A | unknown falls back to `0.0` (`round(context_pct or 0.0)`) | live `AugAssign`, `_turn_complete_line` (487-519) | 3 failed — `assert 'context' not in 'Turn complete: …, context 0%'` **on the rendered line**, plus both disagreement assertions |
| B | a measured `0.0` folded into unknown (`if context_pct:`) | live `If`, `_turn_complete_line` (487-520) | 3 failed — `assert 'context 0%' in 'Turn complete: 12 events, 3 tool calls'`, plus both disagreement assertions |
| C | blind fallback keyed on `not pct` | live `If`, `recycle_background` (934-972) | `test_measured_zero_is_not_blind` — "Expected shutdown to not have been awaited. Awaited 1 times." |
| D | frontend `> 0` guard restored | `controls.tsx:143` | 2 failed — measured-zero ring absent, and the markup-disagreement test |
| E | frontend renders `contextPct ?? 0` | `controls.tsx:143` | 2 failed — unmeasured states a percentage, and the markup-disagreement test |

Every pair is additionally asserted to **DISAGREE** in one assertion, as `fit.py`'s own test does — a
test that only checks `is None` passes just as happily when both cases collapse to the same output again.

**Tests:** `tests/test_context_pct_honesty.py` (13) and
`web/src/ui/composer/contextRingHonesty.test.tsx` (4), plus three existing tests corrected to the honest
contract (`test_acp_types` defaults, `test_agent_provider` stateless defaults,
`test_recycle_blind_fallback`) and three added to `test_session.py`.

**No matrix cell flipped and `dag.json` untouched.** The `O7` cell needs an owner-gated live drive to
re-mark; this change makes the surface honest rather than measuring it.

**DISCOVERY — one intermittent red I could NOT root-cause, recorded rather than papered over.**
`tests/test_subagent.py::TestSubagentReaper::test_reaper_kills_expired_subagent` and
`::test_a_failed_reaper_audit_write_raises` failed together in **2 of 5** full-suite runs on this
branch, and passed in the other 3; the base commit (`a692faa9`) was green in **3 of 3**. They pass in
isolation (7/7), and `tests/test_subagent.py` alone passes 3/3 under `-n 8 --dist worksteal`. Both
failures need a cross-file neighbour and only ever appeared at ~99% on one worker, immediately after
`test_subagent_persistence` / `test_subagent_validate` / `test_durability_sync_*` on that worker.
Narrowed but not solved: **the signature is `patch("personalclaw.subagent.sel")` not affecting the
lookup the executing `_force_reap` performs** — every other assertion in the same test passes (`info.done`,
`_running_count`, the batched `on_done`, the event ordering), so `_force_reap` ran to completion, yet
`mock_sel().log_tool_invocation` records **0 calls** and the sibling test's `pytest.raises(OSError)` sees
nothing raised. That is consistent with the REAL `sel()` running inside the `with patch(...)` block, i.e.
a module-identity / leaked-patch hazard in the test infrastructure, not a defect in the reaper. Ruled out:
a pytest 120 s timeout killing an earlier test mid-`with` (no timeouts in any run's log), and
`importlib.reload` of `personalclaw.subagent` (nothing reloads it). This diff touches none of
`subagent.py`, `sel.py`, or the reaper path, and the suite runs under `--dist worksteal` (timing-derived
scheduling), so the most likely reading is a pre-existing order-dependent isolation bug that this
change's timing shift exposes — but **3-of-3 green on the base is not enough runs to prove that at a
~40 % failure rate**, so it is NOT claimed as a known flake. Nothing was skipped, weakened, or
xfail-ed. Wants its own session with a deterministic ordering harness.

- 2026-08-21 — `AAP-9` **DONE** for `G4` (the slash-command half). Code-only; no CLI driven, no
  authenticated session, so nothing here flips a cell to CONFIRMED.
  **The defect was two unconditional lines, not a missing feature.** `chat_runner.py:2411` chose
  `client.stream_command(message)` for any `first_word in _SLASH_COMMANDS` with no capability check,
  and `acp/session.py` sent `_vendor.dev/commands/execute` with no guard. `acp/types.py`'s module
  docstring claimed such requests "fail gracefully (timeout or JSON-RPC error)" — `O23`/`C8`/`K11`
  falsify that: the error arrives on the turn's TERMINAL frame, so it fails the whole turn. That
  sentence is now corrected in place.
  **The gate follows the `loadSession` pattern, one key, one derivation.** `CAP_COMMANDS`
  (`_vendor.dev/commands`) is read off the `initialize` handshake by
  `AcpConnection.supports_native_commands`; `AcpClient` mirrors it into `_can_execute_commands`
  beside the existing `_can_load_session` line, and `AcpSessionProvider` reads the same connection
  property, so the N=1 and concurrent paths cannot disagree about one process. It is an ALLOWLIST:
  `O1`'s measured seven capability keys contain no command capability, so claude-code closes the
  gate and no frame is written. `ModelProvider.supports_native_commands` defaults False (the honest
  answer for every provider with no command axis), and `ModelCallGuard` needs an EXPLICIT
  pass-through — its `__getattr__` proxy only fires on a lookup miss, and the ABC property is not a
  miss, so without it the guard would answer False for a command-capable agent. The SDK-facing
  `agents/provider.py` `AgentProvider` declares the same defaulted property, so
  `AcpSessionProvider`'s answer is an OVERRIDE of a stated contract rather than an attribute one
  subclass happens to carry (additive and non-abstract, so `test_app_scaffold.py`'s
  `__abstractmethods__` rail is unaffected).
  **`-32601` before output and after output are different situations, and both are implemented.**
  `AcpMethodNotFound` is raised only for that code (reusing ONE definition —
  `constants.JSONRPC_METHOD_NOT_FOUND`, which `inbound/mcp_http.py` now re-exports and
  `mcp_shared.py` imports instead of a second literal). `stream_slash_command` substitutes a plain
  prompt only while the turn has yielded NOTHING; after any event it refuses with
  `AcpCommandFailedAfterOutput`, whose message is written for the chat bubble. Re-issuing there
  would append a second answer to the same assistant message, re-run any tool call that already
  ran, and bill the turn twice — a refusal that says so is the correct answer, not a papered one.
  Every other JSON-RPC error is untouched and still surfaces.
  **DISCOVERY — fixing the dispatch alone would have made `/compact` worse, not better.**
  `chat_runner.py:3719` has a post-turn deferred-compaction branch that fires for `first_word ==
  "/compact"`, WIPES the streamed chunks, and waits up to 120 s on `wait_for_compaction`. On a
  substituted turn there is no compaction coming, so ungated it traded `O23`'s error card for a
  stall ending in "Compaction timed out." with the answer just produced thrown away. It is now
  gated on the substitution signal, and the turn-level test reds on "no answer reached the user"
  when that gate is removed.
  **Visibility reuses CE2-8's channel, not a new one:** `activity_event {kind: "slash_fallback"}`,
  which `ActivityLine` renders inline with no frontend change (`web/` untouched, so no web gate).
  **Matrix mark corrected, with the row cited:**
  `docs/roadmap/research/acp-agent-parity-audit.md:140` (§4e, columns
  `Feature | native | claude-code | codex | kiro-cli | Evidence`) — the **claude-code** cell said
  `WIRED (protocol commands/execute)`, which was a code reading rather than a measurement; it now
  reads **ABSENT** with `O23` cited and the host's degrade named. The codex and kiro-cli cells in
  that same row stay `UNKNOWN` **deliberately**: `C8`/`K11` measured them, but they are outside this
  atom's claude-code fence — correcting them is one line of follow-up for whoever owns those
  columns. `docs/agents/acp-parity.md`'s shared row is updated too: the host half is DONE, the
  command still does not EXECUTE anywhere (that is upstream). No cell flipped to CONFIRMED;
  `dag.json` untouched.
  **NOT done, named rather than hidden:** the SEL row for a slash command is still written at
  `chat_runner.py:2030` as `slash_command` / `outcome: "bypass"` — before the provider is known, so
  it cannot tell a native command from a substitution. Fixing it means minting a new outcome word,
  and `sel.py:89`'s `AUDIT_OUTCOME_FAMILIES` + `test_audit_outcome_families.py` own that closed
  vocabulary, so it is a separate change rather than a silent widening here.
  **Gates.** `make lint` clean (mypy 959 files); targeted pytest 538 green across the new
  slash-fallback suite plus acp session / client / types / turn-scenarios / session-provider,
  dashboard chat + hooks, guardrails query-class and the MCP inbound/shared/core suites; `make test`
  full suite **run three times: green, green apart from ONE unrelated red, green** — run 2 failed
  `test_subagent.py::TestSpawnWithApprovalCallback::test_rejected_spawn_logs_sel_rejection`
  ("Expected 'log_tool_invocation' to be called once. Called 0 times"). Reported as a run count, not
  as a flake: it is a THIRD test in the same file with the same signature as the branch's open
  DISCOVERY (a `patch("personalclaw.subagent.sel")` that does not affect the executing lookup), it
  did not reproduce in 2 of 3 full runs here, and `test_subagent.py` alone is 66/66. Nothing in this
  change touches `subagent` or `sel`. `gate_report.py` **6/6**; flat wire-error census
  **1507/1507** (this change adds no error path — the two new errors are exceptions rendered through
  the existing chat error bubble).
  Two pre-existing harnesses updated, not weakened: `test_acp_turn_scenarios.py` and
  `test_acp_session_provider.py`'s `_FakeConn` now DECLARE the command capability their real
  counterparts would have captured — a fake that stayed silent would have been testing the refusal
  path while claiming to test the command turn.
### 2026-08-21 — `G5` audited claim-by-claim, then closed: two of four claims were open

**DONE (code-only; no CLI drive, no authenticated session, no owner authentication).** `G5`/`O16`
asked whether a gateway restart silently changes a session's runtime. The gap named four things; an
audit-first pass measured each one on `origin/main` before writing any code, and the result was
**two open, one already shipped, one open for a reason the gap had not identified**.

**Claim 1 — `acp_provider` cleared: OPEN, and the "already fixed" reading was the inert-reader trap.**
`chat_persistence.py:309-313` really does restore `acp_provider`/`acp_provider_agent` from the meta
line, which is what makes it look closed. Two independent defects sat under that read:
1. **The reader lived in the wrong path.** Line 309 is inside `_rehydrate_session_from_history`, the
   *targeted* single-session resume. A gateway restart goes through `restore_recent_sessions`
   (`dashboard/server.py:2060`), which read `reasoning_effort`, `workspace_dir` and `mode` and **never
   read `acp_provider` at all**. Two independent readers of one contract, one of them missing a field.
2. **The writer was clobbered on the next turn.** There IS a writer — the bind endpoint persists the
   binding with `ConversationLog.update_metadata` at `chat_handlers.py:1891-1902`, comment and all
   ("Persist so the ephemeral binding survives a gateway restart"). But `_save_session_to_history`
   **rebuilds the whole metadata line from the in-memory session** on every turn, carrying over only
   `created_at`/`last_consolidated`/`side` — and it did not write `acp_provider`. So the bind endpoint's
   write survived exactly until the end of the next turn and then vanished. `workflows/service.py:584`
   already documents this rewrite hazard in a comment; nothing had connected it to the ACP binding.
   Measured: after a real bind → turn → save, `meta` keys were
   `[created_at, last_consolidated, memory_mode, mode, model, reasoning_effort, tab_id, workspace_dir]`
   — no `acp_provider`. That is also why the bind endpoint's own `update_metadata` is a no-op for a
   session with no turns yet: `history.py:632` returns early when the JSONL does not exist.

**Claim 2 — `workspace_dir` cleared: ALREADY SHIPPED, and the gap's premise was wrong.** The writer
(`chat_persistence.py:566-567`) and readers in **both** restore paths (`:314`, `:448`) all date from
the initial public commit `1b4a2bdd`. A measured save→restart→restore round-trip returns it unchanged.
`O16`'s observation of `workspace_dir: ""` stands; the inference that the restart cleared it does not —
S3 was never given one. The `G5` bullet in P1 has been corrected to name two fields, not three, and the
same caution flagged for `K20`'s `reasoning_effort: null` (that field round-trips too). Railed anyway so
it cannot regress silently.

**Claim 3 — `task_mode` cleared: OPEN, exactly as suspected.** Neither writer nor reader existed
anywhere. `task_mode` lives on the private `session._task_mode` (`state.py:351`), not on the public
`session.mode` that the meta line already carried — so a grep for `mode` found the wrong field. A
restored session came back `agent` regardless of the posture it was in. Worth naming: the mode is **two**
writes (`chat_utils.apply_task_mode` — the session posture *and* `SessionManager.set_task_mode`, the
runtime's `_guard_and_invoke` gate), so restoring only the attribute would have produced a session the
UI labels "Plan" whose tools still run. The restore goes through `apply_task_mode`.

**Claim 4 — `resume_sid is None`: OPEN, for a reason the gap had not identified.** The load path is real
and correctly gated (`acp/client.py:502` under `_can_load_session` from `agentCapabilities.loadSession`),
and `SessionMap` really does persist to `session_map.json`. The failure is one level up, in
`SessionMap.get` (`session_map.py:160`): it returns a sid **only if
`$PERSONALCLAW_HOME/sessions/<sid>.json` exists**, and otherwise `_remove_entry`s the mapping and
returns `None`. Nothing in core ever writes that file — `subagent_persistence.py:258` only *deletes*
the pair, `session_workspace.py` uses `sessions/<id>/` as a directory, and the adapter writes it only
when the provider was built with `session_files_dir`, an **opt-in the app bundle has to pass**
(`llm/acp_agent.py:895`; provisioning it is already owned by the `dag.json` atom "§2.4 Resume — live
`session/load` via bundle `session_files_dir` (gap 6)"). Measured directly: `set()` then `get()` with no
session file returns `None` and leaves `session_map.json` as `{}`. That also resolves `O16`'s
contradiction — the sid it found (`2cb03780-…`) was a **fresh** one minted by `session.py:1262` after the
subsequent `session/new`, not the one the resume tried to use. So `resume_sid=None` was structurally
guaranteed on that home, and **unexplainable from the logs**, because that branch pruned in total
silence while its sibling (the empty-JSONL branch, `:167`) logged. It now logs the reason and the
directory it looked in. **Not otherwise fixed here** — provisioning `session_files_dir` is gap 6's scope,
not this atom's, and inventing a second writer would have been the wrong shape.

**The requirement that actually mattered.** Even with all three fields persisted, the residual harm in
`G5` is not the lost binding — it is a turn that runs with a **different tool set and different
confinement while looking completely normal**. So `_ChatSession` now carries `_acp_meta_binding`: what
the persisted meta line *asked* the runtime to be, recorded on restore whether or not the binding was
honoured, and consumed by the first turn after a restore. If that turn is not resolving on an `acp`
axis, it emits an `activity_event` naming the runtime it could not restore and saying the built-in agent
has different tools and different confinement. No new channel — `ActivityLine` renders any
`activityKind` outside `context`/`learned`/`stats` as an inline process step (`ChatPage.tsx:3632`), so
there is no `web/` change, following `G8`'s precedent of making the unmeasured case *representable*
rather than fabricated. It fires once, not per turn, and an explicit pick or clear through either
provider endpoint drops it — a user choosing an axis by hand is not a silent substitution.

**Coherence.** Both restore paths now call one `_restore_runtime_binding(state, session, meta)` helper,
because "two readers of one contract, one missing a field" is precisely how claim 1 shipped half-done.
A new test asserts the two paths return the same binding.

**FALSIFICATIONS — four mutants, each applied to the LIVE line, confirmed by re-read plus an AST probe
of the enclosing function, and restored from a file copy (never `git checkout`).**
1. Dropped `meta_line["acp_provider"]` in `_save_session_to_history` (AST: line 609 in
   `_save_session_to_history`, a `Call` statement). Red: `assert '' == 'acp:claude-agent-acp'` — the
   **restored binding being wrong after a simulated restart**, not a dict key being absent.
2. Replaced the `_restore_runtime_binding` call in `restore_recent_sessions` with `pass` (AST: line 483
   in `restore_recent_sessions`, a `Pass`). **5 red**, every one on restored state:
   `'' == 'acp:claude-agent-acp'`, `'agent' == 'plan'`, `'' == '/tmp/g5-ws'`, and the two-path agreement.
3. Made the un-restorable case silent (`if False:` over the broadcast; AST: line 1807 in `_run_chat`, an
   `If`). Red on the **silence**: "an un-restorable runtime binding resolved on the native axis in
   silence", plus the fires-once rail at `0 == 1`.
4. Vacuity floors: removing the reason from the `session_map` log reds the legibility assertion; making
   `get` return `None` with both session files present reds the floor test — so neither is vacuous.

**GATE.** `make lint` clean (black/isort/flake8 + mypy, 959 source files). Targeted: 14 suites, all
paths existence-checked first, **670 passed** (`test_acp_restart_binding.py` 13/13 plus the existing
acp-client / session-map / session-restore / dashboard-chat / task-modes / ephemeral-session suites).
`scripts/gate_report.py` **6/6**. Flat wire-error census **1507 = `FLAT_BASELINE`, zero slack,
unchanged** — this change adds no error path. No config field, so no round-trip and no baseline regen.
Nothing under the real `~/.personalclaw` was touched: every test uses `tmp_path` or a monkeypatched
`config_dir`/`PERSONALCLAW_HOME`.

**No matrix cell flipped and `dag.json` untouched** — re-marking the resume row needs an owner-gated live
drive. The one documentation change is the `G5` bullet's own factual correction (four fields → two),
cited above.

- 2026-08-21 — `AAP-8` **`G7` CLOSED (code): ACP turns now feed procedural memory. `G6` found still
  OPEN — its failure bit dies one line after it is authored.** Code-only session; no CLI driven, no
  authenticated `claude` session, so no matrix cell is flipped and `dag.json` is untouched.
  **`G7` root cause — confirmed as stated, and it was only the SECOND of two broken links.**
  `drain_tool_outcomes` was implemented on exactly ONE provider
  (`agents/native/runtime.py:1696`); `dashboard/chat_runner.py:238` reads it duck-typed
  (`getattr(provider, "drain_tool_outcomes", None)`), so on any `acp:*` provider the lookup returned
  `None`, `tool_outcomes` stayed `[]`, and `after_turn_review.record_procedural_outcomes` was handed
  nothing. Measured before the fix: 0 occurrences of the name in `llm/acp_agent.py`,
  `llm/acp_session_provider.py`, `agents/provider.py`, `llm/base.py`.
  **DISCOVERY — the first link, and it means `G6` is NOT closed.** `acp/translate.py:238` does stamp
  the failure bit (`_meta = {"ok": False}` on `status: "failed"`) exactly as `AAP-6` recorded — but
  `acp/adapter.py`'s `acp_event_to_agent_event`, documented as mapping the event "field-for-field",
  **omitted `tool_meta`**. Every downstream consumer therefore read the dataclass default `{}`.
  Runtime probe on `origin/main` (`05bba66e`): `AcpEvent(tool_meta={"ok": False})` →
  `AgentEvent.tool_meta == {}`. Consequences, both measured: the tool card's failure colour-coding
  never fires on ACP, and `chat_runner.py:2877`'s `_acp_failed = _tool_ok is False` can never be
  True — so the entire `_acp_breaker` path (`:1586` construction, `:1594` `_acp_breaker_aborted`,
  `:2877` `record`, then `WARN_THRESHOLD` / `BLOCK_THRESHOLD` / `circuit_tripped()`) is **live code
  that cannot execute**. `AAP-6`'s breaker is present but inert; `G6`'s "host acts" half is written
  and its "host is told" half is severed at the adapter.
  **Why the suite was green over it:** `tests/test_acp_unattended_and_loop_breaker.py` proves the
  translate half correctly (`:234`, `:241`, on `AcpEvent`) and then drives the breaker over
  **hand-built** `AgentEvent(..., tool_meta={"ok": False})` values (`:314`, `:536`, `:564`) — events
  the production path could not produce. Falsified: with the adapter mapping reverted, that whole
  file stays **green** while the new suite reds 6. A rail now exists
  (`TestFailureBitCrossesTheAdapter`, with a passing-call vacuity floor) so the bit cannot be dropped
  again silently.
  **What shipped.** `acp/adapter.py` maps `tool_meta` (one field; closes `G6`'s severed link and
  supplies `G7`'s only honest failure signal). New `acp/outcomes.py`: `ToolOutcomeAccumulator` +
  `AcpToolOutcomesMixin`, mixed into `AcpAgentProvider` and `AcpSessionProvider` so both the N=1
  client-backed and the pooled session-backed provider carry the hook.
  **Where the accumulator lives, and why:** on the **provider wrapper**, not `AcpClient`/`AcpSession`.
  Three reasons. (1) The wrapper is the object `chat_runner` already holds as `provider=client`
  (`:3839`), so the existing duck-typed read finds the hook with no change at the read site — and
  `ModelCallGuard.__getattr__` (`guardrails/model_call.py:550`) forwards unknown attributes to
  `_inner`, so a guarded provider keeps it too. (2) `stream()` / `stream_command()` **is** the turn
  boundary: one call per turn, which is where the reset has to be expressible. (3) A connection is
  shared by co-tenant sessions, so accumulating below the wrapper risks smearing attribution across
  sessions.
  **Turn boundary — one thing the native accumulator does NOT have.** `begin_turn()` clears at the
  START of each turn, because the drain is behind the learning gate: `_maybe_after_turn_review`
  returns on `not decision.worthwhile` *before* reaching the drain, so an accumulator that only
  cleared on drain would credit the next turn with the previous turn's failures. Falsified: deleting
  the `self._outcomes.clear()` line reds exactly one test with
  `[('Bash','failed'), ('Read','success')] != [('Read','success')]`.
  **Vocabulary — emitted from its single definition.** `memory_service.py:120`'s
  `PROCEDURAL_OUTCOMES = {"success", "failed", "denied"}`. The ACP seam emits **`success` and
  `failed` only**; the accumulator re-checks membership and drops-with-warning, ahead of
  `after_turn_review.py:135`'s own drop-and-warn. `denied` is deliberately NOT emitted: it requires a
  denial *observation* authored by `security.classify_denial`, which the native runtime has because it
  refuses the call itself — on this seam the CLI owns the refusal and a rejected permission arrives as
  an ordinary failed (or absent) result. Deriving one here would be the second-derivation defect.
  Failure is likewise read from the `ok` key, never re-derived from the ACP `status` field, so the
  breaker and procedural memory cannot disagree about one call.
  **Hook NOT promoted to the ABC.** Kept duck-typed. `ModelProvider`/`AgentProvider` have six
  implementers (`OpenAIProvider`, `AnthropicProvider`, `ModelCallGuard`, `NativeAgentRuntime`, the two
  ACP providers) plus test doubles; four of them have no tool loop and would gain a method returning
  `[]`. `getattr` tolerates absence by design — widening the contract to fix one seam is how a
  6-line change becomes a 6-file one.
  **Tool identity.** ACP has no tool-name field; `update.title` is the identity
  (`acp/translate.py:101`, `:164`). Only the `tool_call` title is trusted — a `tool_call_update`
  title is a progress DETAIL when it differs from the name (`chat_runner.py:2848` renders it that
  way), and folding it in would fragment one tool's priors across every argument it ever saw. A
  result with no preceding `tool_call` is dropped rather than filed under a placeholder. Bounded at
  200, the native ceiling.
  **Falsifications (each mutation applied to the LIVE line, re-read, and its enclosing function
  confirmed by AST probe; restored from a file copy).** (a) rename `drain_tool_outcomes` →
  `getattr` misses → `AssertionError: O12: zero procedural rows after a six-tool-call ACP turn`
  (9 failed / 6 passed) — the reproduction reds on ROWS, not on a missing attribute. (b) disable the
  vocabulary guard → the out-of-vocabulary outcome is silently STORED (`[('Read','success')] != []`).
  (c) accumulator never clears → the turn-leak test reds as above (1 failed / 14 passed). (d) replace
  `tools=tuple(sorted({t for t, _outcome in tool_outcomes}))` with a second `drain()` →
  `observe_turn` receives `'tools': ()` — the second reader starves exactly as the drain-once comment
  at `chat_runner.py:235` warns. (e) revert the adapter mapping → 6 reds here, and
  `test_acp_unattended_and_loop_breaker.py` stays green (the false green above).
  **Gates.** `make lint` clean (mypy 960 files); targeted pytest **480 green** across the new suite
  plus after-turn-review, acp client / session / session-provider / translate / event-mapping /
  unattended-and-loop-breaker, chat-runner procedural wiring, memory-service, memory-procedural,
  learning-procedural-loop and dashboard-chat. `gate_report.py` **6/6**; flat wire-error census
  **1507/1507** (no error path added). No test touches the real home — the memory store is a
  `tmp_path` sqlite file.
  **Inventory correction.** The `G7` bullet's root cause is now recorded above; `G6` remains OPEN and
  should NOT be re-read as closed on the strength of `AAP-6`'s log — the breaker it describes is real
  and correct, it was simply never reachable. Whether the residual `G6` work is anything beyond this
  adapter line is a live-drive question (`AAP-6`'s own BOUNDARY note records that kiro never emits
  `status: "failed"`, so the failure rungs stay unmeasured on that CLI), and that drive is owner-gated.

- 2026-08-21 — `AAP-8` **`G10` DONE (code-only; no CLI drive, no authenticated session).** Effective
  risk was mis-calibrated in both directions, and the two directions turned out to have **different
  causes** — the inventory bullet had folded them into one. Corrected in place above (three claims,
  each with the row it misread); the observation rows themselves are untouched.
  **Direction 1, over-labelling.** `resolve_effective_risk` returned the LITERAL `"destructive"`
  whenever `tool_kind` was `execute`/`command` and no command text was available. ACP agents open a
  tool call with `rawInput: {}` + `status: pending` and supply the input in a later
  `tool_call_update` — `extract_tool_update_events`' own docstring states this — and the SEL
  `invoked` row is written from the OPENING frame (`chat_runner.py:2649`). So a read-only `pwd; ls`
  was audited `destructive` from the *absence* of its command. Absence now has its own value and
  floors at `caution`.
  **Direction 1b, the empty kind (`O5`).** A wire-declared `kind: "read"|"search"|"fetch"` reached
  the permission path as `""` and floored at `caution`; trust-reads auto-approves only `safe`, so a
  plain **Read**/**Search**/**Fetch** raised a card forever even with trust-reads on. Measured
  before/after, kind carried vs not: `caution` → `safe`. This — not "name/kind-based and coarse" —
  is the mechanism behind the `trust_reads` row's PARTIAL verdict. That row is left as-is: flipping
  it needs an owner-gated live drive.
  **Direction 2, over-denying (`O13`).** ACP types the permission frame's `toolCall` as a
  `ToolCallUpdate`, whose input field is named `rawInput` — the same key `extract_tool_event` reads.
  `build_permission_event` read only `input`/`params`, so a frame carrying the command **inline**
  reached the task-mode gate with an empty input and the gate fell back to the display name; a title
  of the `Running: …` form (the shape `chat_runner.py:2663` strips) trips the `run` mutating hint, so
  a read-only `ls` was refused in the mode that exists to let you look around.
  **Correlation key: `toolCallId`** — the only identifier the two frames share, and the same key
  `tool_call_inputs` already used. New per-turn `tool_call_kinds` cache, written by
  `extract_tool_event` and by `extract_tool_update_events` when an update declares a kind, read
  (never popped) by `build_permission_event` as a **fallback only** — the frame's own declaration
  always wins, so codex's real `kind` is never overwritten. Owned and cleared per turn by
  `AcpSession` next to the inputs cache. The input cache is now read rather than popped: a popped
  cache made a second permission request for one call look like a tool whose command the host cannot
  see, reaching the fabricating state with no adapter misbehaving.
  **ONE risk vocabulary.** `task_modes.classify_invocation` → `READ_ONLY`/`MUTATING`/`UNCLASSIFIED`
  is the single classifier; `_is_read_only_tool` is now a projection of it, and `task_mode_denies`
  and `resolve_effective_risk` both derive from it. No second vocabulary was minted and
  `AUDIT_OUTCOME_FAMILIES` was not widened (no new SEL outcome; `metadata.risk` keeps its three
  existing values). **Polarity, stated because the two consumers want opposite things:**
  `UNCLASSIFIED` fails **closed** at the gate (still denied in ask/plan/build — honest labelling is
  not permission) and **honest** at the label (`caution`: never `safe`, so trust-reads cannot
  auto-approve a command nobody read; never `destructive`, which asserted an unmeasured fact).
  §2.2's recorded choice — the declared kind informs the label, never the gate, so a CLI that calls
  its own mutation "read" cannot turn a deny-by-default into an allow — is **preserved and now
  pinned** by a structural test on the `task_mode_denies` call site.
  **`adapter.py` DOES map `tool_kind`** (`acp/adapter.py:21`), so unlike `#1877`'s `tool_meta` this
  fix has one link, not two, and needs nothing from that branch. Asserted through the real
  `acp_event_to_agent_event` anyway: every new test starts at a JSON-RPC frame and ends at the risk
  verdict or the gate answer, never at the layer that authors the kind.
  **FALSIFIED, four live-line mutations (each re-read after applying and its enclosing function
  confirmed by AST probe; restored from a `cp` backup, never `git checkout`):**
  · **M1** drop the correlation read in `build_permission_event` → 3 red, and the load-bearing one
  reds on the VERDICT, not on an empty field: `test_a_declared_read_kind_reaches_the_risk_decision`
  → `assert 'caution' == 'safe'`.
  · **M2** revert the `UNCLASSIFIED` branch to `return declared_str or "destructive"` → 2 red,
  `test_pending_shell_frame_is_not_audited_as_destructive` → *"absence of a command is not evidence
  of destruction"*, `assert 'destructive' != 'destructive'`.
  · **M3** drop the `rawInput` fallback → 1 red, on the DENIAL:
  `test_command_carried_inline_on_the_permission_frame` → *"a read-only ls RUNS in ask mode"*,
  `assert 'Ask mode — o…make changes)' == ''`.
  · **M4** make `UNCLASSIFIED` resolve `READ_ONLY` (unknown → permissive) → 3 red, including
  `test_unclassified_is_denied_by_the_task_mode_gate` → `assert '' != ''`.
  **BLIND SPOT MEASURED: not one pre-existing test noticed any of the four.** 516 / 345 / 266 / 266
  green respectively across acp translate·session·client·permission-authority·turn-scenarios·
  unattended, task-modes, tool-risk, chat-plan-mode, dashboard-approval, approval-threading,
  chat-craft-sel-audit, sel and security. The old suite's blind spot is exactly the one `#1877`
  found: it asserted the decoder's output and drove the consumer over hand-built objects, so a
  field that never reached the consumer read as fine.
  **Not done, named rather than hidden.** (a) The SEL `invoked` row is still written from the opening
  `tool_call` frame, so a shell call whose input arrives in the following `tool_call_update` is
  audited `caution` and never re-scored once the command lands — honest now, but less precise than
  the card beside it. Re-scoring means either deferring the row (losing the "every executed tool is
  audited" guarantee) or emitting a second row (a new outcome word, and `sel.py`'s
  `AUDIT_OUTCOME_FAMILIES` is a closed vocabulary owned by `test_audit_outcome_families.py`) — a
  separate change, not a silent widening. (b) `O7`'s notification could not be attributed to a
  specific card from the recorded body; see the corrected bullet.
  **Gates.** `make lint` clean; targeted pytest **598 green** over the new suite plus acp
  translate·session·permission-authority·unattended·turn-scenarios·agent-event-mapping·client·types,
  task-modes, tool-risk, chat-plan-mode, audit-outcome-families, sel, security, dashboard-approval,
  approval-threading, approval-timeout-policy, chat-craft-sel-audit, inert-surface-baseline and
  structural-baseline; `make test` full suite; `gate_report.py` **6/6**; flat wire-error census
  **1507/1507** (this change adds no error path). **No matrix cell flipped to CONFIRMED and
  `dag.json` untouched** — a live drive is owner-gated.

- **2026-08-22 — `G18` closed on the same seam, and the correlation now carries BOTH halves.**
  `G10` above correlated the declared `kind` onto the permission frame; the title was left at
  `tool_call.get("title", "unknown")`, which is `G18` — codex's `session/request_permission` payload
  is `{toolCallId, kind, status}` and the human title lives one frame earlier, so every codex
  approval card and every SEL decision row read `tool: "unknown"` **while the real name sat in the
  cache one lookup away**. The parity doc had already localised it exactly ("the same function
  already correlates the input across those two frames, so the title is one cache lookup away").
  **Rather than thread a third sibling dict**, `tool_call_kinds` became `tool_call_seen:
  dict[str, SeenToolCall]` carrying `kind` + `title` — the signature arity of all three decoders is
  UNCHANGED (so the existing positional `{}` call sites still hold), and the per-turn cache count
  stays at two instead of three. A `tool_call_update` refines the two fields **independently**
  (`dataclasses.replace`): an update that names the tool but omits the kind must not erase the kind
  the opening frame declared, and vice versa.
  **A second defect the fix exposed:** `.get("title", "unknown")` only treated a MISSING key as
  absent, so a frame sending `title: ""` shipped a nameless card even when the correlation held the
  real name. Both a missing key and an empty string are now the same fact.
  **The vacuity floor is deliberate and asserted:** an uncorrelated `toolCallId` still resolves to
  `unknown` rather than borrowing another call's name — a guessed name is worse than a missing one,
  and `test_a_title_is_never_invented_when_no_frame_named_the_tool` pins it.
  **Falsifications** (AST-confirmed inside `build_permission_event`, restored from a file copy):
  · **M1** title fill removed (`if False and seen is not None`) → **3 of the 5 new tests red**
  (the titleless frame, the empty title, and the update-refinement); the other two do not depend on
  the fill and correctly stayed green. · **M2** the old `.get("title", "unknown")` restored →
  **2 red**. **Blind spot measured:** under M1 the pre-existing ACP suites
  (permission-authority · effective-risk-correlation · unattended-and-loop-breaker) were
  **90 passed, 0 failed** — nothing existing caught this, which is what the new tests close. The new
  tests drive the real two-frame sequence rather than seeding `tool_call_seen` by hand, because a
  test that builds the correlation itself cannot catch a producer that stops filling it.
  **Doc truth moved with the fix:** `docs/agents/acp-parity.md`'s codex row is struck from
  "Protocol or CLI constraint" and a compensation row added, with the "five mechanisms" count
  re-derived to six. Both are marked **not re-driven** — the fix is unit-proven, so the doc states a
  landed mechanism, not a measured cell. `G18`'s inventory bullet is the record; no matrix cell
  flipped and `dag.json` is untouched.

- **2026-08-22 — `G14` + `G15` DONE (P3 legibility, ad-hoc off `origin/main` `05bba66e`).** One
  sentence, two lies, fixed together because correcting the verb while the runtime label still named
  the adapter would have left the line lying.
  **`G15` verdict: the recommendation was WRONG, and `resumed` was only half the right gate.** The
  audit the task asked for changed the fix. Read `session.py`: `get_or_create` returns
  `(provider, is_new, resumed)`, and the **reuse path returns `resumed=False` unconditionally**
  (`:1065` `return provider, was_new, False`) while the creation path returns `(provider, True,
  resumed)` (`:1281`). So `is_new` means "a runner was STARTED this turn" — exactly what
  `chat_runner.py`'s floor-seeding comment says — and `resumed` means "that runner LOADED a persisted
  session". Gating the verb on `resumed` alone made every later turn of one live session say
  "created" (the filed symptom, real); gating it on `is_new` alone would have made every reused turn
  say "resumed" (the filed recommendation, worse). The two flags answer two different questions, so
  the fix uses both: `is_new` decides whether a runner was started, `resumed` picks the verb when one
  was, and `not is_new` gets a third word the line could not previously say — **"continued"**. Two
  divergent `broadcast_ws` branches collapsed into one call with one format string, since two
  branches is precisely how `G14` shipped a stale label.
  **`G14`: the label now names the runtime the user PICKED.** `AcpAgentProvider.provider_id` returns
  the configured `acp:<cli>` `ProviderEntry` name, threaded in as a `runtime_id` kwarg by `_factory`
  (`entry.name`); basename inference survives only as the fallback for a provider built without one
  (`test_agent_provider.py:30`'s existing assertions still pass unchanged). This is **convergence,
  not invention** — `AcpSessionProvider.provider_id` already returned `self._runtime_id`,
  `discover_agents` already applied entry-name-with-basename-fallback to `options["runtime_id"]`
  (`acp_agent.py:284`), and `GET /api/agent-providers` already reported `entry.name` as the row's
  `provider_id` with `test_agent_providers_endpoint.py:341-346` locking `"acp:claude-agent-acp" not
  in rows`. `AcpAgentProvider` was the last of the four disagreeing. The `acp:` prefix is enforced as
  an invariant rather than passed through, because `chat_runner.py:2498` derives the not-gateable/SEL
  key as `provider_id[4:]` behind a `startswith("acp:")` test — a bare entry name would have silently
  disabled the ungated-tool report.
  **`provider_id` consumers checked BEFORE touching it** (the census is why the change is safe):
  four definitions (`NativeAgentRuntime` → `"native"`; `AcpAgentProvider`; `AcpSessionProvider` →
  `self._runtime_id`; the `AgentProvider` ABC). Only TWO sites read the property off an instance —
  `chat_runner.py:1912` (this label) and `:2497` (the `_acp_cli` derivation). `agents/registry.py`'s
  `provider_id` parameter is fed from config/`entry.name`, never from the property. Nothing persists
  it to disk or JSON, and nothing in `src` compares it to a literal. The one identity consumer,
  `acp/permission_authority.py:272-286` `normalize_provider`, is **explicitly documented as
  accepting "the bundle name, the `acp:<cli>` runtime id, or the raw launch-command basename … since
  the three disagree"** — so it tolerates both the old and new value. `runtime_label` /
  `_runtime_label_for` (`handlers/providers.py:345`) was deliberately NOT reused for this line: it
  title-cases to `"Claude Code"`, which reads like an agent name and loses the external-CLI signal
  the line exists to carry. It stays the providers-list display form.
  **Not fixed, named rather than hidden:** the `"Creating session…"` status broadcast at
  `chat_runner.py:1850` fires before `get_or_create`, so it also over-claims on the reuse path. It is
  a transient `kind: "status"` progress line and the truth genuinely is not knowable before the call,
  so it is left alone rather than half-fixed. **Also measured:** `ChatPage.tsx:961` drops
  `kind === 'session'` outright, so this sentence is user-visible on the **Loop** and **Code**
  cockpits (`LoopCockpitPage.tsx:368-373` — whose comment literally says "session created" —
  and `CodeCockpitPage.tsx:459-460`), not in the main chat transcript. No frontend change needed;
  no `web/` file touched.
  **Falsifications (mutate the LIVE line, re-read, AST-probe the enclosing function, restore from a
  file copy).** (a) `provider_id`'s configured branch neutered → **6 red**, including the rendered
  sentence: `['Session created · default · auto · via acp:claude-agent-acp']` and
  `['Session created · … · via acp:npx']` — the adapter binary and the npx degradation both named on
  the wire. (b) verb re-gated on `resumed` alone (the filed bug restored) → **3 red** on the wrong
  word: `"Session created"` for a reused session, `"Session continued"` for a loaded one. (c) the
  settled gate inverted (`not is_new` → `is_new`) → **6 red**. AST probes confirmed each mutation sat
  in real code inside the intended function (`provider_id` [72-97] as an `Assign`; `_run_chat`
  [1435-4197] as an `If` testing `resumed`/`is_new`) — not in a docstring.
  **BLIND SPOT (worth having): nothing pre-existing noticed either mutation.** Ten suites / **430
  passed, 1 skipped** under mutation (a); twelve suites / **458 passed, 1 skipped** under mutation
  (b) — including `test_dashboard_approval.py` and `test_chat_rewind.py`, which drive `_run_chat`
  heavily, and `test_agent_provider.py`, which asserts `provider_id` directly. The reason is that
  **zero tests asserted this sentence before this change** (`grep -rn "Session created"` over `src`,
  `tests` and `web/src` matched only the two producing f-strings), and the suites that drive
  `_run_chat` assert decoder output and approval outcomes over hand-built mocks. Both gaps were
  invisible to the entire suite by construction, which is why the new rails assert the broadcast
  sentence and not the flags.
  **Gates.** `make lint` clean (mypy 959 source files). Targeted pytest: 12 new + 458 pre-existing
  across `test_agent_provider` / `test_acp_permission_authority` / `test_agent_providers_endpoint` /
  `test_acp_bundles` / `test_dashboard_chat` / `test_acp_session_provider` /
  `test_session_acp_pool_claim` / `test_acp_unattended_and_loop_breaker` / `test_chat_plan_mode` /
  `test_acp_slash_command_fallback` / `test_dashboard_approval` / `test_chat_rewind` — **470 passed,
  1 skipped**. `make test` full suite **green on the first run: 24,017 passed / 30 skipped / 12
  xfailed / 0 failed in 271s** — neither known item appeared (no `test_loop_worktree_sparse` sparse-
  cone flake, no `test_subagent.py` isolation red). `gate_report.py` **6/6**; flat wire-error census
  **1507/1507, delta 0** (this change adds no error path). No `web/` file touched, so the frontend
  gates do not apply. `git status --porcelain` clean.
  No matrix cell flipped to CONFIRMED; `dag.json` untouched. Depends on nothing in flight, but
  **note for `#1876`** (`G5`, a runtime-binding rail around `chat_runner.py:1798-1818`): it lands
  next to these sites and a textual conflict is expected — the two changes are independent
  (`#1876` fixes *which* runtime is bound, this fixes what the line *says* about it).
- 2026-08-22 — **`G16` FIXED** (code-only; no CLI driving, no owner authentication). The reported
  mechanism reproduced exactly: `detect_facet_candidate`'s veto branch searched
  `\b((?:never|do ?n'?t ever|do not ever|always avoid)\b[^.!?\n]*)`, so against
  "Answer in exactly one sentence from now on, never more." it matched `never` as a **degree
  adverb**, `[^.!?\n]*` took `" more"`, and `capture_preference_facet` wrote the durable lesson
  `Never: never more`.
  **The rule now, stated once in `preference_facets.py` and implemented once in the new
  `veto_clause()`:** a veto is recognized only when a negative trigger is followed by a
  **prohibited-action clause** — (1) a head token that can open a verb phrase, i.e. NOT a
  closed-class function word / degree adverb / comparative / pronoun / preposition / conjunction /
  copula / modal (`_NON_ACTION_HEADS`), (2) at least one further token for the action's
  object-or-complement, and (3) not one of the enumerated non-prohibitive idioms
  (`_VETO_IDIOM_HEADS` = "never mind …", `_VETO_IDIOM_CLAUSES` = "never say never").
  **Deliberately rejected:** "never more", "never more than one sentence" (a quantity nudge — the
  style detector and the after-turn summarizer own preferences of that shape), "never again",
  "never mind the tests", "better late than never", "now or never", "I would never have guessed"
  (`have`/`had` are excluded as heads on purpose: the counterfactual is commoner than "never have
  X"), and any trigger with no complement. Precision over recall, as the docstring already
  promised: this is the ONE candidate class that reaches the durable lesson store, so a missed
  veto is cheap and a false one is not. **No LLM call added** (C15's constraint) — the fix is
  strictly cheaper than the regex it replaced plus a frozenset lookup.
  **Recall went UP, not down, in one respect:** every trigger occurrence is now tried, so
  "In one sentence, never more. Also never use emoji." yields `never use emoji`, where the old
  single-`re.search` returned the fragment and stopped.
  **Routing NOT changed, deliberately.** The asymmetry the gap notes is real — the loosest matcher
  produced the least reversible record — but splitting heuristic vetoes off into a decaying facet
  would rebuild the parallel always/never model this module's docstring (`:13-15`) exists to
  prevent, and `upsert_facet` returns None for `veto` specifically to enforce the single home. The
  proportionality dial already exists on the READ side: `learning/lesson_confidence.derive` gives a
  single-sighting non-human-authored lesson `corroboration(1) = 0.0` against a `0.5` threshold, so
  a junk row is RETAINED-not-INJECTED until three sightings. That is a mitigation, not the fix, for
  three measured reasons: the row is still written and still consumes contradiction judging; the
  standings computation **fails OPEN** (`vector_memory.py:2925-2934` — an unreachable evidence store
  injects everything at 1.0); and a habitual phrasing crosses the threshold on the third repeat, at
  which point the junk rule *is* injected. So the fix is at the writer, and confidence composes
  underneath it as a second line.
  **Falsifications (each mutation applied to the LIVE line, re-read, and its enclosing function
  confirmed by an `ast` probe; each restored from a `cp` file copy, never `git checkout`):**
  · **M1 — old regex restored** inside `veto_clause` (AST: `veto_clause`, line 257). Red observed on
  the STORED TEXT, not on a regex: `AssertionError: ['Never: never more']` from
  `test_facet_capture_does_not_learn_a_never_fragment_as_a_lesson`, plus 13 more (11 rejection cases,
  the multi-trigger recovery, the `veto_clause` unit) = **14 failed / 66 passed**.
  **Blind spot: the pre-existing suites did not notice at all — 147 passed, 0 failed** across
  preference-facets / after-turn-review / lesson-confidence / lesson-contradiction / lesson-scope /
  lessons-memory-reroute / memory-service / temporary-chat with the new tests deselected. The defect
  had zero coverage; that measurement is the finding.
  · **M2 — precision collapsed to "detect nothing"** (`len(tokens) < 2` → `< 99`; AST: `veto_clause`,
  line 260). Pre-existing suites **DID** notice: 3 reds including the store-level
  `test_facet_capture_veto_routes_to_lesson`. The new direction-(b) tests red too (8, one per genuine
  veto), so they are not vacuous.
  · **M3 — veto→lesson routing flipped** to `upsert_facet(vs, "style", …)` (AST:
  `capture_preference_facet`, line 179). Pre-existing suites noticed with 1 red; the new
  `test_veto_is_durable_while_a_style_nudge_decays` red at its `force-push`-in-the-lesson-store
  assertion, so the durable-vs-decaying distinction is now **asserted, not assumed**.
  **DISCOVERY — `K49`'s second veto artifact is a DIFFERENT defect and stays open.**
  `Never: never violate these):` was clipped from *injected prompt boilerplate*, so its root cause
  is provenance (capture ran over injected context that had arrived inside the user message), not
  grammar: "never violate these" is a well-formed prohibition and the tightened rule accepts it,
  correctly by its own terms. Closing it means teaching the capture path to separate the user's own
  words from injected content — filed on the `G16` bullet rather than half-fixed here. The same
  `K16` turn's *other* row ("User correction to honor: …") comes from `is_correction_signal`, a
  different writer, and is likewise untouched.
  **Gates.** `make lint` clean (mypy 959 files); targeted pytest **168 green** across
  preference-facets, after-turn-review, lesson-confidence, lesson-contradiction, lesson-scope,
  lessons-memory-reroute, memory-service and temporary-chat (paths existence-checked first);
  `make test` full suite **green on the first run — 24026 passed, 30 skipped, 12 xfailed**, with
  neither the documented `test_loop_worktree_sparse` sparse-cone flake nor the open `test_subagent.py`
  isolation red appearing (run count: 1). `gate_report.py` **6/6**; flat wire-error census
  **1507/1507, zero slack** (this change adds no error path). No cell flipped to CONFIRMED;
  `dag.json` untouched.
  **INDEPENDENT VERIFICATION found a false NEGATIVE on the same axis, fixed here.** Probing the
  shipped `veto_clause` over 18 phrasings (9 per direction) rather than re-reading its regex,
  **"never, ever do that again" was silently dropped** — emphatic doubling puts the intensifier in
  the clause-head position, `ever` is (correctly) a `_NON_ACTION_HEADS` member, and `ever` alone is
  not a trigger, so no later occurrence rescued it. An emphatic veto is the *most* emphatic kind, so
  this is the atom's own property failing, not a recall trade-off: added `_EMPHATIC_CLAUSE_HEADS`
  and a leading-emphatic strip before the head is judged, +2 positive and +2 negative cases
  ("never, ever", "never ever more" must still be refused — stripping must not manufacture a veto
  out of nothing). Falsified: `while False and …` (AST-confirmed, real `While`, inside
  `veto_clause`) reds **exactly the 2 new positive cases**, 43 passed.
  **A wrong-target mutation of my own is worth recording.** Re-running M1 as `len(tokens) < 2` →
  `< 1` gave **41 passed, zero red** — not a hole in the property but a *weaker* mutation than the
  agent's "old regex restored": for "never more" the token count is 1, so the count guard passes and
  the **next** guard (`more` ∈ `_NON_ACTION_HEADS`) still refuses it. Mutating that head check
  instead reds 3 (`I would never have guessed`, `never mind the tests…`, `never more than one
  sentence please`). So the two guards are **complementary, neither redundant** — the count guard
  alone catches "never more", the head guard alone catches "never have guessed" — which the agent's
  single-mutation account did not show. Untouched suites saw **37 passed** under the head mutation:
  the blind spot the new tests close.
  **Two claims re-verified against code, one citation corrected.** The fail-open confidence path is
  verbatim at `vector_memory.py:2927-2934` (`LessonStanding.INJECTED`, "confidence unavailable —
  injected rather than silently dropped"), so a veto that reaches the store is *injected* even when
  confidence is unavailable — which is why precision, not recall, is the right bar here. And
  `_HUMAN_AUTHORED_SOURCES` is `frozenset({"user_explicit", "vault_edit"})` at `vector_memory.py:126`,
  so `facet_veto` is **not** human-authored (the agent cited `lesson_confidence.py` — wrong file,
  right substance).
  **Final gate, re-run independently on the amended tree:** `make lint` clean (mypy **959** files);
  targeted **115 green** across preference-facets, after-turn-review, lesson-confidence,
  lesson-contradiction and the wire-error census, every path existence-checked first;
  `gate_report.py` **6/6**; flat census **1507/1507**; probe sweep **16** (the PHF-14 baseline) and
  `git status` empty; full `make test` **24030 passed, 30 skipped, 12 xfailed, exit 0**. An earlier
  full run on the pre-amendment tree had **1 red**, `test_loop_worktree_sparse.py::TestPoolBound::
  test_batch_creates_every_worktree` — the documented sparse-cone flake; **47/47 in isolation** and
  absent from the final run (run count: 2 full, 1 red). Nothing in this diff touches worktrees.
