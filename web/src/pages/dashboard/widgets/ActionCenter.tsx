import { useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { Check, X, ShieldCheck, Inbox, Sparkles, CheckCheck, Send } from 'lucide-react'
import { api } from '../../../lib/api'
import { useDashboardLive } from '../DashboardLive'
import { EmptyState, WidgetRow, RowAction } from './kit'
import type { RouteProps } from '../../../app/useQueryState'

type Kind = 'approval' | 'inbox' | 'proposal'
interface Entry { key: string; kind: Kind; title: string; sub: string; id: string; session?: string }

/** Action Center — the unified triage queue merging pending tool approvals, inbox
 *  items awaiting a reply, and skill proposals. Approvals + proposals resolve
 *  inline (approve/reject · accept/reject); an inbox reply opens the item where
 *  its draft editor lives (a blind dashboard send would bypass draft review).
 *  Acted rows optimistically leave the list; the live feed reconciles. Calm "all
 *  clear" state when the queue is empty. */
export function ActionCenter({ navigate }: RouteProps) {
  const { approvals, inbox, proposals, refreshAll } = useDashboardLive()
  const [busy, setBusy] = useState<Set<string>>(new Set())
  // Optimistically hidden rows (acted on) until the feed catches up.
  const [done, setDone] = useState<Set<string>>(new Set())

  const withBusy = async (key: string, fn: () => Promise<unknown>) => {
    setBusy((s) => new Set(s).add(key))
    try { await fn(); setDone((s) => new Set(s).add(key)) } catch { /* leave in place on failure */ }
    finally { setBusy((s) => { const n = new Set(s); n.delete(key); return n }) }
    refreshAll()
  }

  // Order by urgency: approvals (a run is blocked on you) first, then inbox
  // replies, then skill proposals (least time-critical).
  const allEntries: Entry[] = [
    ...approvals.map((a) => ({ key: `a:${a.id}`, kind: 'approval' as const, id: a.id, title: `Run ${a.tool}`, sub: a.tool_purpose || a.source || 'Tool approval', session: a.session })),
    ...inbox.map((i) => ({ key: `i:${i.id}`, kind: 'inbox' as const, id: i.id, title: i.sender_name || i.channel_name || 'Message', sub: i.message?.slice(0, 90) || '' })),
    ...proposals.map((p) => ({ key: `p:${p.id}`, kind: 'proposal' as const, id: p.id, title: `Skill: ${p.slug}`, sub: p.description?.slice(0, 90) || '' })),
  ].filter((e) => !done.has(e.key))

  if (allEntries.length === 0) {
    return <EmptyState icon={CheckCheck}>All clear — nothing waiting on you.</EmptyState>
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
    if (e.kind === 'approval') withBusy(e.key, () => api.resolveApproval(e.id, 'approve'))
    else if (e.kind === 'proposal') withBusy(e.key, () => api.acceptSkillProposal(e.id))
    else navigate('inbox')  // reply in the detail where the draft editor lives
  }
  const secondary = (e: Entry) => {
    if (e.kind === 'approval') withBusy(e.key, () => api.resolveApproval(e.id, 'reject'))
    else if (e.kind === 'proposal') withBusy(e.key, () => api.rejectSkillProposal(e.id))
    else withBusy(e.key, () => api.updateInboxItem(e.id, { status: 'dismissed' }))
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      <AnimatePresence initial={false}>
        {entries.map((e) => {
          const Icon = icon[e.kind]
          const isBusy = busy.has(e.key)
          return (
            <WidgetRow
              key={e.key}
              onClick={() => navigate(routeFor(e))}
              actions={
                isBusy ? <span data-type="label-m" className="px-m text-on-surface-low">…</span> : (
                  e.kind === 'inbox' ? (
                    <>
                      <RowAction tone="primary" onClick={() => primary(e)} title="Open to reply"><Send size={14} /> Reply</RowAction>
                      <RowAction tone="danger" onClick={() => secondary(e)} title="Dismiss"><X size={14} /></RowAction>
                    </>
                  ) : (
                    <>
                      <RowAction tone="ok" onClick={() => primary(e)} title={e.kind === 'approval' ? 'Approve' : 'Accept'}><Check size={14} /> {e.kind === 'approval' ? 'Approve' : 'Accept'}</RowAction>
                      <RowAction tone="danger" onClick={() => secondary(e)} title="Reject"><X size={14} /></RowAction>
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
