/** OU-7 — blast-radius DERIVATION for an approval prompt (Contract C2,
 *  `docs/roadmap/plans/ONBOARDING-UX.md`).
 *
 *  This module DESCRIBES what a pending tool call can touch so the human weighing
 *  an approval can see it at a glance. It DECIDES nothing. Nothing gates on its
 *  result, and nothing may: the approval gate, trust-reads and the task-mode gate
 *  all live in `src/personalclaw/task_modes.py` + `src/personalclaw/gateway.py` and
 *  are unchanged by this file. Read-only consumption of classifications the backend
 *  already computed — per C2, "no security-logic change".
 *
 *  CONSUMER: `OU-8` (the ApprovalCard redesign) renders these facets as chips, and
 *  `OU-9` carries the same fields over `ChannelDelivery.request_approval`. Until
 *  OU-8 lands this module has NO call site — that split is the atom boundary, not an
 *  oversight, so nothing here is live yet.
 *
 *  ── Honesty contract ─────────────────────────────────────────────────────────
 *  Every returned boolean is a POSITIVE claim; `false` means "not established",
 *  never "verified absent". Two consequences the callers depend on:
 *
 *  1. `undefined` is returned whenever NO facet could be established. An all-false
 *     object would render as four negatives ("no writes, no network, no shell, not
 *     read-only") — a confident claim from zero evidence. Absence is C2's own
 *     unknown channel (`blastRadius?`), so the renderer simply shows no chips.
 *  2. `readOnly` is only ever claimed on positive evidence, and never alongside an
 *     established write. Under-claiming safety is the correct direction to err.
 *
 *  ── Why `risk` is optional ───────────────────────────────────────────────────
 *  The two surfaces that ask for permission carry DIFFERENT data:
 *    - chat: the `approval` WS event carries `risk` (`chat_runner.py`, broadcast as
 *      the resolved EFFECTIVE risk) → read into `ApprovalSegment['risk']` at
 *      `web/src/pages/ChatPage.tsx:911`.
 *    - the approvals queue behind `#/companion`: `GET /api/approvals` returns
 *      `PendingApproval` (`web/src/lib/api.ts:1661`) = {id, source, tool,
 *      tool_input?, tool_purpose?, session, ts} — there is NO `risk` field.
 *  So `risk` must be absent-able, and its absence must not silently imply a read.
 *  With no risk we fall back to tool-name evidence alone, which can still establish
 *  writes/network/shell positively and can still leave everything unknown
 *  (→ `undefined`).
 *
 *  ── Why `readOnlyCommand` has no caller yet ──────────────────────────────────
 *  C2 names "command-screening classification" as a third input. That classification
 *  exists — `is_read_only_bash()` (`src/personalclaw/task_modes.py:88`) is run per
 *  approval at `src/personalclaw/dashboard/chat_runner.py:2593` and stored as
 *  `perm_meta["is_read_only"]` — but it is NOT on the `approval` WS payload and is
 *  read by nothing, backend or frontend. So the parameter is declared here with the
 *  exact shape that classification produces and NO caller supplies it today; wiring
 *  the pass-through is OU-8/OU-9's scope. It is not re-implemented client-side: this
 *  module never inspects a command string, because deciding whether a command is
 *  read-only IS security logic and it already has an owner.
 */

import type { ApprovalSegment } from './chatTypes'

/** The approval risk vocabulary. Identical to `ToolItem.risk_level`
 *  (`web/src/lib/api.ts:1008`) and to the backend `RiskLevel` values — one
 *  vocabulary, aliased here rather than re-declared so it cannot drift. */
export type ApprovalRisk = NonNullable<ApprovalSegment['risk']>

/** Contract C2's shape, verbatim. Four independent facets, not a severity scale:
 *  a read-only `bash` invocation is both `shell` and `readOnly`. */
export interface BlastRadius {
  writes: boolean
  network: boolean
  shell: boolean
  readOnly: boolean
}

export interface BlastRadiusInput {
  /** Tool name as it arrives on the wire (`approval.tool` / `PendingApproval.tool`). */
  tool: string
  /** The EFFECTIVE per-invocation risk the backend already resolved. ABSENT on the
   *  approvals-queue/companion path — see the module header. */
  risk?: ApprovalRisk
  /** The existing command-screening verdict (`is_read_only_bash`) when a caller has
   *  it. No caller supplies it yet — see the module header. */
  readOnlyCommand?: boolean
}

/** Does a risk level positively establish that the call is a read?
 *
 *  Consumed, not invented: `resolve_effective_risk` (`task_modes.py:245`) reaches
 *  'safe' through exactly three branches — (1) a read-only bash invocation, (2) a
 *  declared-SAFE native tool (all reads: read_file/list_dir/glob/grep/…), (3) a
 *  positive read-only ACP `tool_kind` — so in this codebase EFFECTIVE-safe is
 *  already derived FROM read-only-ness. 'caution' and 'destructive' say a call has
 *  side effects but not WHICH facet, so they establish nothing here.
 *
 *  Typed as a total `Record` on purpose: adding a member to the risk union makes
 *  this object a type error, so a new level cannot arrive silently unmapped. There
 *  is deliberately no `default:` branch anywhere in this module. */
export const RISK_ESTABLISHES_READ_ONLY: Record<ApprovalRisk, boolean> = {
  safe: true,
  caution: false,
  destructive: false,
}

