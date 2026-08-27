import { useState } from 'react'
import { ChevronDown, ChevronRight, FlaskConical, ShieldAlert } from 'lucide-react'
import { LoadError } from '../../ui/ListScaffold'
import { QuietButton } from '../../ui/QuietButton'
import { useQuery } from '../../lib/data'
import { api, type StudyPair, type StudyRow, type StudyView } from '../../lib/api'
import { studyDetailKey } from './proposalCache'
import { EvalsOffNotice, evalsCode } from './evalsOff'

/** Pre-registered template A/B studies (EVALUATION-SUBSTRATE §2 / ES-5).
 *
 *  The formal answer to "is template v(N+1) actually better than v(N)?" — k paired runs over
 *  the study's registered input cases, judged blind at both positions, against a rubric hashed
 *  before arm 1 ran.
 *
 *  Three rendering rules, each of which exists because breaking it would report the
 *  opposite of what happened:
 *
 *  1. **`null` is "not measured", never 0.** An unmeasurable agreement rate is exactly WHY a
 *     study is `judge_unreliable`; drawing it as 0% would claim we measured a catastrophically
 *     position-biased judge, when the truth is that no pair was judgeable at all.
 *  2. **`invalidated` and `judge_unreliable` are shown as loudly as a win.** They are the
 *     append-only honesty §2.4 asks for. A UI that hid them would leave a user believing the
 *     study had not run, and the next thing they would do is re-register it — which is
 *     precisely the re-interpretation the pre-registration exists to prevent.
 *  3. **Nothing is re-decided here.** The verdict, the floor it was judged against and the
 *     locked-check regressions all arrive computed. A panel that re-derived "did it win"
 *     from `win_rate > 0.5` would eventually disagree with the substrate, and the copy
 *     shipping the permissive answer would be this one.
 *
 *  🔴 The rubric TEXT and the `locked/` checks are absent by design, not by omission — the
 *  server does not serve them (§2.2), so there is nothing here to render. The rubric's hash
 *  is shown instead: enough to prove the pin, not enough to satisfy it. */
