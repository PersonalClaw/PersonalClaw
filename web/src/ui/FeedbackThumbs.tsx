import { useEffect, useRef, useState } from 'react'
import { ThumbsUp, ThumbsDown } from 'lucide-react'
import { api, type FeedbackTargetKind, type FeedbackProducer } from '../lib/api'
import { cx } from './cx'

/** Quiet 👍/👎 pair for AI JUDGMENT outputs (FEEDBACK-SIGNAL plan 58) — inbox
 *  classifications/drafts/digests, loop findings. Never chat messages.
 *
 *  👍 is silent-positive (tooltip "Mark accurate") — recorded only so accuracy has
 *  a denominator. 👎 ("Mark wrong — tell me why") opens an optional one-line "why"
 *  popover; Enter or click-away records without a reason. State is reflected (a
 *  filled thumb), reversible (re-thumb supersedes), and hydrated from the store on
 *  mount so a reopened card shows the existing verdict. Renders nothing while the
 *  feedback config kill-switch is off (the backend 404s; we hide on the first 404). */
export function FeedbackThumbs({ targetKind, targetId, producer, snapshot, className }: {
  targetKind: FeedbackTargetKind
  targetId: string
  /** The producing artifact this verdict attributes to (from the card's payload). */
  producer?: FeedbackProducer
  /** The judgment AS SHOWN — so accuracy survives later edits. */
  snapshot?: Record<string, unknown>
  className?: string
}) {
  const [verdict, setVerdict] = useState<'up' | 'down' | null>(null)
  const [disabled, setDisabled] = useState(false)
  const [whyOpen, setWhyOpen] = useState(false)
  const [why, setWhy] = useState('')
  const whyRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let alive = true
    setVerdict(null); setWhyOpen(false); setWhy('')
    api.feedbackTarget(targetKind, targetId)
      .then((r) => { if (alive) setVerdict(r.verdict) })
      .catch(() => { if (alive) setDisabled(true) })  // kill-switch → hide entirely
    return () => { alive = false }
  }, [targetKind, targetId])

  useEffect(() => { if (whyOpen) whyRef.current?.focus() }, [whyOpen])

  if (disabled) return null

  const record = async (v: 'up' | 'down', reason = '') => {
    setVerdict(v)  // optimistic; the store supersedes on re-thumb
    setWhyOpen(false); setWhy('')
    try {
      await api.recordFeedback({
        target_kind: targetKind, target_id: targetId, verdict: v,
        reason: reason || undefined, snapshot,
        producer_kind: producer?.producer_kind, producer_id: producer?.producer_id,
      })
    } catch { /* never break the host surface — the thumb stays optimistic */ }
  }

  const btn = 'inline-flex size-6 items-center justify-center rounded-md transition-colors hover:bg-surface-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50'

  return (
    <span className={cx('relative inline-flex items-center gap-0.5', className)}>
      <button type="button" title="Mark accurate" aria-label="Mark accurate" aria-pressed={verdict === 'up'}
        onClick={() => record('up')}
        className={cx(btn, verdict === 'up' ? 'text-ok' : 'text-on-surface-low hover:text-on-surface')}>
        <ThumbsUp size={13} className={verdict === 'up' ? 'fill-current' : undefined} />
      </button>
      <button type="button" title="Mark wrong (tell me why)" aria-label="Mark wrong — optionally tell me why"
        aria-pressed={verdict === 'down'}
        onClick={() => (verdict === 'down' ? record('down') : setWhyOpen((o) => !o))}
        className={cx(btn, verdict === 'down' ? 'text-danger' : 'text-on-surface-low hover:text-on-surface')}>
        <ThumbsDown size={13} className={verdict === 'down' ? 'fill-current' : undefined} />
      </button>
      {whyOpen && (
        <>
          {/* click-away records WITHOUT a reason (skippable by design) */}
          <div className="fixed inset-0 z-40" aria-hidden onClick={() => record('down', why)} />
          <div className="absolute right-0 top-7 z-50 w-64 rounded-lg border border-outline-variant/40 bg-surface-container p-2 shadow-lg">
            <input ref={whyRef} value={why} maxLength={500} placeholder="Why was this wrong? (optional)"
              aria-label="Why was this wrong (optional)"
              onChange={(e) => setWhy(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') record('down', why)
                if (e.key === 'Escape') { setWhyOpen(false); setWhy('') }
              }}
              className="h-8 w-full rounded-md bg-surface px-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
            <div className="mt-1 text-on-surface-low text-[0.6875rem]">Enter to send · Esc to cancel · click away to skip</div>
          </div>
        </>
      )}
    </span>
  )
}