/** Runtime membership test. `ChatPage.tsx:911` casts the raw wire string into the
 *  union WITHOUT validating it, so a session written by another build can carry a
 *  level this build has never heard of. Treat that as no evidence — the same
 *  defence `RiskChip` already makes with its `if (!m) return null`. */
function riskEstablishesReadOnly(risk: ApprovalRisk | undefined): boolean {
  if (risk === undefined) return false
  return Object.prototype.hasOwnProperty.call(RISK_ESTABLISHES_READ_ONLY, risk)
    ? RISK_ESTABLISHES_READ_ONLY[risk]
    : false
}

// ── Tool-name evidence ───────────────────────────────────────────────────────
// Name fragments mirroring the backend's own name vocabulary in
// `src/personalclaw/task_modes.py` so the two agree on what a name means.
// Only POSITIVE matches set a facet; an unmatched name leaves it unknown.

/** Runs a command / spawns a process. `_MUTATING_NAME_HINTS`' exec family, split
 *  out because "can run anything" is its own facet. `terminal`/`shell` cover the
 *  ACP display names ACP agents send as the title. Deliberately NOT `run`: the
 *  `project_run_*` tools drive a workflow run, not a shell. */
const SHELL_HINTS = ['bash', 'shell', 'terminal', 'zsh', 'exec', 'spawn', 'command'] as const

/** Leaves the machine. `web_fetch`/`web_search` are the app-provided web tools;
 *  the rest cover MCP tools named by convention. */
const NETWORK_HINTS = ['web_', 'http', 'fetch', 'browse', 'download', 'upload', 'crawl', 'scrape', 'url'] as const

/** Destructive verbs — `_DESTRUCTIVE_NAME_HINTS` verbatim. Checked FIRST and, like
 *  the backend, they win outright: a delete is a write to the world. */
const DESTRUCTIVE_HINTS = ['delete', 'remove', 'destroy', 'drop_', 'purge', 'forget'] as const

/** Query/inspection verbs — `_READ_VERB_HINTS` verbatim. Checked BEFORE the broad
 *  mutating hints for the same reason the backend does it: `schedule_list` matches
 *  the mutating fragment "schedule" but is plainly a read. */
const READ_VERB_HINTS = ['list', 'get', 'search', 'read', 'status', 'info', 'find', 'inspect', 'show', 'view'] as const

/** Other mutating verbs — `_MUTATING_NAME_HINTS` minus the exec family (now under
 *  SHELL_HINTS), plus `remember`. `remember` is the one deliberate divergence:
 *  `memory_remember` durably persists a lesson, so it writes, but the backend's
 *  `_MUTATING_NAME_HINTS` has no `remember` token. Adding it there is a change to
 *  live risk inference and is NOT this atom's to make (C2: E4 if a gap tempts one),
 *  so the divergence is recorded here and in the plan's execution log instead. The
 *  conservative `readOnly` guard below keeps the two consistent in the only place
 *  it matters: an established write never claims read-only, whatever `risk` says. */
const WRITE_HINTS = [
  'write', 'edit', 'create', 'save', 'update', 'move', 'rename', 'append', 'remember',
  'set_', 'put_', 'install', 'deploy', 'subagent', 'schedule', 'notify', 'post_',
  'send', 'commit', 'push', 'generate',
] as const

/** Normalize a wire tool name for fragment matching. Mirrors
 *  `infer_risk_from_name`'s `mcp/<server>/` strip so the verb match sees the bare
 *  name, and lowercases so ACP display titles ("Terminal", "Read") match too. */
function normalizeToolName(tool: string): string {
  const lowered = (tool || '').toLowerCase().trim()
  return lowered.includes('/') ? lowered.slice(lowered.lastIndexOf('/') + 1) : lowered
}

function hasAny(name: string, hints: readonly string[]): boolean {
  return hints.some((h) => name.includes(h))
}

/** Derive the blast-radius facets of one pending approval, or `undefined` when the
 *  inputs establish nothing.
 *
 *  Purely descriptive and total — no throws, no I/O, no clock, no randomness. Safe
 *  to call on every render. */
export function deriveBlastRadius(input: BlastRadiusInput): BlastRadius | undefined {
  const name = normalizeToolName(input.tool)

  const shell = hasAny(name, SHELL_HINTS)
  const network = hasAny(name, NETWORK_HINTS)

  // Name-verb precedence, mirroring `infer_risk_from_name`: destructive wins
  // outright, then a read verb short-circuits, then the broad mutating hints.
  let writes = false
  let readVerb = false
  if (hasAny(name, DESTRUCTIVE_HINTS)) writes = true
  else if (hasAny(name, READ_VERB_HINTS)) readVerb = true
  else if (hasAny(name, WRITE_HINTS)) writes = true

  // `readOnly` needs positive evidence, and never rides over an established write.
  // The screening verdict is the strongest signal (it inspected the actual command),
  // then EFFECTIVE-safe risk, then a read-verb name. An explicit `false` from the
  // screening verdict positively rules the claim out.
  let readOnly = false
  if (input.readOnlyCommand === true) readOnly = true
  else if (input.readOnlyCommand !== false) readOnly = riskEstablishesReadOnly(input.risk) || readVerb
  if (writes) readOnly = false

  // Nothing established → say nothing. See the honesty contract in the header.
  if (!writes && !network && !shell && !readOnly) return undefined
  return { writes, network, shell, readOnly }
}