export function StudiesPanel({ studies, error, onRetry }: {
  studies: StudyRow[] | undefined
  error: unknown
  onRetry: () => void
}) {
  const [open, setOpen] = useState('')

  // A 404 is the ordinary state — no study has been registered — so it renders as guidance.
  // Any other failure is surfaced, because "no studies" and "we could not read the studies"
  // send a user to two different places.
  if (studies === undefined && error) {
    // "Off" is NOT "empty", and this panel used to answer both with the empty state. A user
    // who has never switched the substrate on was told "No study has been registered" — true
    // but useless, since registering one would not have helped. The two states name two
    // different next actions, so they get two renderings.
    if (evalsCode(error, 'evals_disabled')) {
      return (
        <section className="flex flex-col gap-s" aria-labelledby="studies-heading">
          <Heading />
          <EvalsOffNotice>
            graduate a template change on evidence rather than on a hunch
          </EvalsOffNotice>
        </section>
      )
    }
    if (isAbsent(error)) return <Empty />
    return <LoadError what="studies" error={error} onRetry={onRetry} />
  }
  if (!studies) return null
  if (studies.length === 0) return <Empty />

  return (
    <section className="flex flex-col gap-m" aria-labelledby="studies-heading">
      <Heading count={studies.length} />
      <p className="text-on-surface-low text-[0.75rem]">
        Each study is registered before its first run and judged against a rubric pinned by
        hash. Editing that rubric mid-study invalidates it rather than re-scoring it. Pairs are
        judged blind at both positions; below the agreement floor the study reports an
        unreliable judge instead of a winner.
      </p>
      <ul className="flex flex-col gap-xs">
        {studies.map((study) => (
          <li key={study.study_id} className="flex flex-col gap-xs rounded-lg bg-surface-container pt-m">
            <div className="flex items-start gap-s px-l">
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span data-type="title-s" className="truncate text-on-surface">
                  {String(study.subject.template_id ?? study.study_id)}
                  {versionRange(study)}
                </span>
                {study.hypothesis && (
                  <span className="truncate text-on-surface-low text-[0.75rem]">{study.hypothesis}</span>
                )}
                <span className="text-on-surface-low text-[0.75rem]">
                  k={study.k} · agreement {fmtRate(study.agreement)} (floor{' '}
                  {fmtRate(study.agreement_floor)}) · win rate {fmtRate(study.win_rate)}
                </span>
              </div>
              <VerdictBadge study={study} />
            </div>
            {/* A LABELLED disclosure rather than a whole-row button. The row carries three lines
                of summary, so making all of it the control would give a screen-reader user one
                long unreadable name — and `QuietButton` already owns the disclosure shape
                (`aria-expanded` included), so this adds no fourth spelling of it. */}
            <div className="px-l pb-m">
              <QuietButton
                ariaExpanded={open === study.study_id}
                onClick={() => setOpen(open === study.study_id ? '' : study.study_id)}
              >
                {open === study.study_id
                  ? <ChevronDown size={13} aria-hidden="true" />
                  : <ChevronRight size={13} aria-hidden="true" />}
                {open === study.study_id ? 'Hide runs' : 'Show runs'}
              </QuietButton>
            </div>
            {open === study.study_id && <StudyDetail studyId={study.study_id} />}
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The per-run drill-down (§2.4: "a surprising aggregate is always drillable").
 *
 *  Mounted only while a study is expanded, which is also how the fetch is gated — the panel
 *  lists studies from one cheap read and pays for a detail read on demand. */
function StudyDetail({ studyId }: { studyId: string }) {
  const { data: view, error, refresh } = useQuery<StudyView>(
    studyDetailKey(studyId),
    () => api.evalStudy(studyId),
  )
  if (view === undefined && error) {
    return (
      <div className="px-l pb-m">
        <LoadError what="study detail" error={error} onRetry={refresh} />
      </div>
    )
  }
  if (!view) {
    return <p className="px-l pb-m text-on-surface-low text-[0.75rem]">Loading the runs…</p>
  }
  return (
    <div className="flex flex-col gap-m border-t border-outline-variant px-l py-m">
      <dl className="grid grid-cols-[auto_1fr] gap-x-m gap-y-0.5 text-[0.75rem]">
        <dt className="text-on-surface-low">Decision rule</dt>
        <dd className="text-on-surface-var">{view.decision_rule}</dd>
        <dt className="text-on-surface-low">Rubric pinned at</dt>
        <dd className="font-mono text-on-surface-var">{view.rubric_sha256.slice(0, 16)}</dd>
        <dt className="text-on-surface-low">Hidden locked checks</dt>
        <dd className="text-on-surface-var">
          {view.locked_check_count} — executed by the supervisor in each run's output
          workspace, never shown to a run
        </dd>
      </dl>

      {view.verdict === null
        ? (
          <p className="text-on-surface-low text-[0.8125rem]">
            Registered, not yet run. The design is sealed: k={view.k} over {view.inputs.length}{' '}
            case{view.inputs.length === 1 ? '' : 's'}.
          </p>
        )
        : <Tally verdict={view.verdict} />}

      {view.verdict?.detail && (
        <p className="text-on-surface-low text-[0.8125rem]">{view.verdict.detail}</p>
      )}

      {view.verdict && view.verdict.locked_regressions.length > 0 && (
        <div className="flex flex-col gap-0.5 rounded-md border border-error bg-surface-high px-m py-s">
          <span className="flex items-center gap-xs text-error text-[0.75rem]">
            <ShieldAlert size={13} aria-hidden="true" />
            Locked-check regression — a fail regardless of the win rate
          </span>
          <ul className="flex flex-col gap-0.5 text-error text-[0.75rem]">
            {view.verdict.locked_regressions.map((r) => <li key={r} className="font-mono">{r}</li>)}
          </ul>
        </div>
      )}

      {view.verdict && !view.verdict.ledger_row_written && (
        <p className="text-on-surface-low text-[0.75rem]">
          This verdict is not in <code className="text-on-surface-var">results.tsv</code>: the run
          could not be attributed to a model binding, and an unattributable score is refused
          rather than recorded.
        </p>
      )}

      {view.runs.length > 0 && (
        <div className="flex flex-col gap-xs">
          <span data-type="title-s" className="text-on-surface">Per-run pairs</span>
          <div className="overflow-x-auto">
            <table className="w-full text-[0.75rem]">
              <caption className="sr-only">
                Every judged pair for study {view.study_id}, by case and trial
              </caption>
              <thead>
                <tr className="text-left text-on-surface-low">
                  <th scope="col" className="py-0.5 pr-m font-normal">Case</th>
                  <th scope="col" className="py-0.5 pr-m font-normal">Trial</th>
                  <th scope="col" className="py-0.5 pr-m font-normal">Slot A held</th>
                  <th scope="col" className="py-0.5 pr-m font-normal">A/B</th>
                  <th scope="col" className="py-0.5 pr-m font-normal">B/A</th>
                  <th scope="col" className="py-0.5 font-normal">Counted as</th>
                </tr>
              </thead>
              <tbody>
                {view.runs.flatMap((run) =>
                  run.pairs.map((pair) => (
                    <PairRow key={`${run.case_id}-${pair.trial}`} caseId={run.case_id} pair={pair} />
                  )))}
              </tbody>
            </table>
          </div>
          <p className="text-on-surface-low text-[0.75rem]">
            "Slot A held" is the randomized assignment, recorded outside the judge's prompt. A
            pair whose winner changes when the slots do is counted for neither arm.
          </p>
        </div>
      )}
    </div>
  )
}

function PairRow({ caseId, pair }: { caseId: string; pair: StudyPair }) {
  return (
    <tr className="text-on-surface-var">
      <td className="py-0.5 pr-m">{caseId}</td>
      <td className="py-0.5 pr-m">{pair.trial}</td>
      <td className="py-0.5 pr-m">{pair.slot_a_arm}</td>
      <td className="py-0.5 pr-m">{pair.direct_winner}</td>
      <td className="py-0.5 pr-m">{pair.swapped_winner}</td>
      <td className="py-0.5">
        {pair.position_flipped
          ? <span className="text-on-surface-low">no signal — flipped with position</span>
          : pair.outcome}
      </td>
    </tr>
  )
}

function Tally({ verdict }: { verdict: NonNullable<StudyView['verdict']> }) {
  return (
    <p className="text-on-surface-var text-[0.8125rem]">
      {verdict.wins} win · {verdict.losses} loss · {verdict.ties} tie · {verdict.no_signal} no
      signal, over {verdict.decided_cases} decided case
      {verdict.decided_cases === 1 ? '' : 's'} at k={verdict.k}. Position-swap agreement{' '}
      {fmtRate(verdict.agreement)} against a {fmtRate(verdict.agreement_floor)} floor.
      {verdict.low_power && ' Low power: too few decided cases to be more than suggestive.'}
    </p>
  )
}

/** The verdict, named as the substrate named it.
 *
 *  `invalidated` and `judge_unreliable` get their own words rather than collapsing into
 *  "inconclusive": one means the rubric moved and the other means the judge cannot be
 *  trusted, and they are fixed in completely different places. */
function VerdictBadge({ study }: { study: StudyRow }) {
  if (study.verdict === null) {
    return <span className="shrink-0 text-on-surface-low text-[0.75rem]">not run yet</span>
  }
  // Real tokens only. The Material-style *-container / on-*-container names this first reached
  // for are NOT in `design/tokens.css`, so they compile to nothing: the badge would have shipped
  // with no background and — worse — no colour difference between a win and a loss. Caught by the
  // inert-utility rail. Their literal spellings are kept out of this comment because that rail
  // scans the whole file, so naming them here would re-report them as offenders.
  const tone = study.verdict === 'win'
    ? 'bg-surface-high text-success'
    : study.verdict === 'loss'
      ? 'bg-surface-high text-error'
      : 'bg-surface-high text-on-surface-var'
  return (
    <span className={`shrink-0 rounded-full px-s py-0.5 text-[0.75rem] ${tone}`}>
      {VERDICT_LABELS[study.verdict] ?? study.verdict}
    </span>
  )
}

/** The closed set the server can send. A value NOT in here falls through to the raw string
 *  rather than to a friendly default: a default branch would swallow a verdict this UI has
 *  never heard of and render it as something reassuring. */
const VERDICT_LABELS: Record<string, string> = {
  win: 'candidate wins',
  loss: 'candidate loses',
  tie: 'no difference',
  invalidated: 'invalidated — rubric moved',
  judge_unreliable: 'judge unreliable',
}

function Empty() {
  return (
    <section className="flex flex-col gap-s" aria-labelledby="studies-heading">
      <Heading />
      <p className="text-on-surface-low text-[0.8125rem]">
        No study has been registered. A study is the deliberate instrument for graduating a
        template change — k paired runs, a rubric pinned by hash, and hidden checks the runs
        cannot read. It is registered before it runs, on purpose, so its result cannot be
        re-interpreted afterwards.
      </p>
    </section>
  )
}

function Heading({ count }: { count?: number } = {}) {
  return (
    <div className="flex items-center gap-s">
      <FlaskConical size={16} className="text-on-surface-var" aria-hidden="true" />
      <h2 id="studies-heading" data-type="title-m" className="text-on-surface">
        Template studies
      </h2>
      {count !== undefined && (
        <span className="text-on-surface-low text-[0.75rem]">{count}</span>
      )}
    </div>
  )
}

function versionRange(study: StudyRow): string {
  const from = study.subject.old_version
  const to = study.subject.new_version
  if (from === undefined || to === undefined) return ''
  return ` v${String(from)} → v${String(to)}`
}

/** "No study has been registered" is a 404 the panel EXPECTS. Matched on the backend's
 *  stable `code`, never on prose — the message is human copy and may be reworded.
 *
 *  `evals_disabled` used to be folded in here and is now its own rendering above: it is the
 *  one 404 that registering a study would not fix. Note that the LIST route this panel reads
 *  answers 200 + `[]` for an empty registry (`handlers/evals.py:api_evals_studies`) and mints
 *  `study_absent` only on the per-study route, so this arm is the shape a caller passing a
 *  single-study rejection would land in — the same sentence, so the same rendering. */
function isAbsent(error: unknown): boolean {
  return evalsCode(error, 'study_absent')
}

/** A rate. `null` is UNMEASURED — the one value that must never render as 0%, because an
 *  unmeasurable agreement is the reason a study reports an unreliable judge. */
function fmtRate(value: number | null): string {
  return value === null ? 'not measured' : `${Math.round(value * 100)}%`
}
