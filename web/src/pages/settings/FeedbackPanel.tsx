import { useState } from 'react'
import { ThumbsUp, ThumbsDown, BellOff, RotateCcw } from 'lucide-react'
import { api, type FeedbackProducerRow } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { Button } from '../../ui/Button'
import { PanelHeader, Section } from './settingsUI'

/** Per-producer feedback accuracy (FEEDBACK-SIGNAL plan 58) — honest counts only.
 *
 *  Every row is a JUDGMENT SOURCE (a bound prompt, a loop judge kind, a workflow's
 *  surfacing, a routing pair, an app producer) with its rolling ups/downs. Below
 *  min-N the row shows "collecting" and no number (nothing is shown before the
 *  sample means anything). A `suppressed` source stopped surfacing — which only happens for
 *  kinds with a surfacing gate (today `skill_synthesis` alone, see
 *  `feedback.ENFORCED_SUPPRESSION_KINDS`); every other kind below the threshold reads
 *  "retire proposed", because it keeps surfacing and only earns a proposal. Snooze pauses
 *  the check 30 days; Clear un-suppresses after you've edited the source.
 *  Rich "is it learning?" analytics belong to LEARNING-VISIBILITY — this is the
 *  raw table. */
export function FeedbackPanel() {
  const { data, refresh } = useQuery(
    'settings:feedback-producers',
    () => api.feedbackProducers().catch(() => null),
    { persist: false },
  )
  const [busy, setBusy] = useState('')

  const act = async (fn: () => Promise<unknown>, tag: string) => {
    setBusy(tag)
    try { await fn(); invalidateKeys('settings:feedback-producers'); refresh() }
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
    // 🔴 THE SUPPRESSED ROW LOST ITS OWN IDENTITY AT 390px — the row that matters most. Every chip
    //    on the right is `shrink-0`, and a suppressed producer has one more of them (accuracy +
    //    "suppressed" + Snooze + Clear), so the identity block — `min-w-0 flex-1`, i.e. free to
    //    collapse to zero — was squeezed out and its text painted UNDERNEATH the pills. Measured at
    //    390×844: the "prompt 3 6" line occupied x 36–78 while the "33%" pill occupied x 48–88 and
    //    sat on top of it, and `task-inbox-classify` was not readable at all. The healthy sibling row
    //    (one chip fewer) rendered correctly, which is what made it look fine at a glance.
    //    `ux-audit --viewport phone` reported it as a 3.93:1 contrast failure "via: sibling" against
    //    `rgb(86,51,50)` — the danger pill's own tint. That number is not a colour bug: measured
    //    against the real backdrop the ink is 5.93:1 and passes AA. The audit was reporting the
    //    OVERLAP, correctly, in the only vocabulary it has.
    //    Same shape as the projection-rule rows: a `flex-1` box with permission to reach zero beside
    //    siblings that cannot shrink. Fix is the same — let the row wrap, and give the block a floor.
    <div className="flex flex-wrap items-center gap-3 rounded-lg bg-surface-container px-3 py-2.5">
      <div className="min-w-40 flex-1">
        <div className="truncate font-mono text-on-surface text-[0.8125rem]">{row.producer_id}</div>
        <div className="mt-0.5 flex items-center gap-2 text-on-surface-low text-[0.75rem]">
          <span>{row.producer_kind}</span>
          {/* 🔴 A 10px GLYPH WAS THE ONLY THING SAYING WHICH COUNT IS WHICH. Measured on the live
              row: the accessible text was "task-inbox-classify prompt 3 6 33% suppressed" — two bare
              integers, and reading them the wrong way round inverts the meaning of the whole panel
              ("was this source right?" becomes "was it wrong?"). Lucide renders a bare `<svg>` with
              no name, so nothing carried the distinction.
              `role="img"` + a label is this repo's declared form for exactly that case — see
              `ModelsPanel`'s breaker dot ("the dot is the ONLY carrier of the state, no text
              equivalent") and `UsagePanel`'s bar row. The wording follows `ui/FeedbackThumbs`, the
              interactive twin of these same two icons ("Mark accurate" / "Mark wrong"), so the
              summary and the control that produces it speak one vocabulary. */}
          <span role="img" aria-label={`${row.ups} marked accurate`}
            className="inline-flex items-center gap-1"><ThumbsUp size={10} /> {row.ups}</span>
          <span role="img" aria-label={`${row.downs} marked wrong`}
            className="inline-flex items-center gap-1"><ThumbsDown size={10} /> {row.downs}</span>
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
      {/* Below the threshold, but this producer kind has no surfacing gate, so nothing withholds it
          — it keeps being injected and only earns a retire proposal. This row used to render the
          "suppressed" pill above, titled "Stopped surfacing", for exactly these producers: five of
          the six kinds. Saying "retire proposed" is both true and the actionable half. */}
      {row.proposal_only && (
        <span className="shrink-0 rounded-pill px-2 py-0.5 text-[0.75rem]"
          style={{ background: 'color-mix(in srgb, var(--color-warning) 14%, transparent)', color: 'var(--color-warning)' }}
          title="Below the retire threshold. This kind of source has no surfacing gate, so it still runs — you get a retire proposal to act on.">retire proposed</span>
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
