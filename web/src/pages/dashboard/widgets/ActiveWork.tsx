import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Send, MessageCircleQuestion, Coffee } from 'lucide-react'
import { api, type Loop } from '../../../lib/api'
import { useDashboardLive } from '../DashboardLive'
import { loopStatusMeta } from '../../loops/loopStatusMeta'
import { EmptyState, RowAction, StatusDot } from './kit'
import { spring } from '../../../design/motion'
import type { RouteProps } from '../../../app/useQueryState'

// Loops the user should see as "active work" — anything in flight or awaiting them.
const ACTIVE = new Set(['running', 'paused', 'stagnant', 'blocked', 'needs_input'])

function pendingText(l: Loop): string | null {
  const q = l.pending_question
  if (!q) return null
  return typeof q === 'string' ? q : q.question
}

/** Active Work — each running/active loop as a live row: status dot + progress
 *  ring, current-step/cycle text, inline Nudge, and an Answer box when the loop is
 *  `needs_input`. Long-running autonomous runs are the emphasis (double-height in
 *  the curated layout). Opening a row jumps to its cockpit. */
export function ActiveWork({ navigate }: RouteProps) {
  const { loops } = useDashboardLive()
  const active = loops
    .filter((l) => ACTIVE.has(l.status))
    .sort((a, b) => (b.started_at ?? b.created_at) - (a.started_at ?? a.created_at))

  if (active.length === 0) {
    return <EmptyState icon={Coffee}>No active work. Loops you launch appear here as they run.</EmptyState>
  }

  return (
    <div className="flex flex-col gap-s pt-xs">
      <AnimatePresence initial={false}>
        {active.map((l) => <ActiveRow key={l.id} loop={l} navigate={navigate} />)}
      </AnimatePresence>
    </div>
  )
}

function ActiveRow({ loop, navigate }: { loop: Loop; navigate: RouteProps['navigate'] }) {
  const meta = loopStatusMeta(loop.status)
  const question = pendingText(loop)
  const [answering, setAnswering] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  const pct = loop.max_cycles > 0 ? Math.min(1, loop.total_cycles / loop.max_cycles) : null
  const cycleText = loop.max_cycles > 0
    ? `cycle ${loop.total_cycles}/${loop.max_cycles}`
    : `cycle ${loop.total_cycles} · ongoing`

  const send = async () => {
    const t = text.trim()
    if (!t) return
    setBusy(true)
    try { await api.uLoopNudge(loop.id, t); setText(''); setAnswering(false) }
    catch { /* keep the text so the user can retry */ }
    finally { setBusy(false) }
  }

  return (
    <motion.div
      layout
      transition={spring.spatialDefault}
      className="rounded-lg bg-surface-low p-m"
    >
      <div className="flex items-center gap-s">
        <button type="button" onClick={() => navigate(`loops/${loop.id}`)} className="flex min-w-0 flex-1 items-center gap-s text-left">
          {pct != null ? <ProgressRing pct={pct} tone={meta.tone} /> : <StatusDot color={meta.tone} pulse={loop.status === 'running'} />}
          <div className="min-w-0">
            <p data-type="title-m" className="truncate text-on-surface">{loop.name || loop.task?.slice(0, 60) || 'Loop'}</p>
            <p data-type="body-m" className="truncate text-on-surface-low">
              <span style={{ color: meta.tone }}>{meta.label}</span> · {cycleText}
            </p>
          </div>
        </button>
        {loop.status === 'needs_input' && !answering && (
          <RowAction tone="primary" onClick={() => setAnswering(true)} title="Answer the loop's question"><MessageCircleQuestion size={14} /> Answer</RowAction>
        )}
        {loop.status !== 'needs_input' && !answering && (
          <RowAction tone="default" onClick={() => setAnswering(true)} title="Nudge this loop"><Send size={14} /> Nudge</RowAction>
        )}
      </div>

      {question && !answering && (
        <p data-type="body-m" className="mt-s rounded-md bg-surface px-m py-s text-on-surface-var">
          <MessageCircleQuestion size={13} className="mr-xs inline text-info" />{question}
        </p>
      )}

      <AnimatePresence>
        {answering && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast}
            className="mt-s overflow-hidden"
          >
            {question && <p data-type="body-m" className="mb-xs text-on-surface-var">{question}</p>}
            <div className="flex items-end gap-s">
              <textarea
                autoFocus
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send(); if (e.key === 'Escape') { setAnswering(false); setText('') } }}
                placeholder={loop.status === 'needs_input' ? 'Answer…' : 'Nudge the loop…'}
                aria-label={loop.status === 'needs_input' ? 'Answer the loop' : 'Nudge the loop'}
                rows={2}
                className="min-h-0 flex-1 resize-none rounded-md bg-surface px-m py-s text-on-surface outline-none placeholder:text-on-surface-low"
                data-type="body-m"
              />
              <RowAction tone="primary" onClick={send} title="Send (⌘↵)">{busy ? '…' : <><Send size={14} /> Send</>}</RowAction>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

function ProgressRing({ pct, tone, size = 28 }: { pct: number; tone: string; size?: number }) {
  const r = size / 2 - 2.5, c = 2 * Math.PI * r
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-surface-high)" strokeWidth={2.5} />
      <motion.circle
        cx={size / 2} cy={size / 2} r={r} fill="none" stroke={tone} strokeWidth={2.5} strokeLinecap="round"
        strokeDasharray={c} initial={false} animate={{ strokeDashoffset: c * (1 - pct) }} transition={spring.spatialSlow}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  )
}
