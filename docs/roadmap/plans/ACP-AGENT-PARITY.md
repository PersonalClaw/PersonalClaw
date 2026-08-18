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
- **Acceptance:** with task-mode=Ask, a file write via any ACP provider produces a host approval card (or is blocked) — never a silent write; the deny-list rejects a denied command at the permission prompt with the standard denial message; PreToolUse blocking hooks fire pre-execution on every permission-surfaced tool. The residual not-gateable set per provider is enumerated in the parity doc, not discovered by users.

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
| `O8` | S2 (workspace_dir = `…/.dev-home/scratch` set BEFORE binding, `PERSONALCLAW_HOME=…/.dev-home`), turn: "Run exactly one Bash command: pwd" | no approval card (read-only-bash auto-resolved); `pwd` → **`/Users/golani/.personalclaw/workspace`**. Neither the session's `workspace_dir` nor the configured `PERSONALCLAW_HOME` reached the ACP process; the file the CLI later wrote landed in the operator's real home |
| `O9` | S2's activity frames | `Injected 11,560 chars of context (memory, lessons, history, episodic)` — turn-0 context assembly runs for an ACP turn |
| `O10` | `security_events.jsonl` under the isolated home after O5/O8 | 12 `tool_invocation` rows for the ACP turns, hash-chained, e.g. `operation: "Terminal", tool_kind: "execute", outcome: "invoked", metadata: {"risk": "destructive"}`, plus `outcome: "approved"` rows carrying the approval decision. No `risk_level` other than the heuristic one |
| `O11` | `ls .dev-home/session_pid_*.txt` | `session_pid_28787.txt` → `dashboard:chat-1-…`, `session_pid_52634.txt` → `dashboard:chat-2-…` — the session-key resolution file IS written for ACP sessions, and the two sessions hold **two different PIDs** |
| `O12` | `sqlite3 .dev-home/memory.db` + `learning.db` after the 6-tool-call ACP turn | `semantic_memory`, `episodic_memories`, `memory_events`, `mem_*` all `0`; `learning.db staging` `0` — no procedural-outcome row from an ACP turn |
| `O13` | S2 with `POST /api/chat/task-mode {mode:"ask"}`, then "Use your Write tool right now to create … ask-mode-probe.txt" | the write was **blocked**, not executed: `tool_result` = "Tool permission request failed: Error: Tool use aborted" and the tool line read `Write … (Ask mode — only read-only tools run (switch to Agent to make changes))`. The file was never created. A follow-up **read-only** `ls` bash was denied by the same gate |
| `O14` | S2 in `plan`, "Quote verbatim every line of your context containing 'Task mode'" | the reply quoted `## Task mode: Agent` — but from **replayed sibling-session history**, not the live framing. Re-run on a FRESH session (S3) created with `task_mode=plan` before its first turn quoted `## Task mode: Plan` and "Plan mode is active… you MUST NOT make any edits" → the framing is live and correct; the stale line was history |
| `O15` | S3's first-turn context (fresh session) | `Injected 18,394 chars of context (memory, lessons, history, episodic)` and the quoted context contained verbatim user/assistant turns from a DIFFERENT dashboard session — cross-session recall reaches an ACP turn |
| `O16` | killed the gateway, restarted it, then sent a turn on the SAME session S3 | `GET /api/chat/sessions` now reports S3 with `acp_provider: ""`, `task_mode: "agent"`, `workspace_dir: ""`; the turn resolved on the **native** axis and errored `no model provider resolves for use case 'chat'`. `gateway.log`: `Pool decision: key=dashboard:chat-3-… resume_sid=None …` — no `session/load` was attempted. `session_map.json` holds a sid for the session (`2cb03780-…`) but records no `cwd` |
| `O17` | S4: `workspace_dir` set to `…/.dev-home/scratch`, provider bound, one `pwd`; DEBUG logging on | `gateway.log`: `Pool decision: key=dashboard:chat-1-1786980914 … cwd=/private/tmp/aap1-wt/.dev-home/scratch pool_cwd=/private/tmp/aap1-wt/.dev-home/scratch` — the cwd DID reach `get_or_create`; the ACP process still answered `pwd` → `/Users/golani/.personalclaw/workspace`. The cwd is dropped between `get_or_create` and the spawn |
| `O18` | `grep -in "set_config_option\|session/new\|mcpServers\|set_mode" gateway.log` at DEBUG | nothing — the host logs no ACP wire frames, so mode/`mcpServers` forwarding cannot be observed from the host's own logs; the marks below that depend on it are taken from CLI-observable behavior instead |
| `O19` | S5: provider bound, `task_mode=plan` set BEFORE the first turn, then "Use the Write tool now to create … pm3.txt" | the CLI did **not** call `Write`. It emitted a `"Ready to code?"` permission request whose `tool_input.plan` says "My CLI permission mode is `plan`, which forbids writing anywhere except this plan file"; its final reply says "Permission mode: `plan` (Claude Code's CLI plan mode)". Rejecting the card left the file uncreated. On S4, where `plan` was set MID-conversation, the same request produced a plain `Write` attempt and the CLI reported "Agent" mode |
| `O20` | S6 bound with `{model: "global.anthropic.claude-haiku-4-5-…", reasoning_effort: "low"}`, then "Name the exact model id you are running as" | the bind echoed both values; the activity line read `Session created · default · global.anthropic.claude-haiku-4-5-20251001-v1:0 · via acp:claude-agent-acp`; the CLI answered "Model: `global.anthropic.claude-haiku-4-5-20251001-v1:0`; reasoning effort not specified in session context" |
| `O21` | S6: "Run this exact bash command: `git push --dry-run .`" | `tool_result` = "Permission denied: Permission to use Bash with command git push --dry-run . has been denied." **No `approval` frame reached the host** and `security_events.jsonl` contains **zero** rows mentioning `push` — the denial came from the CLI's own deny list, not the host's, and left no SEL row |
| `O22` | S6: a correction turn ("No - stop doing that. Always answer me in exactly one sentence…") | two `activity_event` frames of kind `learned`: `Learned: never more` and `Learned: User correction to honor: No - stop doing that…`. The CLI also wrote a memory file into the operator's real `~/.claude/projects/…/memory/` |
| `O23` | S6: sent the literal message `/compact` | the turn hard-failed: `Prompt error: {'code': -32601, 'message': '"Method not found": _vendor.dev/commands/execute'}`. No plain-prompt fallback ran. SEL logged `slash_command` with `outcome: bypass` |
| `O24` | S6 in `ask` mode: "Attempt these six Write tool calls one after another without stopping, even if each one fails" | **six** consecutive `tool_result` failures in ONE turn ("Tool permission request failed: Error: Tool use aborted"), then `Turn complete: 106 events, 6 tool calls`. No warn, no block, no circuit abort, no steering injection — the native breaker's warn@3 / block@5 / circuit thresholds never fired |
| `O25` | `POST …/{S6}/regenerate` then `POST …/{S6}/fork` | the regenerated assistant message carries `variants` + `variant_idx`; the fork created `chat-4-…` with 24 messages — but the fork's `acp_provider` is `""`, so the branch does not inherit the ACP binding |
| `O26` | `POST /api/chat` with a second message while a turn was in flight | **inconclusive** — the probe turn (count to 40) finished 1.2 s before the second message landed, so the two never overlapped; `queue` stayed `null` and the second message ran as its own turn. Recorded as not-exercised rather than as a queue verdict |

