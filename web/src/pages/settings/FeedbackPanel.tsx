import { useState } from 'react'
import { ThumbsUp, ThumbsDown, BellOff, RotateCcw } from 'lucide-react'
import { api, type FeedbackProducerRow } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { Button } from '../../ui/Button'
import { PanelHeader, Section } from './settingsUI'

/** Per-producer feedback accuracy (FEEDBACK-SIGNAL plan 58) — honest counts only.
 *
 *  Every row is a JUDGMENT SOURCE (a bound prompt, a loop judge kind, a workflow's
 *  surfacing, a routing pair, an app producer) with its rolling ups/downs. Below
 *  min-N the row shows "collecting" and no number (nothing is shown before the
 *  sample means anything). A suppressed source stopped surfacing; Snooze pauses
 *  the check 30 days; Clear un-suppresses after you've edited the source.
 *  Rich "is it learning?" analytics belong to LEARNING-VISIBILITY — this is the
 *  raw table. */
export function FeedbackPanel() {
  const { data, refresh } = useCachedData(
    'settings:feedback-producers',
    () => api.feedbackProducers().catch(() => null),
    { persist: false },
  )
  const [busy, setBusy] = useState('')

  const act = async (fn: () => Promise<unknown>, tag: string) => {
    setBusy(tag)
    try { await fn(); invalidateCache('settings:feedback-producers'); refresh() }
    catch { /* the table re-reads; a failed action leaves it unchanged */ }
    finally { setBusy('') }
  }

  const rows = data?.producers ?? []

  return (
    <div>
      <PanelHeader title="AI feedback"
        hint="Every 👍/👎 you leave on an AI judgment (inbox triage, drafts, digests, loop findings) is attributed to the source that produced it — the bound prompt, judge, or rule. A source that keeps being wrong stops surfacing and asks to be reviewed. Everything here is deterministic counting; nothing leaves this machine." />

      <Section title="Judgment sources"
        hint={data ? `Rolling ${data.window_days}-day window · accuracy shown after ${data.min_n} verdicts. History restarts when you rebind a prompt (a new prompt is a new source).` : undefined}>
        {rows.length === 0 ? (
          <div className="rounded-lg bg-surface-container px-3 py-3 text-on-surface-low text-[0.8125rem]">
            No feedback yet — 👍/👎 appear on inbox classifications, drafted replies, digests, and loop findings. Verdicts collect here per judgment source.
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {rows.map((r) => <ProducerRow key={`${r.producer_kind}:${r.producer_id}`} row={r} busy={busy} act={act} />)}
          </div>
        )}
      </Section>
    </div>
  )
}

function ProducerRow({ row, busy, act }: {
  row: FeedbackProducerRow
  busy: string
  act: (fn: () => Promise<unknown>, tag: string) => void
}) {
  const key = `${row.producer_kind}:${row.producer_id}`
  const producer = { producer_kind: row.producer_kind, producer_id: row.producer_id }
  return (
    <div className="flex items-center gap-3 rounded-lg bg-surface-container px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="truncate font-mono text-on-surface text-[0.8125rem]">{row.producer_id}</div>
        <div className="mt-0.5 flex items-center gap-2 text-on-surface-low text-[0.75rem]">
          <span>{row.producer_kind}</span>
          <span className="inline-flex items-center gap-1"><ThumbsUp size={10} /> {row.ups}</span>
          <span className="inline-flex items-center gap-1"><ThumbsDown size={10} /> {row.downs}</span>
        </div>
      </div>
      {row.collecting ? (
        <span className="shrink-0 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low text-[0.75rem]">collecting · {row.n} of few</span>
      ) : (
        <span className="shrink-0 rounded-pill px-2 py-0.5 text-[0.75rem] tabular-nums"
          style={(row.accuracy ?? 0) >= 0.7
            ? { background: 'color-mix(in srgb, var(--color-ok) 14%, transparent)', color: 'var(--color-ok)' }
            : (row.accuracy ?? 0) >= 0.4
              ? { background: 'color-mix(in srgb, var(--color-warning) 14%, transparent)', color: 'var(--color-warning)' }
              : { background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }}>
          {Math.round((row.accuracy ?? 0) * 100)}%
        </span>
      )}
      {row.suppressed && (
        <span className="shrink-0 rounded-pill px-2 py-0.5 text-[0.75rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }}
          title="Stopped surfacing — accuracy fell below the retire threshold.">suppressed</span>
      )}
      {!row.collecting && (
        <div className="flex shrink-0 items-center gap-1">
          <Button size="xs" variant="ghost" disabled={busy === key}
            onClick={() => act(() => api.feedbackSnooze(producer), key)}>
            <BellOff size={12} /> Snooze
          </Button>
          {row.suppressed && (
            <Button size="xs" variant="ghost" disabled={busy === key}
              onClick={() => act(() => api.feedbackClear(producer), key)}>
              <RotateCcw size={12} /> Clear
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
