/** OU-7 — blast-radius DERIVATION for an approval prompt (Contract C2,
 *  the ONBOARDING-UX plan (internal, not in this repo)).
 *
 *  This module DESCRIBES what a pending tool call can touch so the human weighing
 *  an approval can see it at a glance. It DECIDES nothing. Nothing gates on its
 *  result, and nothing may: the approval gate, trust-reads and the task-mode gate
 *  all live in `src/personalclaw/task_modes.py` + `src/personalclaw/gateway.py` and
 *  are unchanged by this file. Read-only consumption of classifications the backend
 *  already computed — per C2, "no security-logic change".
 *
 *  CONSUMERS: `pages/chat/ApprovalCard` renders these facets as chips and
 *  `app/approvalToast` renders the same vocabulary as one line (both landed with
 *  OU-8); `OU-9` carries the same fields over `ChannelDelivery.request_approval`.
 *  The facet WORDS live here too, beside the derivation, so the surfaces cannot
 *  drift into three vocabularies for one claim.
 *
 *  ── Honesty contract ─────────────────────────────────────────────────────────
 *  Every returned boolean is a POSITIVE claim; `false` means "not established",
 *  never "verified absent". Two consequences the callers depend on:
 *
 *  1. `undefined` is returned whenever NO facet could be established. An all-false
 *     object would render as four negatives ("no writes, no network, no shell, not
 *     read-only") — a confident claim from zero evidence. Absence is C2's own
 *     unknown channel (`blastRadius?`), so the renderer simply shows no chips.
 *  2. `readOnly` is only ever claimed on positive evidence — a read-only EFFECTIVE risk
 *     or a screened read-only command — and never alongside a write. Under-claiming
 *     safety is the correct direction to err.
 *
 *  ── What the name is, and is not, evidence of ─────────────────────────────────
 *  `readOnly` comes from the backend's classification alone: `risk` is `safe` only when
 *  the tool DECLARES it only reads or the command it runs is a read-only one. A tool's
 *  NAME never establishes a read — `task_list_create` carries "list", and a name is only
 *  ever a guess about what a tool might do. The name hints below DESCRIBE a call that is
 *  not a read (`writes`, `shell`, `network`); they claim nothing about a read.
 *
 *  ── Why `risk` is optional ───────────────────────────────────────────────────
 *  The chat's `approval` WS event carries the EFFECTIVE risk (`chat_runner.py`), and so
 *  does `GET /api/approvals` (`PendingApproval.risk`) for a call whose tool declares one.
 *  A call that declares nothing — an ACP agent's own tool — carries `""`, so `risk` must
 *  be absent-able, and its absence must not imply a read: with no risk the name hints can
 *  still establish writes/network/shell and can still leave everything unknown
 *  (→ `undefined`).
 *
 *  ── Where `readOnlyCommand` comes from (#2821) ───────────────────────────────
 *  C2 names "command-screening classification" as a third input, and for a while it
 *  had no supplier: `is_read_only_bash()` ran per approval and was stored in
 *  `perm_meta["is_read_only"]`, but reached no wire and no reader, so the
 *  `readOnlyCommand === true` branch below was unreachable in production.
 *
 *  It is now supplied by `task_modes.read_only_command()` — ONE backend owner, so the
 *  two surfaces that ask a human for permission cannot answer differently — and
 *  arrives on all three paths: the chat `approval` WS event, `GET /api/approvals`, and
 *  the persisted `perm_meta` a reloaded transcript rehydrates.
 *
 *  Two wire spellings exist and `readOnlyCommandOf` is the ONE decoder for both. The
 *  live paths carry a real JSON boolean (or `null`). The history path carries the
 *  legacy `"1"`/`""` strings, because that value is already written into every session
 *  transcript's `cls` column and old transcripts must keep rehydrating. Note `""` is
 *  falsy but not `=== false`, so passing it through raw would land in the "unknown"
 *  branch and silently lose the negative verdict — which is exactly the bug shape this
 *  decoder exists to prevent.
 *
 *  It is still not re-implemented client-side: this module never inspects a command
 *  string, because deciding whether a command is read-only IS security logic and it
 *  already has an owner.
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
  /** The backend's command-screening verdict (`task_modes.read_only_command`), when
   *  this call is a shell call. Absent when it is not one — see the module header.
   *  Decode a raw wire value with `readOnlyCommandOf`, never by casting. */
  readOnlyCommand?: boolean
}

