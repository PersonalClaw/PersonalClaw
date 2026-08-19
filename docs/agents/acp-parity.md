# ACP Agent Parity — Per-Provider Capability Statement

PersonalClaw can run a turn on an external coding CLI over the Agent Client Protocol (ACP)
instead of on its own native runtime. This document states, per provider, what that costs.

## How to read this

**ACP providers are not at native parity, and this document exists because the gap is
documented rather than hidden.** The native runtime holds the tool registry, the
pre-execution gate, the failure breaker and the learning drain *inside* its own loop. An ACP
provider is a separate process speaking a protocol, so anything the host cannot see or cannot
reach across that protocol is either supplied by the host from outside, or simply not there.

Every capability below is in exactly one of three buckets:

| Bucket | Meaning |
|---|---|
| **At parity** | Works on this provider the way it works on the native runtime. |
| **Host-compensated** | The CLI does not provide it; PersonalClaw supplies it from the host side, sometimes with a boundary the host cannot cross. That boundary is stated on the row. |
| **Protocol or CLI constraint** | Cannot work today. Each row carries its reason, the watch item (what would have to change, and where), and the version it was measured against. |

A fourth list per provider — **not yet measured** — is not a bucket. It exists so a reader can
tell *measured absent* from *never driven*. A cell in that list is neither working nor broken
as far as this document is concerned; it has no runtime observation behind it, and no claim
here rests on it.

**Versions are stated because a CLI can change any week.** Every constraint row names the
build it was measured against. Two of the three columns were measured against *older* builds
than the ones installed today, and one column was measured against the same build and then
failed to reproduce — see "Coverage" below before treating any row as current fact.

**Evidence ids** (`O4`, `C12`, `K30`, `G27`) refer to the observation ledgers and gap
inventory in [`../roadmap/plans/ACP-AGENT-PARITY.md`](../roadmap/plans/ACP-AGENT-PARITY.md).
Every claim in this document traces to a ledger row, a `G`-entry, or a measured version below.
Nothing here was derived by reading code alone.

## Verified versions

Measured on the development host on 2026-08-19:

| Component | Version | Notes |
|---|---|---|
| `claude` (Claude Code CLI) | `2.1.234.669` | ASBX build, channel `stable` |
| `@agentclientprotocol/claude-agent-acp` | `0.62.0` | adapter; claude-code speaks ACP through it |
| `codex` | `0.146.1.360` | channel `stable` |
| `@agentclientprotocol/codex-acp` | `1.1.7` | adapter |
| `kiro-cli` | `2.18.1` | native ACP — no adapter in the path |
| `gemini` | **not installed** | catalog row and bundle exist; provider unverified |

**The adapters are installed per-home, not globally.** They live at
`<PERSONALCLAW_HOME>/acp-adapters/node_modules/.bin/` — a reader who checks `PATH` for
`claude-agent-acp` or `codex-acp` finds nothing and will wrongly conclude the adapters are
missing. `kiro-cli` is the exception: it speaks ACP natively, so there is no adapter and no
adapter version to pin.

## Coverage — what has actually been measured

The audit that produced these columns defines **63 cells** — one per capability, the same 63
for every provider. Each column was filled by driving the system as a user (dashboard and API,
one isolated home per sweep), never by reading code. Marks: `CONFIRMED` (runtime matched the
audit's prediction), `DIVERGED` (runtime contradicted it), `ENV` (an environment limit, never a
capability verdict), `NOT-EXERCISED` (no runtime observation).

| Provider | CONFIRMED | DIVERGED | ENV | NOT-EXERCISED | Sweep |
|---|---|---|---|---|---|
| claude-code | 35 | 6 | 0 | **22** | 2026-08-17, adapter `0.60.0`, `claude` `2.1.233.669` |
| codex | 33 | 10 | 0 | **20** | 2026-08-17, adapter `1.1.4`, `codex` `0.146.1.359` |
| kiro-cli | 44 | 16 | 1 | 2 | 2026-08-17/18, plus a follow-up sweep 2026-08-18, `kiro-cli` `2.18.1` |
| gemini-cli | — | — | — | 63 | never driven; binary not installed |

Three things a reader must carry into every section below.

**1. kiro's tool-axis rows were measured, then failed to reproduce on the same CLI build.** A
re-drive on 2026-08-19 against the same `kiro-cli 2.18.1` asked the CLI to enumerate its
callable tools and got **`NO_TOOLS`**; asked for one shell command, it answered that it had no
shell tool available and correctly refused to fabricate output. The matrix row it contradicts
(`K4`) measured **57 tools** on that same version with its own `shell`/`read`/`write` working.
A control in the same gateway and the same isolated home — a second session bound to
claude-code — listed nine callable tools, so host-side tool exposure was healthy and the
failure is specific to kiro. The cause is **not** established. Every kiro tool row below is
therefore marked *measured, not reproduced*: read it as history, not as current fact.

**2. claude-code and codex are not complete columns.** 22 and 20 cells respectively have no
runtime observation. Their sections list those cells grouped by why, and the summary tables do
not imply anything about them.

**3. Two kiro cells are unreachable as-a-user by construction**, not for want of a fixture:
the skill-ladder review has no forced-run surface (`G44`), and an empty turn cannot be produced
without stream injection. Both are named in kiro's section with the reason.

## Constraints that hold on all three providers

These rows were measured on more than one provider. Each provider section below applies this
table in addition to its own rows. "Watch" names what would have to change and where — the
CLI, the adapter, the ACP protocol, or this repo. A host-owned row has no upstream watch item:
it is ours.

