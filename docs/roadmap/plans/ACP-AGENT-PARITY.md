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