/** The ONE decoder for the wire's command-screening verdict. Tri-state in, tri-state out.
 *
 *  `true`/`"1"` → screened, read-only · `false`/`""` → screened, NOT read-only ·
 *  anything else (`null`, `undefined`, an unknown string) → not screened.
 *
 *  Every parse site funnels through here so the two wire spellings cannot produce two
 *  different answers. The `""` case is the one worth naming: it is falsy but not
 *  `=== false`, so a raw pass-through would read as "unknown" and quietly drop a
 *  negative verdict, turning a mutating command back into an unscreened one.
 *
 *  Unknown values collapse to `undefined` rather than `false`: absence must never
 *  become a positive claim in either direction, which is the honesty contract the whole
 *  module is built on. */
export function readOnlyCommandOf(raw: unknown): boolean | undefined {
  if (raw === true || raw === '1') return true
  if (raw === false || raw === '') return false
  return undefined
}

/** Does a risk level positively establish that the call is a read?
 *
 *  Consumed, not invented: `resolve_effective_risk` (`task_modes.py`) reaches 'safe'
 *  only through a read-only shell command or a tool that DECLARES it only reads, so
 *  EFFECTIVE-safe is already derived FROM read-only-ness. 'caution' and 'destructive'
 *  say a call has side effects but not WHICH facet, so they establish nothing here.
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

/** The ONE decoder for a risk string off the wire (`PendingApproval.risk`, an `approval`
 *  frame's `risk`): a level this build knows, or `undefined` — `""` (the call declared
 *  nothing) and a level another build wrote are both no evidence. */
export function approvalRiskOf(raw: unknown): ApprovalRisk | undefined {
  return typeof raw === 'string' && Object.prototype.hasOwnProperty.call(RISK_ESTABLISHES_READ_ONLY, raw)
    ? (raw as ApprovalRisk)
    : undefined
}

// ── Tool-name description ────────────────────────────────────────────────────
// What kind of change a call that is not a read can make, from words in its name —
// the lists `src/personalclaw/approval_brief.py` carries, verbatim (a test pins them).
// Only POSITIVE matches set a facet; an unmatched name leaves it unknown.

/** Runs a command / spawns a process. `terminal`/`shell` cover the ACP display names
 *  ACP agents send as the title. Deliberately NOT `run`: the `project_run_*` tools
 *  drive a workflow run, not a shell. */
const SHELL_HINTS = ['bash', 'shell', 'terminal', 'zsh', 'exec', 'spawn', 'command'] as const

/** Leaves the machine. `web_fetch`/`web_search` are the app-provided web tools;
 *  the rest cover MCP tools named by convention. */
const NETWORK_HINTS = ['web_', 'http', 'fetch', 'browse', 'download', 'upload', 'crawl', 'scrape', 'url'] as const

/** Removes something. A delete is a write to the world, so these describe `writes` too. */
const DESTRUCTIVE_HINTS = ['delete', 'remove', 'destroy', 'drop_', 'purge', 'forget'] as const

/** Creates or changes something. */
const WRITE_HINTS = [
  'write', 'edit', 'create', 'save', 'update', 'move', 'rename', 'append', 'remember',
  'set_', 'put_', 'install', 'deploy', 'subagent', 'schedule', 'notify', 'post_',
  'send', 'commit', 'push', 'generate',
] as const

