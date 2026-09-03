import { useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { Check, X, ShieldCheck, Inbox, Sparkles, CheckCheck, Send } from 'lucide-react'
import { api } from '../../../lib/api'
import { reportingWrite } from '../../../app/reportingWrite'
import { rowSubject } from '../../../lib/rowSubject'
import { useDashboardLive } from '../DashboardLive'
import { SlotEmptyState, WidgetRow, RowAction } from './kit'
import { InlineError } from '../../../ui/InlineError'
import type { RouteProps } from '../../../app/useQueryState'
import { invalidateKeys } from '../../../lib/data'

type Kind = 'approval' | 'inbox' | 'proposal'
interface Entry { key: string; kind: Kind; title: string; sub: string; id: string; session?: string }

/** Action Center — the unified triage queue merging pending tool approvals, inbox
 *  items awaiting a reply, and skill proposals. Approvals + proposals resolve
 *  inline (approve/reject · accept/reject); an inbox reply opens the item where
 *  its draft editor lives (a blind dashboard send would bypass draft review).
 *  Acted rows optimistically leave the list; the live feed reconciles. Calm "all
 *  clear" state when the queue is empty. */
export function ActionCenter({ navigate }: RouteProps) {
  /** A proposal decided from the DASHBOARD still changes the Skills page's badge and list, which
   *  read the same collection under `skill-proposals-count` and `skill-proposals`. Prefix mode
   *  keeps every key on that collection in step. */
  const bustProposals = () => invalidateKeys('skill-proposals', true)
  const {
    approvals, inbox, proposals, refreshAll,
    approvalsErr, inboxErr, proposalsErr, retryApprovals, retryInbox, retryProposals,
  } = useDashboardLive()
  const [busy, setBusy] = useState<Set<string>>(new Set())
  // Optimistically hidden rows (acted on) until the feed catches up.
  const [done, setDone] = useState<Set<string>>(new Set())

  const withBusy = async (key: string, what: string, fn: () => Promise<unknown>) => {
    setBusy((s) => new Set(s).add(key))
    // A failed action leaves the row in place AND says why (the server's own sentence).
    // The empty catch here made a 409 Accept look like a dead button: the row stayed,
    // nothing moved, no message — while the Skills page surfaced the same failure fine.
    const ok = await reportingWrite(what, fn)
    if (ok) setDone((s) => new Set(s).add(key))
    setBusy((s) => { const n = new Set(s); n.delete(key); return n })
    refreshAll()
  }

  // Order by urgency: approvals (a run is blocked on you) first, then inbox
  // replies, then skill proposals (least time-critical).
  const allEntries: Entry[] = [
    ...approvals.map((a) => ({ key: `a:${a.id}`, kind: 'approval' as const, id: a.id, title: `Run ${a.tool}`, sub: a.tool_purpose || a.source || 'Tool approval', session: a.session })),
    ...inbox.map((i) => ({ key: `i:${i.id}`, kind: 'inbox' as const, id: i.id, title: i.sender_name || i.channel_name || 'Message', sub: i.message?.slice(0, 90) || '' })),
    ...proposals.map((p) => ({ key: `p:${p.id}`, kind: 'proposal' as const, id: p.id, title: `Skill: ${p.slug}`, sub: p.description?.slice(0, 90) || '' })),
  ].filter((e) => !done.has(e.key))

  // A lane whose READ failed keeps its last-good rows, so a partial failure would otherwise vanish
  // into the queue — or into "All clear" when every lane is empty. Surface each failed lane with a
  // Retry so a swallowed load can't hide a pending item (a tool approval is safety-relevant), and
  // so a failed lane is never mistaken for an empty one. An empty lane stays silent.
  const failures: { key: string; what: string; retry: () => void }[] = [
    ...(approvalsErr ? [{ key: 'approvals', what: 'pending approvals', retry: retryApprovals }] : []),
    ...(inboxErr ? [{ key: 'inbox', what: 'inbox items', retry: retryInbox }] : []),
    ...(proposalsErr ? [{ key: 'proposals', what: 'skill proposals', retry: retryProposals }] : []),
  ]

  if (allEntries.length === 0 && failures.length === 0) {
    return <SlotEmptyState icon={CheckCheck}>All clear — nothing waiting on you.</SlotEmptyState>
  }

  // Cap the visible queue so one flooded source (e.g. many skill proposals) can't
  // bury the rest or blow out the card. Overflow routes to the fullest source.
  const CAP = 8
  const entries = allEntries.slice(0, CAP)
  const overflow = allEntries.length - entries.length

  const icon = { approval: ShieldCheck, inbox: Inbox, proposal: Sparkles }
  const tone = { approval: 'var(--color-warn)', inbox: 'var(--color-secondary)', proposal: 'var(--color-primary)' }
  const routeFor = (e: Entry) => {
    if (e.kind === 'approval' && e.session) return `chat/${encodeURIComponent(e.session)}`
    if (e.kind === 'approval') return 'chat'
    if (e.kind === 'inbox') return 'inbox'
    return 'skills?mode=proposals'
  }

  const primary = (e: Entry) => {
    if (e.kind === 'approval') withBusy(e.key, `approve “${rowSubject([e.title, e.sub])}”`, () => api.resolveApproval(e.id, 'approve'))
    else if (e.kind === 'proposal') withBusy(e.key, `accept “${rowSubject([e.title, e.sub])}”`, () => api.acceptSkillProposal(e.id).then(bustProposals))
    else navigate('inbox')  // reply in the detail where the draft editor lives
  }
  const secondary = (e: Entry) => {
    if (e.kind === 'approval') withBusy(e.key, `reject “${rowSubject([e.title, e.sub])}”`, () => api.resolveApproval(e.id, 'reject'))
    else if (e.kind === 'proposal') withBusy(e.key, `reject “${rowSubject([e.title, e.sub])}”`, () => api.rejectSkillProposal(e.id).then(bustProposals))
    else withBusy(e.key, `dismiss “${rowSubject([e.title, e.sub])}”`, () => api.updateInboxItem(e.id, { status: 'dismissed' }))
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      {failures.map((f) => (
        <InlineError key={f.key} icon onRetry={f.retry}>
          Couldn&rsquo;t load {f.what}.
        </InlineError>
      ))}
      <AnimatePresence initial={false}>
        {entries.map((e) => {
          const Icon = icon[e.kind]
          const isBusy = busy.has(e.key)
          // 🪤 `e.title` ALONE IS NOT THE ROW. For an inbox entry it is the sender/channel, so eight
          // proposals from the same channel all composed "Reply: skills" — the name changed and the
          // ambiguity did not. Measured by re-running the census against the fix, which is the only
          // reason it was caught. The row's identity is title + the summary line beneath it.
          // Bounded, not just composed: the first version of this shipped uncapped and put SIXTEEN
          // 107-character names on this widget — measured in the AX tree, and the same defect in the
          // other direction as the artifact tiles named by their whole body. `lib/rowSubject` owns
          // the rule and the number for every row control in the app.
          const subject = rowSubject([e.title, e.sub])
          return (
            <WidgetRow
              key={e.key}
              onClick={() => navigate(routeFor(e))}
              label={subject}
              actions={
                isBusy ? <span data-type="label-m" className="px-m text-on-surface-low">…</span> : (
                  e.kind === 'inbox' ? (
                    <>
                      <RowAction tone="primary" onClick={() => primary(e)} title="Open to reply"
                        ariaLabel={`Reply: ${subject}`}><Send size={14} /> Reply</RowAction>
                      <RowAction tone="danger" onClick={() => secondary(e)} title="Dismiss"
                        ariaLabel={`Dismiss: ${subject}`}><X size={14} /></RowAction>
                    </>
                  ) : (
                    <>
                      <RowAction tone="ok" onClick={() => primary(e)} title={e.kind === 'approval' ? 'Approve' : 'Accept'}
                        ariaLabel={`${e.kind === 'approval' ? 'Approve' : 'Accept'}: ${subject}`}><Check size={14} /> {e.kind === 'approval' ? 'Approve' : 'Accept'}</RowAction>
                      <RowAction tone="danger" onClick={() => secondary(e)} title="Reject"
                        ariaLabel={`Reject: ${subject}`}><X size={14} /></RowAction>
                    </>
                  )
                )
              }
            >
              <div className="flex items-center gap-s">
                <Icon size={15} style={{ color: tone[e.kind] }} className="shrink-0" />
                <div className="min-w-0">
                  <p data-type="title-m" className="truncate text-on-surface">{e.title}</p>
                  {e.sub && <p data-type="body-m" className="truncate text-on-surface-low">{e.sub}</p>}
                </div>
              </div>
            </WidgetRow>
          )
        })}
      </AnimatePresence>
      {overflow > 0 && (
        <button
          type="button"
          onClick={() => navigate(proposals.length >= inbox.length ? 'skills?mode=proposals' : 'inbox')}
          className="mt-xs self-start rounded-pill px-m py-xs text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface"
          data-type="label-m"
        >
          +{overflow} more to triage →
        </button>
      )}
    </div>
  )
}
