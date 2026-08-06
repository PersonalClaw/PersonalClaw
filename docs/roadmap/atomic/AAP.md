# ACP-AGENT-PARITY — atomic plans

**Source plan:** [`ACP-AGENT-PARITY`](../plans/ACP-AGENT-PARITY.md)  
**Code:** `AAP`  
**Source status:** proposed

Decomposed ACP-AGENT-PARITY into 10 atoms: 3 per-provider runtime-validation sweeps (Phase 1) gating 6 severity-ordered parity fixes (Phase 2 §2.1–§2.6) plus the honest-boundary parity doc (§2.7). Plan is PROPOSED — no execution log, no PRs — so all atoms are TODO. No hard cross-plan dependencies exist (Wave-0 standalone; guardrails/isolation touchpoints are non-blocking), so all edges are intra-plan.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `AAP-1` | ⬜ | Phase 1 validation — claude-code end-to-end sweep | — | Checked-in verified matrix column for claude-code with every audit cell re-marked CONFIRMED or DIVERGED at runtime (zero UNKNOWN cells) and its findings entered in the severity-ranked (P0/P1/P2/P3) gap inventory; incidental in-session bugs fixed per campaign doctrine. |
| `AAP-2` | ⬜ | Phase 1 validation — codex end-to-end sweep | — | Checked-in verified matrix column for codex with every audit cell CONFIRMED or DIVERGED at runtime (zero UNKNOWN cells: compaction, slash commands, context-%, subagent inject-back resolved) and findings added to the severity-ranked gap inventory. |
| `AAP-3` | ⬜ | Phase 1 validation — kiro-cli end-to-end sweep | — | mwinit freshness checked first (auth failures recorded as ENV not capability); checked-in verified matrix column for kiro-cli with every cell CONFIRMED or DIVERGED (personalclaw.json discovery, effort-pill, concurrent-sessions resolved) and findings in the gap inventory. |
| `AAP-4` | ⬜ | §2.1 MCP reachability — personalclaw-core reachable on all three providers (gap 1) | `AAP-1`, `AAP-2`, `AAP-3` | Per provider, 'list your tools' shows personalclaw-core tools and knowledge_search / task_create / notify / subagent_run all work as-a-user with correct session inject-back (session_pid_<pid> + env); seeding is idempotent, marker-scoped and reversible on disable (SC #2). |
| `AAP-5` | ⬜ | §2.2 Approval-gate coverage — host is permission authority (gap 2, the safety hole) | `AAP-1`, `AAP-2`, `AAP-3` | With task-mode=Ask a file write via any ACP provider yields a host approval card or block (never a silent write); deny-list rejects a denied command at the prompt and blocking PreToolUse fires pre-exec on every permission-surfaced ACP tool; residual not-gateable set enumerated per provider (SC #3). |
| `AAP-6` | ⬜ | §2.3 Unattended threading + runtime-agnostic loop breaker for ACP (gaps 3 & 5) | `AAP-1`, `AAP-2`, `AAP-3` | An unattended Code loop bound to each provider runs to completion or fails fast without wedging (Zed dialects via bypassPermissions incl. cron/scheduled; Kiro fail-fasts prompts deterministically); a deliberately failing-tool ACP session trips the circuit and aborts the turn with the standard breaker message (SC #4). |
| `AAP-7` | ⬜ | §2.4 Resume — live session/load via bundle session_files_dir (gap 6) | `AAP-1`, `AAP-2`, `AAP-3` | Gateway restart mid-conversation on a resume-capable provider shows 'Session resumed' with full-fidelity continuation; non-capable providers degrade to compressed history with an accurate 'restored from history' label rather than implying protocol resume (SC #5). |
| `AAP-8` | ⬜ | §2.5 Learning capture + tool-card fidelity + risk plumbing for ACP (gaps 4, 7, 8) | `AAP-1`, `AAP-2`, `AAP-3`, `AAP-4` | After an ACP turn a procedural outcome row exists (none under incognito); an ACP edit-tool turn renders a diff chip and structured input fields; the approval card for a personalclaw-core destructive tool shows its declared risk chip, not the heuristic one; native-only meta stays empty (not fabricated) where frames are empty (SC #6). |
| `AAP-9` | ⬜ | §2.6 Dialect asymmetry closure + project_id stamping (gaps 9 & 10) | `AAP-1`, `AAP-2`, `AAP-3`, `AAP-4`, `AAP-5` | ACP artifact_save stamps the session's bound project (server-side via PERSONALCLAW_SESSION_KEY); composer effort pill greys out (not silent no-op) on Kiro; Kiro 'plan mode' enforced by the host task-mode gate; slash commands labeled 'sent as text' where not negotiated; no dead persona UI for Zed dialects (SC #7 partial). |
| `AAP-10` | ⬜ | §2.7 Parity doc — docs/agents/acp-parity.md (the honest-boundary deliverable) | `AAP-1`, `AAP-2`, `AAP-3`, `AAP-4`, `AAP-5`, `AAP-6`, `AAP-7`, `AAP-8`, `AAP-9` | docs/agents/acp-parity.md exists stating per provider what is at parity, host-compensated, and a protocol/CLI constraint (each ABSENT written down with its reason + upstream watch item + verified CLI/adapter version); linked from each agent app README and the discovered-agents UI capability notes (SC #7). |

## Atom scopes

### `AAP-1` — Phase 1 validation — claude-code end-to-end sweep

**Status:** todo

Phase 1 — VALIDATION (§6 12-step checklist, claude-code column)

**Done when:** Checked-in verified matrix column for claude-code with every audit cell re-marked CONFIRMED or DIVERGED at runtime (zero UNKNOWN cells) and its findings entered in the severity-ranked (P0/P1/P2/P3) gap inventory; incidental in-session bugs fixed per campaign doctrine.

### `AAP-2` — Phase 1 validation — codex end-to-end sweep

**Status:** todo

Phase 1 — VALIDATION (§6 12-step checklist, codex column)

**Done when:** Checked-in verified matrix column for codex with every audit cell CONFIRMED or DIVERGED at runtime (zero UNKNOWN cells: compaction, slash commands, context-%, subagent inject-back resolved) and findings added to the severity-ranked gap inventory.

### `AAP-3` — Phase 1 validation — kiro-cli end-to-end sweep

**Status:** todo

Phase 1 — VALIDATION (§6 12-step checklist, kiro-cli column; incl. concurrent-sessions step 12)

**Done when:** mwinit freshness checked first (auth failures recorded as ENV not capability); checked-in verified matrix column for kiro-cli with every cell CONFIRMED or DIVERGED (personalclaw.json discovery, effort-pill, concurrent-sessions resolved) and findings in the gap inventory.

### `AAP-4` — §2.1 MCP reachability — personalclaw-core reachable on all three providers (gap 1)

**Status:** todo

Phase 2 §2.1 MCP reachability — gap 1 (prong A protocol mcpServers via client.py:419/481 + acp_session_provider; prong B marker-scoped config seeding per bundle)

**Done when:** Per provider, 'list your tools' shows personalclaw-core tools and knowledge_search / task_create / notify / subagent_run all work as-a-user with correct session inject-back (session_pid_<pid> + env); seeding is idempotent, marker-scoped and reversible on disable (SC #2).

### `AAP-5` — §2.2 Approval-gate coverage — host is permission authority (gap 2, the safety hole)

**Status:** todo

Phase 2 §2.2 Approval-gate coverage — gap 2 (most-restrictive mode forwarding + always-ask so tools hit chat_runner.py:1771 gate; extend CC config isolation to bundled default)

**Done when:** With task-mode=Ask a file write via any ACP provider yields a host approval card or block (never a silent write); deny-list rejects a denied command at the prompt and blocking PreToolUse fires pre-exec on every permission-surfaced ACP tool; residual not-gateable set enumerated per provider (SC #3).

### `AAP-6` — §2.3 Unattended threading + runtime-agnostic loop breaker for ACP (gaps 3 & 5)

**Status:** todo

Phase 2 §2.3 Unattended + loop support — gaps 3 and 5 (stop popping unattended at provider_bridge.py:534; auto-deny-with-reason on unattended sessions; extract _FailureBreaker/record_structural into a neutral observer for chat_runner)

**Done when:** An unattended Code loop bound to each provider runs to completion or fails fast without wedging (Zed dialects via bypassPermissions incl. cron/scheduled; Kiro fail-fasts prompts deterministically); a deliberately failing-tool ACP session trips the circuit and aborts the turn with the standard breaker message (SC #4).

### `AAP-7` — §2.4 Resume — live session/load via bundle session_files_dir (gap 6)

**Status:** todo

Phase 2 §2.4 Resume — gap 6 (register_acp_cli_entry passes session_files_dir; core registration helper provisions the dir; client.py:388-414 load path)

**Done when:** Gateway restart mid-conversation on a resume-capable provider shows 'Session resumed' with full-fidelity continuation; non-capable providers degrade to compressed history with an accurate 'restored from history' label rather than implying protocol resume (SC #5).

### `AAP-8` — §2.5 Learning capture + tool-card fidelity + risk plumbing for ACP (gaps 4, 7, 8)

**Status:** todo

Phase 2 §2.5 Learning + fidelity — gaps 4, 7, 8 (procedural drain off neutral EVENT_TOOL_* stream; populate structured-input from translate.py rawInput + kind-based diff chips; declared-risk map for core MCP tools + tool_kind floors)

**Done when:** After an ACP turn a procedural outcome row exists (none under incognito); an ACP edit-tool turn renders a diff chip and structured input fields; the approval card for a personalclaw-core destructive tool shows its declared risk chip, not the heuristic one; native-only meta stays empty (not fabricated) where frames are empty (SC #6).

### `AAP-9` — §2.6 Dialect asymmetry closure + project_id stamping (gaps 9 & 10)

**Status:** todo

Phase 2 §2.6 Dialect asymmetry closure (gap 9) + project stamping (gap 10) (surface dialect caps in discovered-agent payload; stop popping project_id/extra_tool_roots at provider_bridge.py:541; server-side project binding in mcp_core)

**Done when:** ACP artifact_save stamps the session's bound project (server-side via PERSONALCLAW_SESSION_KEY); composer effort pill greys out (not silent no-op) on Kiro; Kiro 'plan mode' enforced by the host task-mode gate; slash commands labeled 'sent as text' where not negotiated; no dead persona UI for Zed dialects (SC #7 partial).

### `AAP-10` — §2.7 Parity doc — docs/agents/acp-parity.md (the honest-boundary deliverable)

**Status:** todo

§2.7 The parity doc (per-provider capability statement generated from Phase 1 matrices + Phase 2 end-state)

**Done when:** docs/agents/acp-parity.md exists stating per provider what is at parity, host-compensated, and a protocol/CLI constraint (each ABSENT written down with its reason + upstream watch item + verified CLI/adapter version); linked from each agent app README and the discovered-agents UI capability notes (SC #7).