/** Normalize a wire tool name for fragment matching: an `mcp/<server>/<tool>` name is
 *  described by its tool, and lowercasing lets ACP display titles ("Terminal") match. */
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

  // What kind of change the call can make, from words in its name — a description, never a
  // read: no word establishes that a call changes nothing.
  const writes = hasAny(name, DESTRUCTIVE_HINTS) || hasAny(name, WRITE_HINTS)
  // `readOnly` needs positive evidence: the screening verdict (it inspected the actual
  // command) or an EFFECTIVE-safe risk (the tool declares it only reads). An explicit
  // `false` from the screening verdict rules the claim out, and so does an established
  // write — a tool labelled read-only whose name says it writes is shown as the write it
  // may be.
  const readOnly = !writes && (input.readOnlyCommand === true
    || (input.readOnlyCommand !== false && riskEstablishesReadOnly(input.risk)))

  // Nothing established → say nothing. See the honesty contract in the header.
  if (!writes && !network && !shell && !readOnly) return undefined
  return { writes, network, shell, readOnly }
}

// ── OU-8: the ONE facet vocabulary every surface renders ─────────────────────
// Three surfaces show a blast radius — the chat card's chips, the out-of-context
// approval toast's one-liner, and (OU-9) the channel brief. They must not each
// invent their own words for `writes`, so the words live here, beside the
// derivation, and each surface only chooses a PRESENTATION.

/** One established facet, ready to render. */
export interface BlastRadiusFacet {
  key: keyof BlastRadius
  /** Chip text. A noun phrase for a capability, never a verdict. */
  label: string
  /** The same claim spelled out, for a `title`. */
  detail: string
}

/** Total over `BlastRadius` — adding a facet to the interface makes this object a
 *  type error, so a new facet cannot arrive unlabelled (the same discipline
 *  `RISK_ESTABLISHES_READ_ONLY` applies to the risk union). */
const FACET_COPY: Record<keyof BlastRadius, { label: string; detail: string }> = {
  writes: { label: 'Writes files', detail: 'Can create or change files on this machine.' },
  shell: { label: 'Runs a command', detail: 'Can execute a command on this machine.' },
  network: { label: 'Uses the network', detail: 'Can reach the network from this machine.' },
  readOnly: { label: 'Reads only', detail: 'Established as a read: no change was established.' },
}

/** Render order — broadest consequence first, the read claim last. Kept as data (not
 *  `Object.keys`) so the order is deliberate and reviewable; a test asserts it covers
 *  every key of `FACET_COPY`, so a fifth facet cannot be silently dropped from every
 *  surface at once. */
export const BLAST_RADIUS_FACET_ORDER: readonly (keyof BlastRadius)[] = [
  'writes', 'shell', 'network', 'readOnly',
]

/** The facets a caller may legitimately SHOW, in render order.
 *
 *  Only established (`true`) facets are returned, and `undefined` yields `[]`. That is
 *  the honesty contract made renderable: a `false` facet means "not established", so
 *  painting it as a negative chip ("no network") would turn absence of evidence into a
 *  confident all-clear. A surface therefore shows the positives or shows nothing — it
 *  must never enumerate all four with on/off states. */
export function establishedFacets(radius: BlastRadius | undefined): BlastRadiusFacet[] {
  if (!radius) return []
  return BLAST_RADIUS_FACET_ORDER.filter((k) => radius[k]).map((k) => ({ key: k, ...FACET_COPY[k] }))
}

/** The compact one-line form, for a surface with no room for chips (the
 *  out-of-context approval toast). Empty string when nothing is established — the
 *  caller then says nothing about the blast radius rather than "nothing established",
 *  which a reader would hear as "nothing happens". */
export function blastRadiusLine(radius: BlastRadius | undefined): string {
  const facets = establishedFacets(radius)
  if (facets.length === 0) return ''
  return facets.map((f) => f.label.toLowerCase()).join(', ')
}
