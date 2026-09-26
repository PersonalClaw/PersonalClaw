import { useMemo, useState } from 'react'
import { AlertTriangle, Brain, Check, RefreshCw, Sparkles, TrendingDown, X } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Button } from '../../ui/Button'
import { QuietButton } from '../../ui/QuietButton'
import { Segmented } from '../../ui/forms'
import { InlineError } from '../../ui/InlineError'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { useQuery } from '../../lib/data'
import { api, isSwitchedOff, type AblationView, type AttentionScope, type BenchmarkView, type SwitchedOffView, type FieldMetricsRow, type IdentityReportView, type JudgeBenchView, type LearningHealth, type LearningInbox, type LearningRow, type NotRunView, type RetrievalBenchView, type StagingWeek, type StudyRow } from '../../lib/api'
import { AblationPanel } from './AblationPanel'
import { AttentionPanel } from './AttentionPanel'
import { FIELD_METRICS_KEY, FieldMetricsPanel, fetchFieldMetrics } from './FieldMetricsPanel'
import { BenchmarkPanel } from './BenchmarkPanel'
import { HealthPanel } from './HealthPanel'
import { IdentityReportPanel } from './IdentityReportPanel'
import { JudgeBenchPanel } from './JudgeBenchPanel'
import { LearningOff } from './LearningOff'
import { RetrievalBenchPanel } from './RetrievalBenchPanel'
import { StudiesPanel } from './StudiesPanel'
import { fvs } from '../../design/fontWeight'
import type { RouteProps } from '../../app/useQueryState'
import {
  DAY_HINT, DAY_TONE, bulkBlockedReason, dayLabel, dayState, evidenceLabel,
  gateLabel, gateRegressed, kindIcon, kindLabel, replayLabel, replayRegressed,
  tierLabel, tierTone,
} from './learningMeta'
import { HEALTH_KEY, IDENTITY_REPORT_KEY, JUDGE_BENCH_KEY, RETRIEVAL_BENCH_KEY, STUDIES_KEY, WEEK_KEY, proposalsKey, refreshAfterDecision, refreshEverything } from './proposalCache'
import { PageTitle } from '../../ui/PageTitle'
import { BUSY_REASON } from '../../ui/unavailable'

/** The ablation report's cache key.
 *
 *  Module-level rather than inline so `dataLayerAdoption`'s census can resolve it, and declared
 *  HERE rather than in `proposalCache.ts` because it has exactly one reader — the drift that file
 *  guards against is a key spelled out at several call sites, which this is not. It follows from
 *  that that the Refresh control re-reads it by calling this panel's own `refresh` beside
 *  `refreshEverything`: with one reader, a refetch IS the invalidation. */
const ABLATION_KEY = 'learning:ablation'

/** The skill-impact benchmark report's cache key (LV-7). Module-level for the same two reasons
 *  `ABLATION_KEY` is: `dataLayerAdoption`'s census resolves it, and it has exactly one reader, so
 *  a refetch IS the invalidation and there is nothing for `proposalCache.ts` to coordinate. */
const BENCHMARK_KEY = 'learning:benchmark'

/** The attention-accounting cache key (ES-16). Module-level for `ABLATION_KEY`'s reasons:
 *  exactly one reader, so a refetch IS the invalidation. */
