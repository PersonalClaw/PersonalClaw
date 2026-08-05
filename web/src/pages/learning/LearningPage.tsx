import { useMemo, useState } from 'react'
import { AlertTriangle, Brain, Check, RefreshCw, X } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Button } from '../../ui/Button'
import { QuietButton } from '../../ui/QuietButton'
import { Segmented } from '../../ui/forms'
import { InlineError } from '../../ui/InlineError'
import { EmptyState, ListSkeleton } from '../../ui/ListScaffold'
import { useCachedData } from '../../lib/useCachedData'
import { api, type LearningInbox, type LearningRow, type StagingWeek } from '../../lib/api'
import { fvs } from '../../design/fontWeight'
import {
  DAY_HINT, DAY_TONE, bulkBlockedReason, dayLabel, dayState,
  kindIcon, kindLabel, tierLabel, tierTone,
} from './learningMeta'
import { WEEK_KEY, proposalsKey, refreshAfterDecision, refreshEverything } from './proposalCache'

/** The Learning page — the Proposal Inbox plus the capture week panel.
 *
 *  This closes LEARNING-FLYWHEEL success criterion 1 ("One Proposal Inbox SHOWS all six proposal
 *  kinds with provenance, evidence manifests, and risk-tier metadata; accept installs, reject
 *  dismisses"). Everything behind that sentence shipped in earlier sessions with no surface, so the
 *  criterion was unmet for want of a page.
 *
 *  The backend owns every judgement here. Ordering (`manual_only` first, unscored above even that),
 *  bulk eligibility, and renderability all arrive decided — this renders them. Re-deriving any of
 *  them in TS would eventually disagree with the server, and the FE would be the copy shipping the
 *  permissive answer. */