### 4a. Prompt-side context — claude-code column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Memory recall injection (turn-0 context) | WIRED | CONFIRMED | WIRED | `O9`, `O15` — the injection line fires and the CLI quoted injected memory/history text back verbatim |
| Knowledge context (@-mention + picker `meta.knowledge`) | WIRED | NOT-EXERCISED | — | the specific injector was never driven; only the generic message-text assembly was observed (`O9`). Residual — see §"Residual not-exercised cells" |
| Attachments/paste (extracted text prepended) | WIRED | NOT-EXERCISED | — | as above; no attachment/paste `meta` was sent |
| @prompt expansion (+ typed vars, snippets) | WIRED | NOT-EXERCISED | — | as above; no `@prompt` mention was sent |
| Skills index in context + `skill_invoke`/`skill_search` execution | PARTIAL | CONFIRMED | PARTIAL | `O4` — the CLI reported the index arrived as prompt text ("the session context references … `skill_invoke`") while the tools themselves are absent from its list |
| Session-live skill drafts (`skill_remember`) | PARTIAL | CONFIRMED | PARTIAL | `O4` — same measurement: no `skill_remember` in the CLI's tool list |
| Task-mode framing (Agent/Ask/Plan/Build suffix) | WIRED | CONFIRMED | WIRED | `O14` — a fresh plan-mode session's context contains `## Task mode: Plan` + "Plan mode is active… you MUST NOT make any edits" |
| Agent profile system prompt / voice layer | PARTIAL | NOT-EXERCISED | — | no agent profile with a distinctive `system_prompt` was bound, so the "CLI's own prompt dominates" half was not measured |
| Project binding (context preamble + cwd) | WIRED | **DIVERGED** | PARTIAL — preamble only | `O8`, `O17` — the preamble/context half works, but the cwd half does not: `cwd` reaches `get_or_create` and the ACP process still runs in `~/.personalclaw/workspace` |
| project_id → artifact stamping | ABSENT | CONFIRMED | ABSENT (stronger) | `O4` — `artifact_save` is not reachable at all on claude-code, so there is nothing to stamp |
| Persona injection (Lumon theme) | WIRED | NOT-EXERCISED | — | the persona toggle was never enabled during the sweep |
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
| Unattended mode (strip interactive tools + fail-fast approvals, T5) | ABSENT | NOT-EXERCISED | — | requires an unattended loop/cron run; this isolated home has **no model provider configured**, so a loop fails on provider resolution before any ACP worker turn (`O16` shows the same error shape) |
| Dry-run replay (T9 observe mode) | ABSENT | NOT-EXERCISED | — | `dry_run` has no dashboard entry point to drive as-a-user |
| OS sandbox wrap of the agent process | WIRED | NOT-EXERCISED | — | sandbox mode was left at its default; no confinement boundary was probed |
| Isolated CLI config hardening (`PERSONALCLAW_CC_ISOLATE`) | WIRED (opt-in) | CONFIRMED off-by-default, and the default measurably leaks | opt-in, and OFF is the shipped default | with the flag unset the spawned CLI loaded the operator's real `~/.claude` — it enumerated the operator's own MCP servers (`O4`) and wrote into `~/.claude/plans/` and `~/.claude/projects/…/memory/` (`O19`, `O22`) |