const ATTENTION_KEY = 'learning:attention'

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
export function LearningPage({ navigate }: Pick<RouteProps, 'navigate'>) {
  const [kind, setKind] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')

  // 🔑 THE FOUR LEARNING READS BELOW CAN ANSWER `{"enabled": false}` (a 200) — the proposals, the
  // capture week, the health panel and the identity report — while `learning.enabled` is off. They
  // used to 404, so the page drew four "Couldn't load" alerts, each with a Retry that could never
  // succeed, for a switch that was merely off. It is one fact about one switch, so it is said once
  // (`learningOff` below) with the way back on, and the four panels it covers are not drawn.
  const { data: inbox, loading, error: inboxError, refresh: refreshProposals } = useQuery<LearningInbox | SwitchedOffView>(
    proposalsKey(kind),
    () => api.learningProposals(kind ? { kind } : undefined),
  )
  const { data: week, error: weekError, refresh: refreshWeek } = useQuery<StagingWeek | SwitchedOffView>(
    WEEK_KEY,
    () => api.learningStagingWeek(7),
  )
  // `error` is READ, not discarded. This panel's subject is "is the flywheel working?", so a
  // swallowed fetch failure would render as "nothing has happened" — the one answer that is
  // never true and never actionable.
  const { data: health, error: healthError, refresh: refreshHealth } = useQuery<LearningHealth | SwitchedOffView>(
    HEALTH_KEY,
    () => api.learningHealth(7),
  )
  // §4.4 attention accounting (ES-16). `error` is read for the health panel's reason: the
  // subject is "what does autonomy still cost?", and a swallowed failure would answer
  // "nothing" — the one claim the panel must never make by accident.
  const { data: attention, error: attentionError, refresh: refreshAttention } = useQuery<{ scopes: AttentionScope[] }>(
    ATTENTION_KEY,
    () => api.workflowAttention(),
  )
  // The lab-vs-field table (ES-9). `error` is read for the attention panel's reason and one
  // more: its divergence flag is the row a demotion was filed on, and a swallowed failure
  // would render "nothing diverged" — the one claim this panel must never make by accident.
  //
  // 🔑 EVERY EVAL READ BELOW CAN ANSWER `{"enabled": false}` (a 200), and each panel renders that
  // itself. With `evals.enabled` off — a default install — these six reads used to 404
  // `evals_disabled`, one console error per panel per visit, for a feature nobody had turned on.
  // The switch stays the backend's judgement: the page asks, the route answers "off".
  const { data: fieldMetrics, error: fieldMetricsError, refresh: refreshFieldMetrics } = useQuery<{ subjects: FieldMetricsRow[] } | SwitchedOffView>(
    FIELD_METRICS_KEY,
    fetchFieldMetrics,
  )
  // The judge tier table (ES-4). `error` is read for the same reason the health panel's is. Its
  // ORDINARY state is "no benchmark has run", a decided `{"ran": false}` the panel renders as the
  // command to run, so what is left in `error` is only ever a real failure.
  const { data: judgeBench, error: judgeBenchError, refresh: refreshJudgeBench } = useQuery<JudgeBenchView | SwitchedOffView | NotRunView>(
    JUDGE_BENCH_KEY,
    () => api.judgeBench(),
  )
  // Pre-registered studies (ES-5). `error` is read for the judge table's reason: a swallowed
  // failure would render an unreadable study tree as "no study has been graduated" — the
  // opposite claim.
  const { data: studies, error: studiesError, refresh: refreshStudies } = useQuery<{ studies: StudyRow[] } | SwitchedOffView>(
    STUDIES_KEY,
    () => api.evalStudies(),
  )
  // Per-arm retrieval ablation (ES-3). `error` is read for the judge table's reason. Its ORDINARY
  // state is "no retrieval benchmark yet" (`{"ran": false}`), and that is the state where the
  // panel still has something useful to offer — the hand-label card.
  const { data: retrievalBench, error: retrievalError, refresh: refreshRetrieval } = useQuery<RetrievalBenchView | SwitchedOffView | NotRunView>(
    RETRIEVAL_BENCH_KEY,
    () => api.retrievalBench(),
  )
  // LV-4's identity report, on the DETERMINISTIC read: no model call, so opening the page costs
  // nothing. The window is NOT passed — the server derives it from the configured cadence, so the
  // period the panel states is the period the scheduled job delivers. Passing 30 here made a
  // weekly install's panel disagree with its own cron. `error` is read for the same reason every
  // panel above reads its own — an unread failure renders as "nothing was learned", which is the
  // one answer this panel must never give by accident.
  const { data: identityReport, error: identityError, refresh: refreshIdentity } = useQuery<IdentityReportView | SwitchedOffView>(
    IDENTITY_REPORT_KEY,
    () => api.identityReport(),
  )

  // The keep/remove/lighten ablation report (ES-7). `error` is read for the judge table's reason,
  // and this route answers THREE distinct non-reports (evals off / nothing has run / unreadable
  // artifacts) that the panel tells apart. Swallowing the error would render the third as nothing
  // at all — and "no ablation has run" is the state a user is in for months, so a broken read must
  // not look like it.
  const { data: ablation, error: ablationError, refresh: refreshAblation } = useQuery<AblationView | SwitchedOffView | NotRunView>(
    ABLATION_KEY,
    () => api.ablation(),
  )
  // The skills-on/off benchmark (LV-7). `error` is read for the ablation route's three reasons and
  // one that is sharper here: this panel's ORDINARY state is "no benchmark has run yet", and the
  // run is 100 real model calls, so most users will be in that state permanently. Swallowing the
  // error would make an unreachable gateway look identical to a benchmark nobody chose to run —
  // and this is the one panel whose whole subject is not overclaiming a measurement.
  const { data: benchmark, error: benchmarkError, refresh: refreshBenchmark } = useQuery<BenchmarkView | SwitchedOffView | NotRunView>(
    BENCHMARK_KEY,
    () => api.learningBenchmark(),
  )

  // Any of the four says it: they read one switch. An off answer counts only while it is that read's
  // latest word — a refetch that fails keeps the previous value as `data`, and "off" must not outlive
  // the switch being turned back on. The views are narrowed here so no panel below has to know the
  // off body exists.
  const saysOff = (v: unknown, err: unknown) => !err && isSwitchedOff(v)
  const learningOff = saysOff(inbox, inboxError) || saysOff(week, weekError)
    || saysOff(health, healthError) || saysOff(identityReport, identityError)
  const proposals = isSwitchedOff(inbox) ? undefined : inbox
  const weekView = isSwitchedOff(week) ? undefined : week
  const healthView = isSwitchedOff(health) ? undefined : health
  const identityView = isSwitchedOff(identityReport) ? undefined : identityReport

  // Kind chips carry their counts, so a filter never has to be clicked to discover it is empty.
  const kindChips = useMemo(() => {
    const counts = proposals?.by_kind ?? {}
    return [
      { key: '', label: `All ${proposals ? `(${proposals.total})` : ''}`.trim() },
      ...Object.entries(counts).map(([k, n]) => ({ key: k, label: `${kindLabel(k)} (${n})` })),
    ]
  }, [proposals])

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

  const rows = proposals?.rows ?? []
  // `?? 0`, so an older gateway that does not send the field reads as "none pending" and the
  // original empty state renders — a missing count must not claim proposals exist.
  const skillPending = proposals?.skill_proposals_pending ?? 0

  const refreshAll = () => {
    refreshEverything(refreshProposals, refreshWeek, refreshHealth, refreshJudgeBench, refreshStudies, refreshRetrieval, refreshIdentity)
    // The ablation report moves only when `personalclaw ablation` or the monthly cadence
    // runs — terminal-side, so its staleness is invisible to the page, exactly like the
    // judge and retrieval tables. It refreshes here rather than inside
    // `refreshEverything` because its key has a single reader, so the refetch is the
    // whole invalidation, not because the parameter list is full.
    refreshAblation()
    // Same reasoning, same shape: the benchmark report moves only when someone runs
    // `scripts/learning_benchmark.py --run`, so its staleness is invisible to the page.
    refreshBenchmark()
    // One reader again — and the field half moves on every thumb/edit/approval,
    // so Refresh is exactly the moment to recompute it.
    refreshFieldMetrics()
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={
          <div className="flex items-center gap-s">
            <Brain size={18} className="text-on-surface-var" />
            <PageTitle>Learning</PageTitle>
          </div>
        }
        right={
          <QuietButton
            title="Refresh"
            onClick={refreshAll}
          >
            <RefreshCw size={14} /> Refresh
          </QuietButton>
        }
      />
      {/* The trio, converging on `scrollRegionNamed`'s form rather than inventing one. Chromium puts
          this scroller in the tab order, and with no role and no label it takes its name from its
          SUBTREE: measured at tab stop 28 on #/learning, 222px of hidden content and **1066
          characters** announced as the region's name, beginning "Capture, last 7 days 7
          silentSun—silentMon—…". A `group` with an explicit `aria-label` does not take its name from
          content, which is the whole point of the pattern. `tabIndex={0}` makes the stop explicit
          instead of relying on Chrome auto-focusing scrollers.
          Named for what it HOLDS — the capture week, the health panel and the proposals list — rather
          than echoing the page's own "Learning" h1, which would add nothing to the announcement. */}
      {/* 🔑 `focus-visible:-outline-offset-2` COMPLETES THE CANONICAL FORM. The trio above
          (`tabIndex` + `role="group"` + `aria-label`) makes this region reachable and named; it does
          not make the focus VISIBLE. The region fills its flex parent, so an outward-drawn ring is
          clipped right and bottom — measured 4px lost on each, the ring's entire reach there. A region
          deliberately given a tab stop, whose focus then cannot be seen, is WCAG 2.4.7. So the inset
          offset belongs to the PATTERN, not to this site, and `scrollRegionNamed` now asserts the
          quartet. */}
      <div tabIndex={0} role="group" aria-label="Capture and proposals" className="flex-1 overflow-y-auto focus-visible:-outline-offset-2">
        <div className="mx-auto flex flex-col gap-xl px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {err && <InlineError icon onDismiss={() => setErr('')}>{err}</InlineError>}

          {learningOff && <LearningOff onTurnedOn={refreshAll} />}

          {/* A failed fetch is not a quiet week. Without this the panel simply VANISHED, and the
              page's whole reason for existing — showing the days capture never ran — disappeared
              silently along with it. */}
          {!learningOff && (week === undefined && weekError
            ? <LoadError what="capture week" error={weekError} onRetry={refreshWeek} />
            : weekView && <WeekPanel week={weekView} />)}

          {!learningOff && (
            <IdentityReportPanel
              report={identityView}
              error={identityError}
              onRetry={refreshIdentity}
              onDelivered={refreshIdentity}
            />
          )}

          {!learningOff && <HealthPanel health={healthView} error={healthError} onRetry={refreshHealth} />}

          <AttentionPanel scopes={attention?.scopes} error={attentionError} onRetry={refreshAttention} />

          <FieldMetricsPanel rows={isSwitchedOff(fieldMetrics) ? fieldMetrics : fieldMetrics?.subjects} error={fieldMetricsError} onRetry={refreshFieldMetrics} />

          <JudgeBenchPanel bench={judgeBench} error={judgeBenchError} onRetry={refreshJudgeBench} />

          <StudiesPanel studies={isSwitchedOff(studies) ? studies : studies?.studies} error={studiesError} onRetry={refreshStudies} />

          <RetrievalBenchPanel bench={retrievalBench} error={retrievalError} onRetry={refreshRetrieval} />

          <AblationPanel view={ablation} error={ablationError} onRetry={refreshAblation} />

          <BenchmarkPanel view={benchmark} error={benchmarkError} onRetry={refreshBenchmark} />


          {!learningOff && <div className="flex flex-col gap-m">
            <div className="flex flex-wrap items-center gap-s">
              <span data-type="title-m" className="text-on-surface">Proposals</span>
              {!!proposals?.flagged && (
                <span
                  className="inline-flex items-center gap-1.5 rounded-pill px-m h-6 text-[0.75rem]"
                  style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)', color: 'var(--color-warn)' }}
                  title="These carry an invalid change manifest. They are shown, not dropped — hiding them would bury a proposer bug."
                >
                  <AlertTriangle size={12} /> {proposals.flagged} flagged
                </span>
              )}
            </div>
            {kindChips.length > 1 && (
              <Segmented
                ariaLabel="Proposal kind"
                options={kindChips.map((c) => ({ key: c.key, label: c.label }))}
                value={kind}
                onChange={setKind}
              />
            )}
            {/* THE one condition that separates "you have none" from "we could not ask":
                measured against a 500 on both learning endpoints, this surface rendered
                "Nothing to review — proposals appear here when the system notices a pattern worth
                offering", with no error text anywhere on the page. That is the most confident
                possible way to say the opposite of what happened. */}
            {inbox === undefined && inboxError ? (
              <LoadError what="proposals" error={inboxError} onRetry={refreshProposals} />
            ) : loading && !proposals ? (
              <ListSkeleton rows={4} what="proposals" />
            ) : rows.length === 0 ? (
              /* 🔴 The empty state must not DENY what another store holds (#321). This said
                 "Nothing to review — proposals appear here when the system notices a pattern
                 worth offering", which describes a skill refinement synthesized from the user's
                 own sessions exactly. Measured with one pending proposal: the identity report
                 lower on THIS PAGE counted it while this panel said there was nothing. Skill
                 proposals live in a different store with its own review UI, so this names them
                 and points there — it does not list them. */
              skillPending > 0 ? (
                <EmptyState
                  icon={Sparkles}
                  title={`${skillPending} skill ${skillPending === 1 ? 'proposal' : 'proposals'} awaiting review`}
                  hint="The ambient lesson pipeline has filed nothing yet. These came from the skill ladder, which keeps its own queue."
                  action={{ label: 'Review in Skill proposals', onClick: () => navigate('skills?mode=proposals'), icon: Sparkles }}
                />
              ) : (
                <EmptyState
                  icon={Brain}
                  title="Nothing to review"
                  hint="Proposals appear here when the system notices a pattern worth offering. Nothing is ever installed without your accept."
                />
              )
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
          </div>}
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
            {/* A MEASURED score drop is the one gate outcome that earns a chip. An ungated row does
                not get one: "we did not measure" is not a warning, and dressing it as one would
                train reviewers to ignore the chip that means something. */}
            {gateRegressed(row) && (
              <span
                className="inline-flex items-center gap-1 rounded-pill px-m h-6 text-[0.75rem]"
                style={{ background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }}
                title={gateLabel(row)}
              >
                <TrendingDown size={12} /> score drop
              </span>
            )}
            {/* The replay harness's own chip, separate from the gate's. A row can regress on the
                shipped scenario library and improve on the user's captured turns (or the reverse),
                and one merged chip would make those two facts indistinguishable — which is exactly
                the disagreement a reviewer most needs to see. Same rule as the gate chip: only a
                MEASURED drop earns one, because "we did not replay" is not a warning. */}
            {replayRegressed(row) && (
              <span
                className="inline-flex items-center gap-1 rounded-pill px-m h-6 text-[0.75rem]"
                style={{ background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }}
                title={replayLabel(row)}
              >
                <TrendingDown size={12} /> replay drop
              </span>
            )}
          </div>
          <div className="mt-1 text-on-surface-var text-[0.8125rem]">
            {/* Provenance is what makes a row weighable. The backend refuses to call a row without it
                renderable, so saying so beats rendering a blank. */}
            {row.provenance ? `from ${row.provenance}` : 'source unknown — cannot be weighed'}
            {row.source_cadence ? ` · ${row.source_cadence}` : ''}
            {row.reinforcements > 1 ? ` · seen ${row.reinforcements}×` : ''}
            {` · ${evidenceLabel(row)}`}
          </div>
          {/* The Loop-2 gate clause, on its own line rather than folded into the `·` chain: it is
              two NUMBERS plus what produced them, and burying that in a run-on metadata sentence is
              how a reviewer misses a regression. Always rendered — an ungated proposal says so. */}
          <div className="mt-1 text-on-surface-low text-[0.8125rem]">
            {gateLabel(row)}
            {row.gate?.pin?.model_fp ? ` · pinned ${row.gate.pin.model_fp}` : ''}
          </div>
          {/* The replay clause, on its own line beside the gate's for the same reason: two numbers
              and what produced them. Always rendered — a proposal nothing replayed says so, and it
              stays fully acceptable. This is evidence the reviewer weighs, never a veto. */}
          <div className="mt-1 text-on-surface-low text-[0.8125rem]">
            {replayLabel(row)}
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
          <Button size="sm" onClick={onAccept} disabled={busy} disabledReason={BUSY_REASON}><Check size={14} /> Accept</Button>
          {/* "Reject", not "Dismiss". The app already distinguishes the two verbs consistently:
              DISMISS triages an item off your list (InboxDetail writes `status: 'dismissed'`),
              while REJECT declines a PROPOSAL and is always paired with Accept. This row is a
              proposal — the handler is `decide(row, 'reject')`, the endpoint is
              `rejectLearningProposal`, the prop is `onReject`, and the file's own doc comment says
              "accept installs, reject …". Every layer said reject; only the label said Dismiss. */}
          <Button size="sm" variant="ghost" onClick={onReject} disabled={busy} disabledReason={BUSY_REASON}><X size={14} /> Reject</Button>
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
        {/* Before the FIRST pass ever, silence is a zero-state. A missing floor is a stale payload
            and fails toward preserving the warning; a known floor excludes earlier days so the
            chip and tiles cannot contradict each other. */}
        {week.first_pass_day === '' ? (
          <span data-type="caption" className="text-on-surface-low">no capture pass has run yet</span>
        ) : week.silent_days.length > 0 ? (
          <span
            className="inline-flex items-center gap-1.5 rounded-pill px-m h-6 text-[0.75rem]"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)', color: 'var(--color-warn)' }}
            title="No capture pass ran on these days. An aggregate view cannot distinguish this from a quiet day."
          >
            <AlertTriangle size={12} /> {week.silent_days.length} silent
          </span>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-s">
        {week.buckets.map((day) => {
          const state = dayState(day, week.first_pass_day)
          return (
            <div
              key={day.day}
              className="flex min-w-[72px] flex-1 flex-col items-center gap-1 rounded-lg bg-surface-container px-m py-m"
              title={`${day.day} — ${DAY_HINT[state]} (${day.passes} pass${day.passes === 1 ? '' : 'es'}, ${day.produced} produced, ${day.errors} error${day.errors === 1 ? '' : 's'})`}
            >
              <span className="text-on-surface-low text-[0.75rem]">{dayLabel(day.day)}</span>
              <span data-type="title-s" style={{ ...fvs(600), color: DAY_TONE[state] }}>
                {day.passes === 0 ? '—' : day.passes}
              </span>
              <span className="text-on-surface-low text-[0.6875rem]">
                {state === 'not_started' ? 'not started' : state === 'silent' ? 'silent' : day.produced > 0 ? `${day.produced} filed` : state === 'error' ? 'error' : 'ok'}
              </span>
            </div>
          )
        })}
      </div>
      <p className="text-on-surface-low text-[0.75rem]">
        {week.produced_total} proposal{week.produced_total === 1 ? '' : 's'} filed
        {week.cost_usd > 0 ? ` · $${week.cost_usd.toFixed(4)} spent` : ''}
        {week.error_days.length ? ` · errors on ${week.error_days.join(', ')}` : ''}
      </p>
    </div>
  )
}