| Axis | Capability | Why it does not work | Watch — what must change, where | Measured against |
|---|---|---|---|---|
| Tools | Typed tool-result meta (`content_type`, `raw_ref`, `truncated`, `original_length`, `recovery_hints`) | The protocol's result frames carry none of these fields. The host leaves them empty rather than fabricating values — every ACP `tool_result` reads `content_type: ""`, `raw_ref: ""`, `truncated: false`, `original_length: null`, `recovery_hints: []` (`O6`, `C5`, `K5`) | **ACP protocol** — result metadata would have to exist on the wire. Empty is the honest shape until it does | adapter `0.60.0` / `1.1.4`, `kiro-cli 2.18.1` |
| Tools | Structured tool-input rendering and file-change diff chips | Not a protocol limit — the raw material is already on the wire and the host drops it. claude sends `kind: "read"｜"edit"｜"execute"` plus a `rawInput` JSON (`O6`); codex sends a real `{type: "diff", oldText, newText, path}` object (`C5`, `G22`); kiro sends a unified diff (`K12`). The frontend receives `input: null` and zero diff keys | **Host seam** (`G9`, `G22`; atom `AAP-8`). Nothing upstream | adapter `0.60.0` / `1.1.4`, `kiro-cli 2.18.1` |
| Session mechanics | Slash commands and `/compact` | Both hard-error `-32601 "Method not found": _vendor.dev/commands/execute` and the turn fails; there is no plain-prompt fallback. The failure is byte-identical across two different adapters and a native CLI (`O23`, `C8`, `K11`), which makes it host-side rather than one adapter's gap | **Host seam** (`G4`; atom `AAP-9`) — capability-gate `stream_command` and degrade to text | adapter `0.60.0` / `1.1.4`, `kiro-cli 2.18.1` |
| Session mechanics | Resume across a gateway restart (`session/load`) | All three advertise the `loadSession` capability (`O1`, `C1`, `K20`) and the host never attempts it. The restart clears the session's `acp_provider` — and its mode, effort and `workspace_dir` — so the next turn silently resolves on the **native** axis: a different runtime, different tools, different confinement (`O16`, `C16`, `K20`). On kiro, re-binding then 404s until a `POST /api/chat` touches the session (`K21`) | **Host seam** (`G5`, `G30`; atom `AAP-7`). The protocol half exists; no agent bundle passes a `session_files_dir`, so the load path has no session file to find | adapter `0.60.0` / `1.1.4`, `kiro-cli 2.18.1` |
| Session mechanics | Context-% accounting | Worse than absent: a `context_usage` frame is emitted on **every** turn with `pct: 0.0`, and the turn line prints `context 0%` — including turns carrying 13–18 KB of injected context (`O7`, `C4`, `K4`). The UI states a number no backend supplied | **Adapters** would have to report token stats; failing that, **host seam** must omit the chip instead of printing zero (`G8`; atom `AAP-8`) | adapter `0.60.0` / `1.1.4`, `kiro-cli 2.18.1` |
| Learning / memory | Procedural-outcome capture (the tool-outcome drain) | Zero rows after multi-tool ACP turns on all three (`O12` — 6 tool calls, nothing; `C14`; `K17` — the only rows came from a **0-tool** correction turn, and the self-model row it wrote asserts `tools: []`). The drain reads an accumulator the native runtime keeps in its own loop; the ACP provider exposes no equivalent | **Host seam** (`G7`; atom `AAP-8`) — accumulate off the neutral tool-call/tool-result stream | adapter `0.60.0` / `1.1.4`, `kiro-cli 2.18.1` |
| Approvals / safety | A CLI-side refusal never reaches the audit trail | When the CLI refuses on its own, no frame reaches the host and no SEL row is written: claude's own deny list refused `git push --dry-run` invisibly (`O21`, `G11`), and codex's `request_user_input` failed CLI-side with `0 tool calls` counted and no row (`C9`, `G25`). The host's audit therefore under-reports what the CLI declined | **Adapters** would have to emit a refusal frame | adapter `0.60.0` / `1.1.4` (not driven on kiro) |
| Prompt-side context | Mid-turn queue-steering | The protocol has no mid-turn injection seam. A message sent during a live ACP turn is queued and runs as its own turn afterwards — `{"queued": true}`, then `queue_pop` (`C10`, `K15`). Queue-then-drain is the documented ACP semantic, not a bug | **ACP protocol** | adapter `1.1.4`, `kiro-cli 2.18.1` (claude's probe missed the window — `O26`, not measured) |
| Prompt-side context | `@prompt` expansion | Provider-independent by construction: expansion is composer-side. A message carrying a literal `@name` reaches the CLI unexpanded and nothing on the ACP path expands it (`K30`, `K31`); the render endpoint works when called directly and the composer is its caller | **Not a provider gap** — whatever the composer substitutes is what any provider receives | `kiro-cli 2.18.1` (not driven on claude or codex) |

### Host compensation that landed after these sweeps

The five mechanisms below exist in the host today but post-date the matrix columns that
measured their cells. Where a mechanism has only been driven as-a-user on one provider, that is
said here rather than generalized — a landed mechanism is not a measured one.

| What the host now supplies | Driven as-a-user on | Boundary |
|---|---|---|
| The `personalclaw-core` tool surface (knowledge, tasks, artifacts, workflows, subagents, `notify`) passed in `mcpServers` at `session/new` | **kiro only** (`K32` — the turn's first tool call was `@personalclaw-core/get_context`, and it raised its own approval card). claude and codex last measured "no `personalclaw-core` surface at all" (`O4`, `C4`) at their earlier tips and have not been re-driven | A CLI that ignores protocol-passed `mcpServers` needs config seeding instead (below) |
| Config seeding for kiro's agent discovery: the generated `personalclaw.json` is made discoverable from a root kiro actually reads, via a marker-scoped, receipt-recorded, reversible symlink | **not re-driven** since `K6`/`G31` measured the file "generated correctly and stored where kiro never looks" | Seeding never edits a user file; a pre-existing path we did not write is refused, not clobbered |
| Permission authority: the host refuses to hand a Zed dialect a self-approving mode (`acceptEdits`/`dontAsk`/`bypassPermissions`) outside an explicit unattended session, and the deny-list is evaluated against the **real command** rather than the truncated permission title | **not re-driven** as a column | Covers only tools the CLI chooses to escalate — see the not-gateable residual below |
| Unattended fail-fast: an approval request arriving on an unattended session is auto-denied with a reason and the turn ends, instead of parking forever waiting for a human | **kiro only** (`K41` — `auto-denied: unattended run, no one to approve`, `[DONE]` in 5.2 s, nothing left pending, the requested file never created). The claude and codex cells were never driven | kiro has no permission-mode axis, so it gets the fail-fast half only — there is no restrictive mode to forward |
| The runtime-agnostic failure breaker and structural loop detection, run by the host off the neutral event stream for ACP turns | **not re-driven**: the last as-a-user measurement of these cells (`O24`, `C10`, `K15` — six consecutive failing tool calls, zero warn/block/circuit/steering) predates it and measured them ABSENT | The host observer can abort or steer **between** protocol events; it cannot block the next tool call pre-execution the way the native breaker does |

## claude-code

`claude` `2.1.234.669` through `@agentclientprotocol/claude-agent-acp` `0.62.0`, Zed dialect
`claude-code`. **The column was measured on adapter `0.60.0` and `claude` `2.1.233.669`, and 22
of its 63 cells were never driven** — read the two tables below as 41 measured cells, not as a
complete statement.

### At parity

| Axis | Capability | Evidence |
|---|---|---|
| Prompt-side context | Memory recall injection at turn 0 | `O9`, `O15` — the injection fires and the CLI quoted injected memory and history text back verbatim |
| Prompt-side context | Task-mode framing | `O14` — a fresh plan-mode session's context contains `## Task mode: Plan` and "you MUST NOT make any edits" |
| Prompt-side context | Compressed thread-history bootstrap into a new process | `O14`, `O15` — a brand-new session's context replayed prior turns verbatim, including sibling-session history |
| Approvals / safety | Interactive approval cards | `O5` — four cards in one turn, each resolvable through the approve route |
| Approvals / safety | Task mode enforced before approval | `O13` (ask blocks a `Write` and a read-only `ls`), `O19`/`O24` (plan blocks every `Write`), SEL rows carrying `reason: task_mode:ask` / `task_mode:plan` |
| Approvals / safety | SEL audit of every executed tool | `O10` — hash-chained `tool_invocation` rows with `tool_kind` and `metadata.risk`, plus `approved`/`denied`/`rejected` decisions. Across 44 audited ACP tool events no tool executed without reaching the host gate — with the contingency in the constraint table below |
| Approvals / safety | Plan mode reaches the CLI's own plan mode | `O19` — the CLI reports "Claude Code's CLI plan mode" and substitutes "Ready to code?" for the edit. **Only when plan is set before the session's first turn** (`G12`); set mid-conversation it reverts to Agent and the host gate is the only enforcement |
| Learning / memory | Preference-facet capture, correction→lesson review | `O22` — `learned` activity events on the correction turn, with no model provider needed. Extraction quality is poor: the facet learned was the fragment "never more" (`G16`) |
| Session mechanics | Variants / regenerate | `O25` — the regenerated message carries `variants` and `variant_idx` |
| Session mechanics | Edit & resend, fork | `O25` — the fork carries all 24 messages, **but loses the ACP binding** (`acp_provider: ""`, `G13`) |
| Session mechanics | Per-session model override | `O20` — the CLI named the exact pinned model id back |
| Session mechanics | Warm pool / instant start | `O17` — the pool path was exercised; it was cold on this run (`pool_size=0`), so every turn cold-started |
| Session mechanics | Turn telemetry (event and tool counts) | `O7` — `Turn complete: 106 events, 6 tool calls`. The context-% part of the same line is fabricated (shared table) |
| Session mechanics | Reasoning effort — **host side only** | `O2` — the adapter advertises five efforts; `O20` — the host accepted and echoed `low`. The CLI cannot self-report its effort, so whether it *honored* the value was never measured |

### Host-compensated

| Axis | Capability | What the host supplies | Boundary |
|---|---|---|---|
| Prompt-side context | Skills | The skills index arrives as prompt text — the CLI reported "the session context references … `skill_invoke`" (`O4`) | The `skill_invoke` / `skill_search` / `skill_remember` **tools** are absent from the CLI's list; the index is text, not an executable ladder |
| Approvals / safety | Plan and ask enforcement | The host gate blocks non-plan mutations at the permission prompt regardless of what the CLI's own mode is (`O19`, `O24`) | Only covers tools the CLI escalates |
| Approvals / safety | Read auto-approve (`trust_reads`) | A read-only `pwd` auto-resolved with no card (`O8`) | Coarse and mis-calibrated in both directions: the same heuristic labelled a read-only `pwd; ls` "destructive" (`O7`, `O10`, `G10`), and ask mode denied a read-only `ls` outright (`O13`) |

Plus everything in "Host compensation that landed after these sweeps" — none of which has been
re-driven on claude-code.

### Protocol or CLI constraint

| Axis | Capability | Why it does not work | Watch — what must change, where | Measured against |
|---|---|---|---|---|
| Approvals / safety | Host gate coverage is **contingent, not structural** | Coverage measured total (44 events, all surfaced) only because config isolation is off by default and this operator's real `~/.claude` happened to auto-approve nothing. The spawned CLI loaded that real config, enumerated the operator's own MCP servers (`O4`) and wrote into `~/.claude/plans/` and `~/.claude/projects/…/memory/` (`O19`, `O22`). An operator with `permissions.allow` entries gets silent execution with no host card (`G2`) | **Bundle + host seam** (atom `AAP-5`) — make the isolated CLI config the default for host-managed sessions. The opt-in hardening flag already exists and is OFF by default | adapter `0.60.0`, `claude 2.1.233.669` |
| Tools | The CLI's file and shell tools are not confined to the session's workspace | `Read`/`Write`/`Terminal` ran in `~/.personalclaw/workspace` regardless of the session's `workspace_dir`, and reached `/Volumes/…`, `~/.claude` and `~/.personalclaw` freely (`O8`, `O17`, `O5`, `O19`, `O22`). The cwd does reach the pool and is dropped below it (`G1`) | **Host seam**. A later kiro measurement found this fixed for a directly-bound session and **still live for an agent-profile-bound one** (`K28`, `K50`, `G39`); claude-code has **not** been re-driven since, so its rows here predate that fix | adapter `0.60.0`, `claude 2.1.233.669` |
| Tools | `AskUserQuestion` card | The card fires only if the CLI exposes an identically-named tool; the CLI's full tool list contains none (`O4`) | **CLI**, or the `personalclaw-core` surface supplying one | adapter `0.60.0` |
| Tools | Per-turn tool retrieval / progressive disclosure | The CLI enumerated only its OWN tools, including its own retrieval tool; no host-injected `tool_search`/`tool_schema` appeared (`O4`) | **Host seam** via the MCP surface | adapter `0.60.0` |
| Tools | External MCP servers are the **operator's**, not PersonalClaw's | The spawned CLI enumerated the operator's own servers from the real `~/.claude`; nothing from PersonalClaw's `mcp.json` (`O4`) | **Bundle + host seam** — the same isolation and seeding prong as the row above | adapter `0.60.0` |
| Prompt-side context | `project_id` → artifact stamping | Absent in the stronger sense: `artifact_save` was not reachable at all, so there was nothing to stamp (`O4`) | **Host seam** — closes with the `personalclaw-core` surface (landed, not re-driven here) plus `project_id` threading (atom `AAP-9`) | adapter `0.60.0` |
| Session mechanics | Concurrent sessions on one adapter process | Two concurrently-bound sessions held two different adapter PIDs (`O11`); the dialect declares no concurrency support | **Adapter** would have to interleave sessions; the flag stays false until a spike proves it | adapter `0.60.0` |
| Session mechanics | Persona / agent selection | Discovery returns exactly one agent with `provider_agent: ""` — one base agent per adapter, so the picker has no persona rows to offer, and there is no dead UI (`O2`) | **Adapter / CLI** | adapter `0.60.0` |

### Not yet measured (22 of 63 cells)

No runtime observation exists for these; they are neither working nor absent here. Grouped by
what was missing, exactly as the sweep recorded it.

1. **Needs a model provider in the sweep home** (5): unattended mode, auto-nudge re-arm,
   skill-ladder review, memory consolidation, the loop half of the failure-breaker check. A
   loop or cron run failed on `no model provider resolves for use case 'chat'|'background'`
   (`O16`) before any ACP worker turn.
2. **Needs a fixture that was not built** (9): knowledge `@`-mention injection,
   attachment/paste extraction, `@prompt` expansion, agent-profile system prompt, per-agent
   approval floor, blocking PreToolUse hooks, the other five hook kinds, tool-disable prefs,
   persona injection.
3. **Needs a timing or failure injection that did not land** (5): queued messages and
   queue-steering (`O26` — the probe turn finished 1.2 s early), cancelled-turn preamble
   re-injection, empty-turn auto-retry, pipe-death auto-retry.
4. **No as-a-user entry point** (3): dry-run replay, OS sandbox confinement, and trust/YOLO
   auto-approve — the last deliberately left off so the gate itself stayed measurable.

## codex

`codex` `0.146.1.360` through `@agentclientprotocol/codex-acp` `1.1.7`, Zed dialect `codex`.
**The column was measured on adapter `1.1.4` and `codex` `0.146.1.359`, on a host with a working
model provider, and 20 of its 63 cells were never driven.**

### At parity

| Axis | Capability | Evidence |
|---|---|---|
| Prompt-side context | Memory recall injection at turn 0 | `C4`, `C7`, `C12` — `Injected 10,403 / 15,569 / 11,169 chars of context (memory, lessons, history, episodic)` on each fresh session; `C6` shows an injected framing line quoted back verbatim |
| Prompt-side context | Compressed thread-history bootstrap | Every turn spawns a new adapter process and continuity still held across 10 turns; `C6` shows prior-turn text replayed into a later turn |
| Prompt-side context | Task-mode framing — **presence only** | `C6` — the CLI quoted `## Task mode: Agent`, but on a session whose earlier turns ran in agent mode, so replayed history explains it equally well. The fresh-session control died on a tool denial, and codex otherwise refuses to quote its context (`C18`, `G26`). Whether the block's value tracks the live mode is **not** established on codex |
| Approvals / safety | Interactive approval cards | `C5` (two cards in one turn, both resolvable), `C11` (a card rejected, the tool did not run, the turn completed gracefully) |
| Approvals / safety | Session trust auto-approve | `C17` — after one `trust` action the next write ran with no card, surfacing a `tool_call` frame with `"auto": true` |
| Approvals / safety | Task mode enforced before approval, and trust cannot bypass it | `C17` — with session trust ACTIVE an ask-mode write was still denied (`reason: task_mode:ask`) and the file never appeared. This closes the bypass question that claude's column left partial |
| Approvals / safety | SEL audit of every executed tool | `C5`, `C10`, `C17` — hash-chained rows with `tool_kind` and `metadata.risk` for every executed tool. Two blind spots: every permission and decision row is named `unknown` (constraint below), and a CLI-side refusal is invisible (shared table) |
| Learning / memory | Preference-facet capture, correction→lesson review | `C13`, `C14` — `learned` events plus a `per_turn｜lesson` row in the learning staging table and two `semantic_memory` rows. Same poor extraction as claude (`G16`) |
| Session mechanics | Variants / regenerate | `C15` — two variants persisted with `variant_idx: 1` |
| Session mechanics | Edit & resend, fork | `C15` — the fork carries all 14 messages, **and loses the ACP binding** (`G13`) |
| Session mechanics | Queued messages, end to end | `C10` — `queue_push` with a `queue_id` during the turn, then `queue_pop` → the queued message ran as its own turn |
| Session mechanics | Warm pool / instant start | `gateway.log` — the pool path ran but was cold on every turn (`pool_size=0`), so a fresh adapter was spawned per turn |
| Session mechanics | Discovered-agent binding | `C3` — the bind round-trips. *Ephemeral* is literal: it is lost on a fork (`C15`) and on a restart (`C16`) |
| Session mechanics | Turn telemetry (event and tool counts) | `C5`, `C10` — `Turn complete: 103 events, 3 tool calls` |

### Host-compensated

| Axis | Capability | What the host supplies | Boundary |
|---|---|---|---|
| Prompt-side context | Skills | The index arrives as prompt text — SEL carries `skill_surface`/`surfaced` rows and the log records `Surfaced skills:` (`C4`) | `skill_invoke`/`skill_search`/`skill_remember` are not in the CLI's tool list |
| Approvals / safety | Plan mode | Plan is enforced **only** by the host gate: plan set before a fresh session's first turn, and the CLI still called `apply_patch` and never called its own plan tool (`C7`) — the host denied it. This is the shape the audit predicted for kiro, not for codex | The CLI has no plan behavior of its own to enter, and a task-mode denial ends the turn (constraint below) |
| Approvals / safety | Read auto-approve (`trust_reads`) | `pwd`, a file read and six `cat` calls auto-resolved as `safe` (`C4`, `C5`, `C10`) | Title-driven — the adapter titles a shell `exec_command` "Read file '…'". **Not** spoofable: `cat X && rm Y` is still classified `execute`/`destructive` and gated (`C11`) |

Plus everything in "Host compensation that landed after these sweeps" — none of it re-driven on
codex.

### Protocol or CLI constraint

| Axis | Capability | Why it does not work | Watch — what must change, where | Measured against |
|---|---|---|---|---|
| Approvals / safety | There is **no config-isolation lever at all** | The bundle applies none by design, so every host-managed session inherits the operator's real environment: all **12** of the operator's own MCP servers live in-session including write-capable internal ones (`C12`), the operator's skills and plugins load (`C4`), each session drags **31** descendant processes (`C14`), and the CLI writes transcripts of host-driven turns plus shell snapshots into the real `~/.codex/` (`C19`). `G17` | **Bundle + host seam** (atom `AAP-5`): the mechanism must be *built* — codex honors `CODEX_HOME` — there is nothing to flip. Until then the host neither declares nor gates that tool surface | adapter `1.1.4`, `codex 0.146.1.359` |
| Approvals / safety | The approval card cannot name the tool it is approving | Every `approval` frame and SEL decision row reads `tool: "unknown"` with `tool_kind: ""` (`C5`, `C17`), so the card shows a raw payload and the audit trail records `unknown ｜ approved`. codex's permission payload is only `{toolCallId, kind, status}`; the human title lives on the *preceding* `tool_call` frame. This also corrects claude's `G10` conclusion — the `kind` **is** present on codex's permission payload | **Host seam** (`G18`; atom `AAP-8`) — the same function already correlates the input across those two frames, so the title is one cache lookup away | adapter `1.1.4` |
| Approvals / safety | A task-mode denial kills the whole turn | When ask or plan mode denies a tool the turn produces **no `tool_result`**, ends with `*Conversation interrupted*`, and the model never receives a denial it could react to (`C6`, `C7`, `C17`) — nor does the user get claude's explanatory line. A *rejected card* on the same provider is graceful (`C11`). So the automatic policy path is the destructive one, and a plan-mode session cannot answer a question if it decides to touch a tool first | **Host seam** (`G19`; atoms `AAP-6`/`AAP-9`) | adapter `1.1.4` |
| Session mechanics | The per-session model pin stops applying after the first turn | `ACP model: openai.gpt-5.4` on turn 1, `ACP model: auto (from agent config)` on turn 2 of the same session (`C12`, `C13`), while the activity line keeps printing the pinned id — the user is told a model that is no longer in force, with no restart needed to reach that state | **Host seam** (`G20`; atom `AAP-7`) | adapter `1.1.4`, `codex 0.146.1.359` |
| Session mechanics | Reasoning effort | The axis does not exist: discovery returns `supported_efforts: []` (`C2`), yet a bind with `reasoning_effort: "low"` is accepted, persisted and echoed back (`C12`) — the composer offers a control the provider cannot honor | **CLI** for the axis itself; **host seam** must key the control off `supported_efforts` (`G21`; atom `AAP-9`) | adapter `1.1.4` |
| Tools | `AskUserQuestion` card | codex *has* a `request_user_input` tool and it fails CLI-side ("unavailable in Default mode"); no card, no SEL row, `0 tool calls` counted (`C9`, `G25`). Absent rather than merely unreachable | **CLI** — the tool would have to work in the mode the host runs it in | adapter `1.1.4`, `codex 0.146.1.359` |
| Tools | The CLI's file and shell tools are not confined to the session's workspace | `exec_command`/`apply_patch` ran in `~/.personalclaw/workspace` regardless of the session's `workspace_dir` and reached arbitrary absolute paths freely (`C4`, `C5`, `C17`) — same defect family as claude's | **Host seam** (`G1`). Later measured fixed on a *directly-bound* kiro session and still live on an agent-profile-bound one (`G39`); codex has not been re-driven | adapter `1.1.4` |
| Tools | External MCP servers are the **operator's** | 12 servers, all from the operator's real config; nothing from PersonalClaw's `mcp.json` (`C12`) | **Bundle + host seam** — the isolation row above | adapter `1.1.4` |
| Prompt-side context | `project_id` → artifact stamping | `artifact_save` was not reachable at all, so there was nothing to stamp (`C4`) | **Host seam** — the landed `personalclaw-core` surface plus `project_id` threading (atom `AAP-9`); not re-driven on codex | adapter `1.1.4` |
| Session mechanics | Concurrent sessions on one adapter process | Three concurrently-bound sessions held three different adapter PIDs (`C14`) | **Adapter** | adapter `1.1.4` |
| Session mechanics | Persona / agent selection | Exactly one agent with `provider_agent: ""` (`C2`) | **Adapter / CLI** | adapter `1.1.4` |
| Session mechanics | Stopping a turn during a tool call | `stop` returned `{"ok": true}` and emitted `state: stopping`; one second later the turn ended with `ACP prompt timed out` (`C18`) — the user asked to stop and was shown a timeout failure | **Host seam** (`G24`) | adapter `1.1.4` |
| Session mechanics | CLI and adapter notices are rendered as assistant prose | Every fresh session's first assistant chunk is the CLI's own "Warning: Skill descriptions were shortened…" (`C4`, `C7`), persisted as an assistant message, so it also feeds compressed history and the auto-title prompt | **Host seam** (`G23`; atom `AAP-8`) | adapter `1.1.4` |

**Negative results worth keeping** (so nobody re-chases them): the adapter's descendant tracking
*does* reap the MCP fleet — after the gateway was killed, no adapters and no orphaned young MCP
processes remained (`C14`, `C19`); and codex wrote **nothing** into the real `~/.personalclaw`
despite running with its cwd inside it (`C19`).

### Not yet measured (20 of 63 cells)

1. **Needs a model provider for the loop path** (4): unattended mode, auto-nudge re-arm,
   skill-ladder review, memory consolidation — a loop run failed on provider resolution (`C16`)
   before any ACP worker turn.
2. **Needs a fixture that was not built** (10): knowledge `@`-mention, attachment/paste,
   `@prompt` expansion, agent-profile system prompt, per-agent approval floor, blocking
   PreToolUse hooks, the other five hook kinds, tool-disable prefs, persona injection,
   incognito/restricted no-write guarantees.
3. **Needs timing or failure injection that did not land** (2): empty-turn auto-retry,
   pipe-death auto-retry.
4. **No as-a-user entry point** (2): dry-run replay, OS sandbox confinement.
5. **Blocked by codex's refusal to disclose its own context** (1): cancelled-turn preamble
   re-injection — the cancel was performed (`C18`) but the re-injection could not be read back
   (`G26`).
6. **No deny-listed command was driven** (1): the hard deny-list cell. The `rm` in `C5` reached
   a card rather than a pre-block, but that command is not known to be on the list, so it proves
   nothing either way.

## kiro-cli

`kiro-cli` `2.18.1`, speaking ACP natively — no adapter in the path, so nothing here is an
adapter version. Core's `default` dialect, which has no permission-mode axis. This is the most
completely measured column (2 of 63 cells unmeasured, and both for stated structural reasons)
and the one with a live contradiction.

> **Version-drift warning — the tool-axis rows below were measured and then failed to
> reproduce on the same build.** A re-drive on 2026-08-19 against this same `kiro-cli 2.18.1`
> got **`NO_TOOLS`** when it asked the CLI to enumerate its callable tools, and the CLI answered
> that it had no shell tool available in that turn (correctly refusing to fabricate a `pwd`
> result). `K4` — the row every tool claim below rests on — measured **57 tools** on this same
> version with its own `shell`/`read`/`write` working. A control session on claude-code in the
> same gateway and the same isolated home listed nine callable tools, so host-side tool exposure
> was healthy and the failure is kiro-specific. **The cause is not established.** Candidates
> named at the time: the operator's own MCP fleet not starting under the gateway's environment
> (that fleet is where all 57 tools came from), singleton contention with a concurrently running
> fleet, or a kiro-side change within `2.18.1`. Every row tagged **(tool axis)** below is history
> — a measurement that did not survive its first re-drive — not current fact. It also means the
> `pwd` confinement question cannot be re-answered from that re-drive: with no shell tool there
> was nothing to escape with.

### At parity

| Axis | Capability | Evidence |
|---|---|---|
| Prompt-side context | Memory recall injection at turn 0 | `K4`, `K22` — `Injected 10,471 / 7,283 / 6,971 / 4,868 chars of context (memory, lessons, history, episodic)` |
| Prompt-side context | Knowledge `@`-mention / picker injection | `K30` — with a knowledge item bound to the turn the CLI quoted it verbatim, including its marker string |
| Prompt-side context | Attachment / paste text extraction | `K30` — the CLI quoted the extracted attachment marker verbatim |
| Prompt-side context | Agent-profile system prompt and voice layer | `K30` — a profile carrying a distinctive `system_prompt` was bound and the CLI quoted its mandatory marker verbatim |
| Prompt-side context | Persona injection | `K30` — with the theme set on the turn, the CLI quoted the persona instruction verbatim |
| Prompt-side context | Cancelled-turn preamble re-injection | `K35` — a `sleep 40` tool call stopped mid-turn (`outcome: soft`); the next turn quoted `[PREVIOUS TURN WAS CANCELLED BY THE USER -- context restore]` and the line after it verbatim |
| Prompt-side context | Compressed thread-history bootstrap | Continuity held across 42 messages, and a fresh process on a 12-message session still answered in context (`K24`). The mechanism differs from the Zed adapters: kiro reuses one process per session (`K8`), so most turns need no re-bootstrap |
| Approvals / safety | Interactive approval cards | `K5` (a card raised, resolved, the tool then ran), `K12` (three cards in one turn) |
| Approvals / safety | Session trust auto-approve | `K15` — one `trust` action and the next five tool calls surfaced `"auto": true` with no card |
| Approvals / safety | Per-agent approval floor ("Always allow") | `K36` — a profile with `approval_mode: auto`, background auto-approver **off**: the command executed with no card at all and the session came back `trust: true`. The floor is implemented by flipping session trust |
| Approvals / safety | Task mode enforced before approval | `K14` (ask-mode write denied, file never created, SEL `denied ｜ task_mode:ask`), `K22` (plan-mode write denied). The *trust-cannot-bypass* half was established on codex and not separately re-driven here |
| Approvals / safety | Hard deny-list, pre-execution | `K25` — `git … push` blocked before execution with the pattern named to the user (`Blocked by security policy: *git*push*`) and a SEL `denied` row. **The only positive result for this cell across all three sweeps**; claude measured it absent and codex never drove it |
| Approvals / safety | SEL audit of every executed tool | `K12`, `K13`, `K15` — hash-chained rows for every executed tool *including the ungated ones*, each carrying the real operation title, so codex's "every row is named `unknown`" does not reproduce. One internal contradiction: a single read produced `invoked ｜ risk: safe` and `approved ｜ risk: caution` for the same call (`G35`) |
| Learning / memory | Preference-facet capture, correction→lesson review | `K16`, `K17` — `learned` events plus `facet_veto` and `after_turn_review` rows. Extraction is worse than "poor" here: on an injected-context turn the extractor swallowed the **injected knowledge block** as if it were the user's correction (`K49`, `G16`) |
| Learning / memory | Memory consolidation | `K42` — the explicit consolidate endpoint on a kiro session moved `last_consolidated` 0 → 33, `semantic_memory` 3 → 5, `episodic_memories` 2 → 5. The per-turn cadence has a 30-message threshold on the history log, which is why thirteen short turns never tripped it |
| Learning / memory | Incognito / restricted no-write guarantees | `K33` — an incognito session ran the SAME correction turn that wrote three rows on a persistent session and wrote **zero**; the CLI itself knew its posture. The transcript is still written (that is how the mode is restored) and the session is not restored after a restart |
| Session mechanics | Variants / regenerate | `K34` — two variants persisted; the re-answer still carried the injected knowledge and the profile marker |
| Session mechanics | Edit & resend, fork | `K18` — the fork inherits `workspace_dir` and **loses the ACP binding**; it also copied 8 of the parent's 42 messages, where codex's fork copied all of its parent's |
| Session mechanics | Queued messages, end to end | `K15` — `queue_push` with a `queue_id` during the turn, then `queue_pop` and its own turn afterwards |
| Session mechanics | Warm pool / instant start | `K8` — a second turn on the same session reused the live process and answered immediately. **The only warm reuse demonstrated across the three sweeps** |
| Session mechanics | Per-session model override | `K10` — the bind echoed the model, the activity line named it, and both sessions answered on it. codex's "pin lapses after turn 1" could **not** be re-tested here: kiro does not self-report its model id |
| Session mechanics | Persona / agent selection | `K2`, `K10` — 27 agents offered and the binding round-trips. Only 3 are kiro's built-ins; the other 24 are the operator's private fleet (see the isolation constraint) |
| Session mechanics | Auto-nudge re-arm | `K43` — armed, fired, re-armed, fired, capped at 2 cycles with `active: false`. First runtime demonstration of the loop-side nudge on any ACP provider |
| Session mechanics | Turn telemetry (event and tool counts) | `K4`, `K15` — `Turn complete: 282 events, 1 tool calls` / `196 events, 13 tool calls` |

### Host-compensated

| Axis | Capability | What the host supplies | Boundary |
|---|---|---|---|
| Tools | The `personalclaw-core` tool surface | `K32` — the first tool call of the turn was `@personalclaw-core/get_context`, reached through the protocol `mcpServers` field alone with no seeded user config, and it raised its own approval card | Measured at a later tip than the rest of this column; `K4`/`K6` measured no core surface at all |
| Approvals / safety | Plan mode | kiro has no mode axis, so plan is enforced **only** by the host gate: the CLI called its write tool, the host blocked it, and the reply carried the host's `[SWITCH_TO_AGENT: …]` marker (`K22`) | kiro never enters a native plan mode. "kiro plans" means "the host refuses mutations", nothing more |
| Approvals / safety | Unattended runs | `K41` — a `cron:`-keyed session with the auto-approver off: `auto-denied: unattended run, no one to approve`, `[DONE]` in 5.2 s, nothing left pending, the requested file never created | Fail-fast only: with no permission-mode axis there is no restrictive mode to forward, so an unattended kiro run resolves prompts deterministically rather than being pre-configured to avoid them |
| Approvals / safety | Blocking PreToolUse hooks | `K39` — with the hook ids bound to the session's agent profile, the tool line read `(hook blocked: …)` and the file was never created | **Conditional:** the same hook, unreferenced by any agent, fired three times and the write still landed. Hook firing is agent-scoped by design; the global path is informational and cannot block (`G40`) |
| Prompt-side context | Skills | The `skill_invoke`/`skill_search`/`skill_remember` tools are absent from the CLI **(tool axis)** (`K4`) | The index half was **not** exercised on kiro — no prompt in the sweep matched a skill, so there is no `Surfaced skills:` line and no `skill_surface` row to point at. codex measured that half |
| Tools | kiro's agent discovery of `personalclaw.json` | Config seeding makes the generated file discoverable from a root kiro actually reads (landed after this column) | Not re-driven: `K6`/`G31` measured the file generated correctly and stored where kiro never looks |

### Protocol or CLI constraint

| Axis | Capability | Why it does not work | Watch — what must change, where | Measured against |
|---|---|---|---|---|
| Approvals / safety | The host gate is **provably not universal** | Seven of thirteen tool calls in one turn executed with **no** permission request — kiro's native `todo_list` — and the host itself labelled each of them `risk: "destructive"`, in the same turns where the read, the write and the `rm` each raised a card (`K13`, `K15`, `G27`). The severity is structural, not about one tool: host safety on ACP is opt-in **by the CLI**, so a provider's ungated set is whatever that CLI chooses not to ask about | **CLI** would have to escalate every tool; failing that, **host seam** needs a positive mechanism (deny-by-default for un-permissioned tool calls) plus the per-provider enumeration rendered below | `kiro-cli 2.18.1` |
| Approvals / safety | There is **no config-isolation lever**, and the leak is an identity leak on top of a tool leak | 24 of the 27 personas offered in the picker are the operator's own private agents (`K2`); the CLI's 57 tools **(tool axis)** are all the operator's, including cloud-credential and expense-write tools (`K4`); each session is a five-process tree (`K7`, `G28`) | **Bundle + host seam** (atom `AAP-5`) | `kiro-cli 2.18.1` |
| Tools | Per-tool disable prefs | The only per-tool disable surface addresses *configured* MCP servers; neither kiro's own tools nor the protocol-injected `personalclaw-core` is one — the request returns `server 'personalclaw-core' not found` (`K45`) | **Host seam** — a per-tool pref that can address an ACP CLI's tools does not exist | `kiro-cli 2.18.1` |
| Tools | Read auto-approve (`trust_reads`) does not fire at all | A `pwd` arrived `risk: "safe"`, `is_read_only: "1"` and **still blocked on a card**; a plain file read did too (`K5`, `K12`). The auto-approve is title-driven, and kiro's honest `Running: pwd` / `Reading probe.txt:1` titles do not trip it, where codex's mislabelled "Read file '…'" title for a shell command did — so the honest provider is the one penalized (`G34`) | **Host seam** — classify on the structured `kind`, not the adapter's prose | `kiro-cli 2.18.1` |
| Tools | `AskUserQuestion` card | kiro exposes no `request_user_input`-style tool at all **(tool axis)** (`K26`) | **CLI**, or the core MCP surface supplying one | `kiro-cli 2.18.1` |
| Tools | Subagents | No `subagent_run` **(tool axis)**; kiro's own native `subagent` is not the platform's (`K4`, `K26`). The inject-back precondition itself holds — one pid file per live session at the later tip (`K37`) — but goes stale exactly when it matters: after a mid-turn CLI death the file still names the dead pid (`K38`) | **Host seam** — reachable through the core MCP surface; the pid-file staleness is its own defect (`G42`) | `kiro-cli 2.18.1` |
| Tools | Dry-run replay | Absent by entry-point census, not by interception: the only `dry_run` on any user-reachable surface is session cleanup's unrelated preview flag, and the observe-mode argument exists solely on the native runtime constructor, which an ACP session never builds (`K46`) | **Host seam** — no ACP entry point exists to build | `kiro-cli 2.18.1` |
| Prompt-side context | Task-mode framing goes **stale** on a reused process | The framing block is injected, and its value drifts: it read `## Task mode: Plan` on a session the API reported as `task_mode: agent` (`K23`), while the same session on a fresh process read `## Task mode: Agent` (`K24`). Because kiro reuses one process per session, this is the common case, not the edge one. Related: a session that has ever been in plan mode wedges (`G29`) | **Host seam** | `kiro-cli 2.18.1` |
| Prompt-side context | Workspace confinement on an agent-profile-bound session | Directly bound, the CLI's own `pwd` answers the session's `workspace_dir` — the earlier escape is gone (`K28`). Bind a PersonalClaw **agent profile** and the same `pwd`, asserted inside the spawned CLI, answers `~/.personalclaw/workspace` — the operator's real home (`K50`, `G39`) — because the profile's empty default directory wins over the session's explicit value | **Host seam**. This is the shape a fix can miss: a sweep that drives only the plain binding measures the escape as gone | `kiro-cli 2.18.1` |
| Session mechanics | Concurrent sessions on one process | Declared and absent, which is worse than claude's and codex's honest "no": the config flag was set, the dialect declares support, an in-process check returns true — and three bound sessions still ran on three separate five-process trees (`K7`, `G32`). The fallback is silent at every surface | **Host seam** — a log line on the fallback before anything else | `kiro-cli 2.18.1` |
| Session mechanics | Reasoning effort | `supported_efforts: []` on all 27 agents (`K2`), yet a bind with `reasoning_effort: "low"` is accepted, persisted and echoed back (`K10`) | **CLI** for the axis; **host seam** to stop offering the control (`G21`) | `kiro-cli 2.18.1` |
| Session mechanics | Pipe-death retry / re-queue | `kill -9` on the session's process tree mid-turn ended the stream with `ACP prompt timed out`; nothing was retried or re-queued and no replacement process appeared for that turn. The **next** turn respawned transparently (`K38`, `G42`) | **Host seam** | `kiro-cli 2.18.1` |
| Approvals / safety | Two of the six script-hook kinds never fire on the ACP path | Over 25+ turns: `SessionStart` 1, `UserPromptSubmit` 17, `Stop` 15 — and `PostToolUse` **0**, `Error` **0**. The `Error` miss is not for lack of errors: a `-32601` and a real `-32603` model-unavailable both failed to fire it (`K40`, `G41`) | **Host seam** (`AAP-8`) | `kiro-cli 2.18.1` |
| Approvals / safety | OS sandbox wrap — **`ENV`, not a verdict** | The host logs `No OS-level sandbox available — app-level checks only` at boot on this platform, so there is no host wrap engaged and no confinement boundary to probe (`K47`). Recorded as an environment limit in both directions. kiro brings its own sandbox layer, which is not the host's mechanism | **Platform** | `kiro-cli 2.18.1` |

### Not yet measured (2 of 63 cells)

Neither is a missing fixture, and neither is reachable by driving the product as a user.

1. **Skill-ladder review** — with a live model provider and 25+ turns including corrections, the
   proposals endpoint never left `{"proposals": []}`, and the route census shows accept, promote
   and verify but **no forced-run surface**. From outside the system "the gate was not met" and
   "the review is inert" are the same observation, so no verdict can be recorded either way
   (`K44`, `G44`). It needs instrumentation, not another sweep.
2. **Empty-turn auto-retry** — no empty turn occurred across 25+ turns and ten sessions,
   including a blocked write, a hook-blocked tool, an auto-denied unattended call, a cancelled
   turn and two protocol errors (`K48`). Producing one requires stream injection, so it is out of
   reach for an as-a-user sweep by construction.

## gemini-cli — unverified

A `gemini-cli` runner row ships in the catalog (`runtime_id: acp:gemini-cli`, binary `gemini`,
ACP entered through the CLI's own `--experimental-acp` flag, no adapter) and a `gemini-cli-agent`
bundle exists. **Nothing in this document applies to it.** The binary is not installed on the
measuring host, so **zero of the 63 cells have any observation** — not one row is at parity,
host-compensated, or absent; they are all unmeasured.

Two things are known without driving it, and both are shipped-data honesty notes rather than
capability claims:

- Its `--experimental-acp` flag is **declared from the vendor's documented behavior, not
  measured here**. The catalog's health probe runs `<binary> --version` and nothing else — it
  never opens a session, so a `ready` probe would prove only that the binary answers a version
  query.
- Its catalog row carries **no dialect and no adapter**, so which protocol dialect the host
  would negotiate with it is also unverified.

Treat a gemini-cli session as untested: the constraints in "Constraints that hold on all three
providers" are likely to apply, since most of them are host-side or protocol-level, but that is
an expectation, not a measurement.

## The not-gateable residual, per provider

An ACP CLI decides for itself which of its tools ask the client for permission. Everything it
does not ask about runs before the host ever sees a decision point, so the deny-list, the
task-mode gate and blocking PreToolUse hooks — all of which hang off the permission request —
never run for it. The host cannot enumerate that set by inspection; it can only measure it.

The measured residual is a registry in core (`acp/permission_authority.py`, `NOT_GATEABLE`) so
the gate and this document cannot drift apart. **Every provider is listed even when its residual
set measured empty, so "no entry" can never be read as "gated".** Rendered:

| Provider | Residual set | Measurement behind it |
|---|---|---|
| kiro-cli | `todo_list` — kiro's native task-list tool emits a tool-call frame and a SEL `invoked` row but never a permission request, so no host gate can run for it. `fs_read` — kiro self-approves its OWN file reads: the read raises no permission request even though the write in the same turn does, so a read of a path the host would have questioned is never offered for a decision. Effective risk resolves to safe, so this residue is labelled, never turn-aborting | `AAP-3` sweep (`K13`, `K15`) plus a live re-drive on 2026-08-18: one turn, 6 tool calls, **1 gated, 5 ungated** (4× `todo_list` + 1 file read) |
| claude-code | **measured empty** | `AAP-1` sweep — 44 audited tool events, every one surfaced a permission request |
| codex | **measured empty** | `AAP-2` sweep — no tool observed executing without passing the host gate |

Two honest qualifications on those empty sets:

- **Empty means "no ungated tool was observed in that sweep", not "this CLI escalates everything
  by construction".** claude's total coverage was explicitly contingent on the operator's own CLI
  config auto-approving nothing (`G2`); a different operator config changes the answer without
  changing the CLI.
- **A provider with no registry entry at all is not covered by this statement.** `gemini-cli` has
  none because it has never been driven.

## Where the columns disagree

The asymmetries are the finding, not noise: a fix keyed on one provider's shape is frequently
wrong for another, and a conclusion drawn from two columns was false on the third.

| Capability | claude-code | codex | kiro-cli |
|---|---|---|---|
| Native plan mode | Enters its own plan mode — **only** if plan is set before the first turn (`O19`, `G12`) | No native plan; host gate only (`C7`) — the shape the audit predicted for kiro | No mode axis; host gate only (`K22`) — as predicted |
| Read auto-approve | Fires, and mis-calibrated in both directions (`O7`, `O10`, `O13`) | Fires, title-driven, and not spoofable by a compound command (`C5`, `C11`) | **Never fires** — honest titles are the ones that miss the heuristic (`K5`, `G34`) |
| Hard deny-list, pre-execution | Measured **absent** (`O21`) — before the deny-list learned to read the real command rather than the permission title | **Never driven** | Measured **wired**, with the pattern named to the user (`K25`) |
| Gate coverage | Total, but contingent on the operator's CLI config (`G2`) | Total — no ungated tool observed | **Provably not universal**: 7 of 13 calls in one turn ungated (`G27`). The two-provider conclusion "no ACP tool executed without passing the host gate" is false here |
| Approval-card identity | Real title present; `kind` missing on the permission frame (`O5`, `G10`) | Title missing (`unknown`); `kind` **present** (`C5`, `G18`) | Real titles throughout (`K12`) |
| Concurrent sessions | Declared false, absent (`O11`) | Declared false, absent (`C14`) | **Declared true**, absent (`K7`, `G32`) |
| Persona axis | None — one base agent (`O2`) | None — one base agent (`C2`) | 27 agents, 24 of them the operator's private fleet (`K2`) |
| Reasoning effort | Adapter advertises five; honoring unobservable (`O2`, `O20`) | None advertised; host still accepts a value (`C2`, `C12`) | None advertised; host still accepts a value (`K2`, `K10`) |
| Config isolation | Opt-in flag exists, **off** by default (`O4`, `O19`) | None, by design (`G17`) | None (`G28`) |
| Process model | New adapter process per turn; pool cold (`O17`) | New process per turn; pool cold; 31 descendants (`C14`) | One process reused per session — the only warm reuse measured (`K8`) |
| Fork message copying | All 24 messages (`O25`) | All 14 messages (`C15`) | 8 of 42 messages (`K18`) |
| Can the provider validate a prompt-side cell? | Yes — it quotes its injected context (`O14`) | **No** — it refuses to disclose its context, which is why three cells stayed open (`C18`, `G26`) | Yes — and doing so is how the framing-goes-stale defect was found (`K23`, `G38`) |

## Refreshing this document

Each row above is a measurement with a version attached, so this document rots exactly as fast as
the CLIs move. The refresh procedure is the audit's own checklist, re-run per provider:

1. **Record the versions first** — CLI, adapter (per-home, not `PATH`), and the host commit. A
   row whose version is not recorded is not a measurement.
2. **Drive as a user**, one isolated home per sweep, through the dashboard and the API. *Reading
   code is not a mark.* A cell with no runtime observation stays in the "not yet measured" list.
3. **Re-measure the auth precondition before any capability probe** where a CLI needs one. A
   stale credential masquerades as protocol failure, and such a cell is `ENV`, never a verdict.
4. **Do not overwrite history when a re-drive disagrees.** Record both observations and say which
   is later — kiro's tool axis is in this document precisely because the second drive contradicted
   the first, and hiding that would have been the only real failure.
5. **Regenerate the not-gateable section from the registry**, never by re-deriving it in prose.

The observation ledgers, the full 63-cell matrices and the severity-ranked gap inventory live in
[`../roadmap/plans/ACP-AGENT-PARITY.md`](../roadmap/plans/ACP-AGENT-PARITY.md).
