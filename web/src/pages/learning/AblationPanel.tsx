import { Scissors, ShieldAlert } from 'lucide-react'
import { LoadError } from '../../ui/ListScaffold'
import { fvs } from '../../design/fontWeight'
import type {
  AblationArmAggregate, AblationHistoryEntry, AblationRegistryRow, AblationView,
} from '../../lib/api'

/** The keep/remove/lighten ablation report (EVALUATION-SUBSTRATE §3.1 / ES-7).
 *
 *  One component is toggled off — or down to a declared cheaper form — and the benchmark is
 *  replayed on each arm. The delta says whether the component earns its keep. A `remove`
 *  verdict ALSO reaches the user as a LEARN-R9 retirement proposal in the inbox; this panel is
 *  the evidence behind it, and the ONLY surface a `keep` or `lighten` verdict has at all.
 *
 *  **Nothing is re-decided here.** The verdict, both deltas and the `epsilon` they were
 *  compared against all arrive computed. A UI that re-derived "is this a real delta" would
 *  eventually disagree with the runner, and the copy shipping the permissive answer would be
 *  this one.
 *
 *  **`null` renders as "not measured", never as a zero.** An arm whose every cell came back
 *  `verifier_absent` has no mean — that absence is exactly why a report is `inconclusive`
 *  rather than `remove`, and drawing 0.000 for it would turn "we never measured this" into
 *  "this scored nothing", which is the strongest possible case for retiring the component. */