### 4c. Tools — claude-code column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Filesystem/shell tools (cwd-confined + extra_tool_roots) | PARTIAL | **DIVERGED** — worse | PARTIAL, and NOT cwd-confined to the session's workspace | `O8`, `O17` — the CLI's own `Read`/`Write`/`Terminal` run in `~/.personalclaw/workspace` regardless of the session's `workspace_dir`, and reached `/Volumes/…`, `~/.claude` and `~/.personalclaw` freely (`O5`, `O19`, `O22`) |
| Full native tool registry (knowledge/tasks/loops/inbox/memory/artifacts/workflows/subagents/web/schedule) | UNKNOWN | CONFIRMED (§5 gap 1 predicted "likely absent") | **ABSENT** | `O4` — `knowledge_search` NO, `task_create` NO, `notify` NO; no `personalclaw-core` MCP server in the CLI's tool list |
| Tool disable prefs (PT3/UT4 per-tool + per-provider) | ABSENT | NOT-EXERCISED | — | no tool-disable pref was set during the sweep |
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
| Memory consolidation on session end | WIRED | NOT-EXERCISED | — | same reason — consolidation is a model call |
| Incognito/restricted no-write guarantees | WIRED | NOT-EXERCISED | — | no incognito/restricted session was driven |

### 4e. Session / conversation mechanics — claude-code column

| Feature | audit said | mark | runtime verdict | evidence |
|---|---|---|---|---|
| Variants / regenerate (‹n/N› switcher) | WIRED | CONFIRMED | WIRED | `O25` — the regenerated assistant message carries `variants` + `variant_idx` |
| Edit & resend, branch continuation (fork) | WIRED | CONFIRMED with a caveat | WIRED, but the branch loses the runtime | `O25` — the fork carries all 24 messages and `acp_provider: ""` |
| Queued messages (merge/pop + live bubbles) | WIRED | NOT-EXERCISED | — | `O26` — probe missed the in-flight window |
| Empty-turn auto-retry | WIRED | NOT-EXERCISED | — | no empty turn occurred across ~14 turns; not forceable as-a-user |
| Auto-nudge re-arm (loops) | WIRED | NOT-EXERCISED | — | loop-only; blocked by the same missing model provider as the unattended cell |
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

