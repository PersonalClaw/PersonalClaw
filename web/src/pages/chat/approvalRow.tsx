import type { ReactNode } from 'react'
import type { PendingApproval } from '../../lib/api'
import { blastRadiusOf, confirmsOf, establishedFacets } from './approvalMeta'

// ── A waiting-list approval, as ApprovalPrompt shows it ────────────────────────────────────────
//
// The fields every surface that shows a row of `GET /api/approvals` hands `ui/ApprovalPrompt`: the
// phone companion's queue and the buddy's expanded card (#3893). One mapping, so the brief the
// owner consents to (what will run, where, its source, its reach, what declining does) reads the
// same wherever the row is answered. Each surface brings its own choices and density.

/** The prompt's brief for `ap`: the tool, its arguments, why, where it came from, the context
 *  block and the second confirmation the row names. *stale* when it can no longer be answered
 *  (past the waiting list's `expires_at`): the block says so in place of how long it waited. */
export function approvalRowFields(ap: PendingApproval, { stale = false }: { stale?: boolean } = {}): {
  tool: string
  args?: string
  purpose?: string
  source?: string
  meta: ReactNode
  confirms: string[]
} {
  return {
    tool: ap.tool,
    args: argsText(ap.tool_input),
    purpose: ap.tool_purpose,
    source: ap.source_label || undefined,
    meta: <ApprovalMeta ap={ap} stale={stale} />,
    // The second confirmation the row names, as every other surface asks it.
    confirms: confirmsOf(ap.confirms, ap.risk),
  }
}

/** How long the request has been waiting, and what the call can touch — the context that turns a
 *  tool name into a decision. Where it came from is the card's own "From" line, in the registry's
 *  words (`source_label`): this block used to print the raw session key (`loop-0a1b2c3d-t-…`) and
 *  the relay's source word (`subagent`).
 *
 *  The blast-radius facets are the radius the backend composed for this call
 *  (`blast_radius`), in the same words the chat card uses. The two surfaces that ask a human
 *  for permission share `ui/ApprovalPrompt` precisely so they cannot drift, and a facet shown
 *  on one and missing on the other was that drift. Inventing it client-side would mean a
 *  second classifier.
 *
 *  Facets render as their own row rather than as chips: this list is `roomy` density on a
 *  phone, where a dl row reads better than a wrapped chip strip. The words are shared; the
 *  presentation is per-surface, exactly as the toast already does it. */
function ApprovalMeta({ ap, stale = false }: { ap: PendingApproval; stale?: boolean }) {
  const rows: [string, string][] = []
  const waited = waitedFor(ap.ts)
  if (stale) rows.push(['Expired', 'It can no longer be answered.'])
  else if (waited) rows.push(['Waiting', waited])
  // `establishedFacets` returns ONLY what is positively established, so an empty list
  // means "nothing could be established" and must not render as a reassurance.
  const facets = establishedFacets(blastRadiusOf(ap.blast_radius))
  if (facets.length) rows.push(['Can touch', facets.map((f) => f.label).join(' · ')])
  if (!rows.length && !ap.deny_effect && !ap.asked_for) return null
  return (
    <>
      {rows.length > 0 && (
        <dl className="mt-s flex flex-col gap-xs">
          {rows.map(([k, v]) => (
            <div key={k} data-type="caption" className="flex gap-s">
              <dt className="shrink-0 text-on-surface-low">{k}</dt>
              <dd className="min-w-0 break-all text-on-surface-var">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {/* Who asked for the turn when it was not you (`approval_grants.asked_for_line`). */}
      {ap.asked_for && <p data-type="body-s" className="mt-s text-on-surface-var">{ap.asked_for}</p>}
      {/* What a Deny does when it does more than decline the call (`turn_endings.deny_effect`). */}
      {ap.deny_effect && <p data-type="body-s" className="mt-s text-on-surface-low">{ap.deny_effect}</p>}
    </>
  )
}

/** `tool_input` is typed `unknown` on the wire — the backend forwards whatever the provider
 *  sent (a pre-rendered string for most tools, a JSON object for some). Render a string
 *  as-is and pretty-print anything else, so the arguments are never shown as "[object
 *  Object]" on the surface whose whole job is showing what is being approved. */
function argsText(input: unknown): string | undefined {
  if (input == null || input === '') return undefined
  if (typeof input === 'string') return input
  try { return JSON.stringify(input, null, 2) } catch { return String(input) }
}

/** Coarse elapsed time from the approval's epoch-seconds `ts`. Coarse on purpose: the useful
 *  fact is "this has been blocked a while", and approvals time out in minutes. */
export function waitedFor(ts: number | undefined): string {
  if (!ts) return ''
  const secs = Math.max(0, Math.round(Date.now() / 1000 - ts))
  if (secs < 60) return `${secs}s`
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m`
  return `${Math.round(mins / 60)}h`
}
