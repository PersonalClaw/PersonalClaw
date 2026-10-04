/** What a pending tool call can touch, as every approval surface shows it (Contract C2).
 *
 *  The blast radius is composed by the BACKEND, once, where the approval is registered
 *  (`approval_brief.call_blast_radius`): from the same reading of the call that gives its risk,
 *  over the call's raw arguments. For a shell call that reading is the command's own — a
 *  redirect into a file "writes files", a program the screen could not vouch for "runs a
 *  command" — which no client could rebuild from a tool's name, and the risk and the facets
 *  cannot disagree because they come from one analysis. Every approval carries it as
 *  `blast_radius`: the chat `approval` frame, `GET /api/approvals`, and the persisted
 *  `perm_meta` a reloaded transcript rehydrates.
 *
 *  This module DECODES that radius and holds the WORDS every dashboard surface renders it in
 *  (the card's chips, the out-of-context toast's line, the phone queue's row). The channel
 *  brief is composed in Python with the same words: `tests/test_approval_brief.py` parses
 *  `FACET_COPY` and `BLAST_RADIUS_FACET_ORDER` out of this file and asserts they agree.
 *
 *  ── Honesty contract ─────────────────────────────────────────────────────────
 *  Every boolean is a POSITIVE claim; `false` means "not established", never "verified
 *  absent". A radius with nothing established is shown as nothing: an all-false object
 *  rendered as four negatives ("no writes, no network, no shell, not read-only") would be a
 *  confident claim from zero evidence. A row that carries no radius (one written before the
 *  backend composed it) decodes as `undefined`, and its surfaces simply show no facets.
 */

import type { ApprovalSegment } from './chatTypes'

/** The approval risk vocabulary: the backend's EFFECTIVE risks (`task_modes.read_call`). The
 *  three declared levels, plus `unchecked` for a shell command the screen could not vouch
 *  for — no tool declares that one, so it is not a `ToolItem.risk_level`. */
export type ApprovalRisk = NonNullable<ApprovalSegment['risk']>

/** Every effective risk this build knows. A total `Record`, so a new member of the union is a
 *  type error here until it is listed. */
const RISK_LEVELS: Record<ApprovalRisk, true> = {
  safe: true,
  caution: true,
  destructive: true,
  unchecked: true,
}

/** The ONE decoder for a risk string off the wire (`PendingApproval.risk`, an `approval`
 *  frame's `risk`): a level this build knows, or `undefined` — `""` (the call declared
 *  nothing) and a level another build wrote are both no evidence. */
export function approvalRiskOf(raw: unknown): ApprovalRisk | undefined {
  return typeof raw === 'string' && Object.prototype.hasOwnProperty.call(RISK_LEVELS, raw)
    ? (raw as ApprovalRisk)
    : undefined
}

/** The risks a surface treats as "may be destructive" — `task_modes.MAY_DESTROY`: the card
 *  withholds its standing grants for them until the user unlocks them. A shell command the
 *  screen could not check can do anything the shell can. */
export function mayDestroy(risk: ApprovalRisk | undefined): boolean {
  return risk === 'destructive' || risk === 'unchecked'
}

/** What a call can touch: independent facets, not a severity scale. `saysReadOnly` is a tool
 *  server's own read-only label, shown as its word; `readOnly` is a read PersonalClaw established. */
export interface BlastRadius {
  writes: boolean
  network: boolean
  shell: boolean
  saysReadOnly: boolean
  readOnly: boolean
}

/** The ONE decoder for a radius off the wire. Anything that is not the four booleans is no
 *  radius at all, never a partial one: a field this build cannot read must not become a
 *  claim in either direction. */
export function blastRadiusOf(raw: unknown): BlastRadius | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const r = raw as Record<string, unknown>
  const keys = BLAST_RADIUS_FACET_ORDER
  if (!keys.every((k) => typeof r[k] === 'boolean')) return undefined
  return {
    writes: r.writes as boolean, network: r.network as boolean, shell: r.shell as boolean,
    saysReadOnly: r.saysReadOnly as boolean, readOnly: r.readOnly as boolean,
  }
}

// ── The ONE facet vocabulary every surface renders ───────────────────────────

/** One established facet, ready to render. */
export interface BlastRadiusFacet {
  key: keyof BlastRadius
  /** Chip text. A noun phrase for a capability, never a verdict. */
  label: string
  /** The same claim spelled out, for a `title`. */
  detail: string
}

/** Total over `BlastRadius` — adding a facet to the interface makes this object a type
 *  error, so a new facet cannot arrive unlabelled. */
const FACET_COPY: Record<keyof BlastRadius, { label: string; detail: string }> = {
  writes: { label: 'Writes files', detail: 'Can create or change files on this machine.' },
  shell: { label: 'Runs a command', detail: 'Can execute a command on this machine.' },
  network: { label: 'Uses the network', detail: 'Can reach the network from this machine.' },
  saysReadOnly: { label: 'Server says it only reads', detail: 'The server that offers this tool labels it read-only. PersonalClaw takes that label only from a server you trust on the Tools page, and only for its tools as they were when you trusted it: one it adds or changes asks until you review it there.' },
  readOnly: { label: 'Reads only', detail: 'Established as a read: no change was established.' },
}

/** Render order — broadest consequence first, the read claims last. Kept as data (not
 *  `Object.keys`) so the order is deliberate and reviewable. */
export const BLAST_RADIUS_FACET_ORDER: readonly (keyof BlastRadius)[] = [
  'writes', 'shell', 'network', 'saysReadOnly', 'readOnly',
]

/** The facets a caller may legitimately SHOW, in render order.
 *
 *  Only established (`true`) facets are returned, and `undefined` yields `[]`. A surface
 *  shows the positives or shows nothing — it must never enumerate all four with on/off
 *  states. */
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