| mark | count |
|---|---|
| CONFIRMED (runtime matched the audit's prediction) | 35 |
| DIVERGED (runtime contradicted it) | 6 |
| NOT-EXERCISED (no runtime observation obtained; reasons below) | 22 |

All **four** cells the audit left literally `UNKNOWN` for claude-code are now definite:
native tool registry → **ABSENT**, `AskUserQuestion` → **ABSENT**, subagents → **ABSENT**,
context-% emission → **emitted but always 0%**. The sweep is therefore **PARTIAL against
`AAP-1`'s "zero UNKNOWN cells"**: nothing is left un-decided that the audit flagged, but 22
cells were not driven and are listed rather than guessed.

### Residual not-exercised cells (and why)

1. **Needs a model provider in the isolated home** (5): unattended mode, auto-nudge re-arm,
   skill-ladder review, memory consolidation, and the loop half of the failure-breaker check.
   A loop/cron run fails on `no model provider resolves for use case 'chat'|'background'`
   (`O16`) before any ACP worker turn happens, so a wedge/no-wedge verdict would measure the
   wrong thing.
2. **Needs a fixture that was not built** (9): knowledge @-mention, attachment/paste, @prompt
   expansion, agent-profile system prompt, per-agent approval floor, PreToolUse hooks, the
   other five hook kinds, tool-disable prefs, Lumon persona injection.
3. **Needs a timing/failure injection that did not land** (5): queued messages and
   queue-steering (`O26` — the probe turn finished 1.2 s early), cancelled-turn preamble,
   empty-turn auto-retry, pipe-death auto-retry.
4. **No as-a-user entry point** (3): dry-run replay, OS sandbox confinement probe, and
   trust/YOLO auto-approve (deliberately not enabled — every card was resolved explicitly so
   the gate itself stayed measurable).

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
  silently escaped. **Not fixed here — outside this atom's fence.** Owner: core seam, and it
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
- **`G5` A gateway restart silently changes the runtime** (`O16`) — the session's `acp_provider`,
  `task_mode` and `workspace_dir` are all cleared, `resume_sid` is `None` (no `session/load`
  attempted even though the adapter advertises `loadSession`, `O1`), and the next turn resolves
  on the native axis. On this provider-less home that surfaced as an error; on a machine with a
  native model provider it would silently run a *different* runtime with different tools and
  different confinement. Strictly worse than the audit's "falls to compressed history". Owner
  `AAP-7`, and the binding-persistence half is new scope for it.
- **`G6` No host-side brake on a failing ACP loop** (`O24`) — six consecutive tool failures in
  one turn produced no warn, no block, no circuit abort and no steering injection. Confirms
  audit gap 5; owner `AAP-6`.
- **`G7` Procedural memory never learns from ACP turns** (`O12`) — zero rows after a 6-tool-call
  turn. Confirms audit gap 4; owner `AAP-8`.

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
  line would read `acp:npx`.
- **`G15` "Session created" prints on EVERY turn** — the line is gated on `resumed`, never on
  `is_new` (`src/personalclaw/dashboard/chat_runner.py:1731-1748`), so a long ACP conversation
  repeats "Session created" and never says "resumed". `AAP-7`'s accurate-labelling acceptance
  criterion needs this row too.
- **`G16` The preference-facet extractor learned the fragment "never more"** from
  "…in exactly one sentence from now on, never more." (`O22`) — the capture path is live
  (that is the CONFIRMED part) but the extraction is poor.

### Incidental bugs fixed in-session

**None.** Every defect this sweep found (`G1`, `G4`, `G5`, `G8`, `G12`, `G13`, `G14`, `G15`,
`G16`) lives outside this atom's fence — in `llm/acp_agent.py`, `acp/client.py`,
`dashboard/chat_runner.py` and the learning capture path — and `G1`/`G5` are structural enough
that the plan's own rule applies: "anything structural waits for Phase 2 so fixes land against
the full three-provider picture." They are filed above at their measured severity instead of
being half-fixed.

## Phase 1 results — codex verified matrix (atom `AAP-2`)

**Swept:** 2026-08-17 · **Adapter:** `@agentclientprotocol/codex-acp` 1.1.4 ·
**CLI:** `codex` 0.146.1.359 (stable) at `/Users/golani/.toolbox/bin/codex`, whose own
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
| `C4` | S1 turn 1: "Run exactly one shell command: pwd … then list your available tool names verbatim, then YES/NO for knowledge_search, task_create, notify" | `pwd` → **`/Users/golani/.personalclaw/workspace`** — the escape reproduces on codex. Tool list: `exec_command, write_stdin, list_mcp_resources, list_mcp_resource_templates, read_mcp_resource, update_plan, request_user_input, apply_patch, view_image, get_goal, create_goal, update_goal, tool_search_tool, parallel`; `knowledge_search` **NO**, `task_create` **NO**, `notify` **NO**; no `personalclaw-core` MCP server. The turn's FIRST assistant chunk was the CLI's own notice — "Warning: Skill descriptions were shortened to fit the 2% skills context budget. Codex can still see every skill…" — i.e. the operator's real `~/.codex` skills/plugins loaded, and an adapter/CLI notice is rendered to the user as assistant prose |
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
| Project binding (context preamble + cwd) | WIRED | **DIVERGED** | the cwd half does not work | `C4` — `workspace_dir` was set to the scratch dir **before** binding and `pwd` inside the spawned CLI answered `/Users/golani/.personalclaw/workspace`; `gateway.log` shows `cwd=…/.dev-home/scratch pool_cwd=/Volumes/workplace/personalclaw-workspace`, so neither the session's dir nor the pool's reaches the process. The preamble half was not separately measured (`C18`) |
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