export function AblationPanel({ view, error, onRetry }: {
  view: AblationView | undefined
  error: unknown
  onRetry: () => void
}) {
  // THREE states, not two. The backend mints a distinct code for each on purpose (see
  // `handlers/evals.py:api_evals_ablation`) because they send the reader to three different
  // places: the switch, the registry, and a bug report. Collapsing any of them into the others
  // makes this panel's empty state a guess — and a failed fetch rendering as "nothing has run
  // yet" is the specific confusion this section is built to refuse.
  if (view === undefined && error) {
    if (hasCode(error, 'evals_disabled')) {
      return (
        <section className="flex flex-col gap-s" aria-labelledby="ablation-heading">
          <Heading />
          <p className="text-on-surface-low text-[0.8125rem]">
            The eval substrate is off, so no ablation can run. Turn on{' '}
            <code className="text-on-surface-var">evals.enabled</code> in{' '}
            <a className="underline" href="#/settings">Settings</a> to start measuring which
            harness components earn their keep.
          </p>
        </section>
      )
    }
    if (hasCode(error, 'ablation_absent')) {
      return (
        <section className="flex flex-col gap-s" aria-labelledby="ablation-heading">
          <Heading />
          <p className="text-on-surface-low text-[0.8125rem]">
            No ablation has run yet. Register a component in{' '}
            <code className="text-on-surface-var">evals/ablation_registry.json</code> and run{' '}
            <code className="text-on-surface-var">personalclaw ablation --force</code>. It is a
            deliberate command on a monthly cadence, not a background job — one report is a
            multi-cell matrix, so nothing here spends money on its own.
          </p>
        </section>
      )
    }
    return <LoadError what="ablation report" error={error} onRetry={onRetry} />
  }
  if (!view) return null

  const report = view.report
  const armKeys = orderArms(Object.keys(report.arms))

  return (
    <section className="flex flex-col gap-m" aria-labelledby="ablation-heading">
      <Heading matrixId={report.matrix_id} />

      <VerdictCard view={view} />

      <p className="text-on-surface-low text-[0.75rem]">
        A gain below ε = {report.epsilon} is no gain. The comparison is SIGNED: a component whose
        absence <em>improved</em> the benchmark is not a keep with a negative delta, it is a
        component that does not pay for itself. Measured over {report.trials}{' '}
        trial{report.trials === 1 ? '' : 's'} per arm
        {report.created_at ? <> · {report.created_at}</> : null}.
      </p>

      <div className="flex flex-col gap-xs">
        <span data-type="title-s" className="text-on-surface">
          {report.component_id || 'component'} · {report.kind} · {report.target}
        </span>
        <div className="overflow-x-auto rounded-lg bg-surface-container">
          <table className="w-full text-[0.75rem]">
            <caption className="sr-only">
              Per-arm results for the {report.component_id || 'ablated'} component, replayed over
              the {report.subject || 'registered'} scenario
            </caption>
            <thead>
              <tr className="text-on-surface-low">
                <th scope="col" className="px-m py-s text-left">Arm</th>
                <th scope="col" className="px-m py-s text-right">Cells</th>
                <th scope="col" className="px-m py-s text-right">Scored</th>
                <th scope="col" className="px-m py-s text-right">Mean score</th>
                <th scope="col" className="px-m py-s text-right">Verifier absent</th>
              </tr>
            </thead>
            <tbody>
              {armKeys.map((arm) => (
                <ArmRow key={arm || 'unattributed'} arm={arm} agg={report.arms[arm]} />
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-on-surface-low text-[0.75rem]">
          on − off: <span className="text-on-surface-var">{fmtDelta(report.delta)}</span>
          {report.cheap_delta !== null && (
            <> · on − cheap: <span className="text-on-surface-var">{fmtDelta(report.cheap_delta)}</span></>
          )}
          {Object.keys(report.live_state).length > 0 && (
            <> · {Object.keys(report.live_state).length} live file
              {Object.keys(report.live_state).length === 1 ? '' : 's'} watched and unchanged</>
          )}
        </p>
      </div>

      <Cadence view={view} />

      {view.history.length > 0 && <History history={view.history} />}

      {view.registry.length > 0 && <Registry rows={view.registry} />}
    </section>
  )
}

function Heading({ matrixId }: { matrixId?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-s">
      <Scissors size={16} className="text-on-surface-var" />
      <span id="ablation-heading" data-type="title-m" className="text-on-surface">
        Component ablation
      </span>
      {matrixId && (
        <span className="text-on-surface-low text-[0.75rem]">{matrixId}</span>
      )}
    </div>
  )
}

/** The verdict, as a verdict.
 *
 *  Not `keep` in a monospace pill: the reader's question is "do I act on this, and how", and a
 *  raw enum answers neither. `inconclusive` gets the loudest treatment of the four because it
 *  is the one the runner refuses to turn into a recommendation — reading it as a `remove` is
 *  how a component gets retired on a measurement that never happened. */
function VerdictCard({ view }: { view: AblationView }) {
  const { verdict, target, component_id: componentId } = view.report
  const subject = target || componentId || 'this component'
  const decided = view.verdict_vocabulary.includes(verdict)
  return (
    <div className="flex flex-col gap-xs rounded-lg bg-surface-container px-l py-m">
      <span data-type="title-s" className="text-on-surface">
        {verdict === 'keep' && <>Keep {subject}</>}
        {verdict === 'remove' && <>Retire {subject}</>}
        {verdict === 'lighten' && <>Lighten {subject}</>}
        {!decided && <>No verdict for {subject}</>}
      </span>
      {!decided && (
        <span
          className="inline-flex w-fit items-center gap-1.5 rounded-pill px-m h-6 text-[0.75rem]"
          style={{
            background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)',
            color: 'var(--color-warn)',
          }}
        >
          <ShieldAlert size={12} /> inconclusive
        </span>
      )}
      <p className="text-on-surface-low text-[0.8125rem]">
        {verdict === 'keep' && (
          <>Switching it off measurably degraded the benchmark, by more than ε. It earns its keep;
            there is nothing to do.</>
        )}
        {verdict === 'remove' && (
          <>Switching it off changed nothing measurable. A retirement proposal is waiting in your{' '}
            <a className="underline" href="#/inbox">Inbox</a> — the harness proposes, you decide.</>
        )}
        {verdict === 'lighten' && (
          <>It pays for itself, but a deliberately cheaper variant matched the full one. Swapping
            in the cheap form keeps the gain and drops the cost.</>
        )}
        {!decided && (
          <>An arm produced no scored cell, so there is no delta to read. This is deliberately not
            a retirement recommendation: an absent verifier is never a zero, and treating it as
            one would retire a component on a measurement that never happened.</>
        )}
      </p>
      <span className="text-on-surface-low text-[0.75rem]" style={fvs(600)}>
        {view.report.subject ? <>replayed over {view.report.subject}</> : null}
      </span>
    </div>
  )
}

function ArmRow({ arm, agg }: { arm: string; agg: AblationArmAggregate }) {
  return (
    <tr className="border-t border-outline-variant/30 align-top">
      <td className="px-m py-s text-on-surface">{armLabel(arm)}</td>
      <td className="px-m py-s text-right text-on-surface-var">{agg.total}</td>
      <td className="px-m py-s text-right text-on-surface-var">{agg.scored_count}</td>
      <td className="px-m py-s text-right text-on-surface-var">{fmtMean(agg.mean_score)}</td>
      <td className="px-m py-s text-right text-on-surface-var">
        {agg.counts.verifier_absent ?? 0}
      </td>
    </tr>
  )
}

/** Last run and whether the cadence is overdue. `due` arrives decided — the page has no
 *  business re-deriving it from a timestamp and a day count, which is how a UI ends up
 *  disagreeing with the scheduler that actually fires. */
function Cadence({ view }: { view: AblationView }) {
  return (
    <p className="text-on-surface-low text-[0.75rem]">
      Every {view.cadence_days} day{view.cadence_days === 1 ? '' : 's'}, one component in turn.{' '}
      {view.last_run_ts
        ? <>Last run {view.last_run_ts}.</>
        : <>Never run.</>}{' '}
      {view.due
        ? <span className="text-on-surface-var" style={fvs(600)}>Due now — run <code>personalclaw ablation</code>.</span>
        : <>Not due yet.</>}
    </p>
  )
}

/** Past cadences, newest first. The `proposal` column is the point: a `remove` verdict with
 *  nothing filed is a DROPPED recommendation, and it looks identical to a completed one
 *  unless the panel says so. */
function History({ history }: { history: AblationHistoryEntry[] }) {
  const rows = [...history].reverse()
  return (
    <div className="flex flex-col gap-xs">
      <span data-type="title-s" className="text-on-surface">Past cadences</span>
      <div className="overflow-x-auto rounded-lg bg-surface-container">
        <table className="w-full text-[0.75rem]">
          <caption className="sr-only">
            The last {rows.length} ablation cadence run{rows.length === 1 ? '' : 's'}, newest first
          </caption>
          <thead>
            <tr className="text-on-surface-low">
              <th scope="col" className="px-m py-s text-left">When</th>
              <th scope="col" className="px-m py-s text-left">Component</th>
              <th scope="col" className="px-m py-s text-left">Verdict</th>
              <th scope="col" className="px-m py-s text-right">Delta</th>
              <th scope="col" className="px-m py-s text-left">Proposal</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((entry) => (
              <tr key={`${entry.ts}-${entry.component_id}`} className="border-t border-outline-variant/30">
                <td className="px-m py-s text-on-surface-var">{entry.ts}</td>
                <td className="px-m py-s text-on-surface">{entry.component_id}</td>
                <td className="px-m py-s text-on-surface-var">{entry.verdict}</td>
                <td className="px-m py-s text-right text-on-surface-var">{fmtDelta(entry.delta)}</td>
                <td className="px-m py-s text-on-surface-low">{proposalLabel(entry)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** What CAN be ablated. Without this the report reads as the whole story, when it is one
 *  component's turn out of a round-robin — and a registry of one is a very different claim
 *  about coverage than a registry of ten. */
function Registry({ rows }: { rows: AblationRegistryRow[] }) {
  return (
    <div className="flex flex-col gap-xs">
      <span data-type="title-s" className="text-on-surface">
        Registered components ({rows.length})
      </span>
      <ul className="flex flex-col gap-xs">
        {rows.map((row) => (
          <li key={row.component_id} className="rounded-lg bg-surface-container px-l py-m">
            <span className="text-on-surface text-[0.8125rem]" style={fvs(600)}>{row.component_id}</span>
            <span className="text-on-surface-low text-[0.75rem]"> · {row.kind} · {row.target}</span>
            {row.description && (
              <p className="text-on-surface-low text-[0.75rem]">{row.description}</p>
            )}
            {row.cheap_value === null && (
              <p className="text-on-surface-low text-[0.75rem]">
                No cheap form declared, so this component can never earn a lighten verdict.
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Matched on the backend's stable `code`, not on prose: the message is human copy and may be
 *  reworded, the code may not. */
function hasCode(error: unknown, code: string): boolean {
  const text = error instanceof Error ? error.message : String(error ?? '')
  return text.includes(code)
}

/** `on`, `off`, `cheap`, then anything else. The runner buckets cells that carry no arm under
 *  `''` rather than dropping them, because a silently discarded cell means the delta was
 *  computed over a different population than the one that ran — so this must not drop them
 *  either. */
function orderArms(keys: string[]): string[] {
  const canonical = ['on', 'off', 'cheap']
  const known = canonical.filter((arm) => keys.includes(arm))
  return [...known, ...keys.filter((k) => !canonical.includes(k))]
}

function armLabel(arm: string): string {
  return arm === '' ? 'unattributed' : arm
}

/** A mean over scored cells. `null` is UNMEASURED — the one value that must never render as
 *  0.000, because for an ablation "scored nothing" is the case for deleting the component. */
function fmtMean(value: number | null): string {
  return value === null ? 'not measured' : value.toFixed(3)
}

/** A signed delta. `null` means an arm was unmeasured, which is what makes the verdict
 *  inconclusive rather than a remove. */
function fmtDelta(value: number | null): string {
  if (value === null) return 'not measured'
  return `${value >= 0 ? '+' : ''}${value.toFixed(3)}`
}

function proposalLabel(entry: AblationHistoryEntry): string {
  if (entry.proposal.startsWith('not_filed:')) {
    return `not filed (${entry.proposal.slice('not_filed:'.length)})`
  }
  if (entry.proposal) return entry.proposal
  return entry.verdict === 'remove' ? 'not filed' : '—'
}