export function LearningPage() {
  const [kind, setKind] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')

  const { data: inbox, loading, refresh: refreshProposals } = useCachedData<LearningInbox>(
    proposalsKey(kind),
    () => api.learningProposals(kind ? { kind } : undefined),
  )
  const { data: week, refresh: refreshWeek } = useCachedData<StagingWeek>(
    WEEK_KEY,
    () => api.learningStagingWeek(7),
  )

  // Kind chips carry their counts, so a filter never has to be clicked to discover it is empty.
  const kindChips = useMemo(() => {
    const counts = inbox?.by_kind ?? {}
    return [
      { key: '', label: `All ${inbox ? `(${inbox.total})` : ''}`.trim() },
      ...Object.entries(counts).map(([k, n]) => ({ key: k, label: `${kindLabel(k)} (${n})` })),
    ]
  }, [inbox])

  async function decide(row: LearningRow, verb: 'accept' | 'reject') {
    setBusy(row.id)
    setErr('')
    try {
      if (verb === 'accept') await api.acceptLearningProposal(row.id)
      else await api.rejectLearningProposal(row.id)
      refreshAfterDecision(refreshProposals)
    } catch (e) {
      // A 403 here is the human-installs gate, not a bug — surface the server's own words rather than
      // a generic failure, because "an agent may propose but never accept" is the actionable message.
      setErr(e instanceof Error ? e.message : `Could not ${verb} the proposal`)
    } finally {
      setBusy('')
    }
  }

  const rows = inbox?.rows ?? []

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={
          <div className="flex items-center gap-s">
            <Brain size={18} className="text-on-surface-var" />
            <span data-type="title-l" className="text-on-surface">Learning</span>
          </div>
        }
        right={
          <QuietButton
            title="Refresh"
            onClick={() => refreshEverything(refreshProposals, refreshWeek)}
          >
            <RefreshCw size={14} /> Refresh
          </QuietButton>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex flex-col gap-xl px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {err && <InlineError icon onDismiss={() => setErr('')}>{err}</InlineError>}

          {week && <WeekPanel week={week} />}

          <div className="flex flex-col gap-m">
            <div className="flex flex-wrap items-center gap-s">
              <span data-type="title-m" className="text-on-surface">Proposals</span>
              {!!inbox?.flagged && (
                <span
                  className="inline-flex items-center gap-1.5 rounded-pill px-m h-6 text-[0.75rem]"
                  style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)', color: 'var(--color-warn)' }}
                  title="These carry an invalid change manifest. They are shown, not dropped — hiding them would bury a proposer bug."
                >
                  <AlertTriangle size={12} /> {inbox.flagged} flagged
                </span>
              )}
            </div>
            {kindChips.length > 1 && (
              <Segmented
                options={kindChips.map((c) => ({ key: c.key, label: c.label }))}
                value={kind}
                onChange={setKind}
              />
            )}
            {loading && !inbox ? (
              <ListSkeleton rows={4} />
            ) : rows.length === 0 ? (
              <EmptyState
                icon={Brain}
                title="Nothing to review"
                hint="Proposals appear here when the system notices a pattern worth offering. Nothing is ever installed without your accept."
              />
            ) : (
              <div className="flex flex-col gap-s">
                {rows.map((row) => (
                  <ProposalRow
                    key={row.id}
                    row={row}
                    busy={busy === row.id}
                    onAccept={() => decide(row, 'accept')}
                    onReject={() => decide(row, 'reject')}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/** One proposal row. Everything a reviewer needs to decide without opening anything else — §6.1 names
 *  the fields, and each absence produces a specific bad review. */
function ProposalRow({ row, busy, onAccept, onReject }: {
  row: LearningRow
  busy: boolean
  onAccept: () => void
  onReject: () => void
}) {
  const Icon = kindIcon(row.kind)
  const blocked = bulkBlockedReason(row)
  return (
    <div className="rounded-lg bg-surface-container px-l py-l">
      <div className="flex items-start gap-l">
        <div className="mt-0.5 shrink-0 text-on-surface-var"><Icon size={18} /></div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-s">
            <span data-type="title-s" className="text-on-surface break-words">
              {/* A row with no title still renders, labelled — the backend reports it as
                  unrenderable, and hiding it would make a proposer bug invisible. */}
              {row.title || <span className="text-on-surface-low">(untitled — proposer bug)</span>}
            </span>
            <span
              className="inline-flex items-center rounded-pill px-m h-6 text-[0.75rem]"
              style={{ background: `color-mix(in srgb, ${tierTone(row.risk_tier)} 14%, transparent)`, color: tierTone(row.risk_tier) }}
            >
              {tierLabel(row.risk_tier)}
            </span>
            <span className="rounded-pill bg-surface-high px-m h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">
              {kindLabel(row.kind)}
            </span>
            {!row.manifest_valid && (
              <span
                className="inline-flex items-center gap-1 rounded-pill px-m h-6 text-[0.75rem]"
                style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)', color: 'var(--color-warn)' }}
                title={row.manifest_issues.join('; ')}
              >
                <AlertTriangle size={12} /> manifest
              </span>
            )}
          </div>
          <div className="mt-1 text-on-surface-var text-[0.8125rem]">
            {/* Provenance is what makes a row weighable. The backend refuses to call a row without it
                renderable, so saying so beats rendering a blank. */}
            {row.provenance ? `from ${row.provenance}` : 'source unknown — cannot be weighed'}
            {row.source_cadence ? ` · ${row.source_cadence}` : ''}
            {row.reinforcements > 1 ? ` · seen ${row.reinforcements}×` : ''}
            {row.evidence_refs.length ? ` · ${row.evidence_refs.length} evidence ref(s)` : ' · no evidence'}
          </div>
          {row.source_excerpt && (
            <p className="mt-2 rounded-md bg-surface-high px-m py-2 text-on-surface-var text-[0.75rem] break-words">
              {row.source_excerpt}
            </p>
          )}
          {blocked && (
            <p className="mt-2 text-on-surface-low text-[0.75rem]">Not bulk-acceptable: {blocked}</p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-s">
          <Button size="sm" onClick={onAccept} disabled={busy}><Check size={14} /> Accept</Button>
          <Button size="sm" variant="ghost" onClick={onReject} disabled={busy}><X size={14} /> Dismiss</Button>
        </div>
      </div>
    </div>
  )
}

/** The capture week panel. An EMPTY day is the point: an aggregate health view cannot see a day where
 *  capture never ran, which is the failure the staging tier exists to expose. */
function WeekPanel({ week }: { week: StagingWeek }) {
  return (
    <div className="flex flex-col gap-m">
      <div className="flex flex-wrap items-center gap-s">
        <span data-type="title-m" className="text-on-surface">Capture, last {week.days} days</span>
        {week.silent_days.length > 0 && (
          <span
            className="inline-flex items-center gap-1.5 rounded-pill px-m h-6 text-[0.75rem]"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)', color: 'var(--color-warn)' }}
            title="No capture pass ran on these days. An aggregate view cannot distinguish this from a quiet day."
          >
            <AlertTriangle size={12} /> {week.silent_days.length} silent
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-s">
        {week.buckets.map((day) => {
          const state = dayState(day)
          return (
            <div
              key={day.day}
              className="flex min-w-[72px] flex-1 flex-col items-center gap-1 rounded-lg bg-surface-container px-m py-m"
              title={`${day.day} — ${DAY_HINT[state]} (${day.passes} pass(es), ${day.produced} produced, ${day.errors} error(s))`}
            >
              <span className="text-on-surface-low text-[0.75rem]">{dayLabel(day.day)}</span>
              <span data-type="title-s" style={{ ...fvs(600), color: DAY_TONE[state] }}>
                {day.passes === 0 ? '—' : day.passes}
              </span>
              <span className="text-on-surface-low text-[0.6875rem]">
                {state === 'silent' ? 'silent' : day.produced > 0 ? `${day.produced} filed` : state === 'error' ? 'error' : 'ok'}
              </span>
            </div>
          )
        })}
      </div>
      <p className="text-on-surface-low text-[0.75rem]">
        {week.produced_total} proposal(s) filed
        {week.cost_usd > 0 ? ` · $${week.cost_usd.toFixed(4)} spent` : ''}
        {week.error_days.length ? ` · errors on ${week.error_days.join(', ')}` : ''}
      </p>
    </div>
  )
}
