import { Activity, AlertTriangle } from 'lucide-react'
import { LoadError } from '../../ui/ListScaffold'
import { fvs } from '../../design/fontWeight'
import type {
  AttributionVerdict, HealthComponent, LearningHealth, MaeBucket,
} from '../../lib/api'

/** The flywheel observability panel (LEARN-R14b / WF2LEA-9 part 3).
 *
 *  Four things the plan names had a live writer and no reader: the 0-100 health composite with
 *  its 50-80% budget-utilization ideal band, R10d's judge-calibration MAE buckets, R16's
 *  attribution verdict history, and R19e's per-op LLM cost aggregates. A metric that is computed
 *  and never rendered is indistinguishable from one that is never computed.
 *
 *  **"Unmeasured" is rendered as unmeasured.** Every score here is `number | null`, and the panel
 *  says "not measured yet" rather than drawing a 0. The backend refuses to score silence for the
 *  same reason: a fresh install reading 0/100 tells the user their flywheel is broken when what
 *  it actually is, is new. */
export function HealthPanel({ health, error, onRetry }: {
  health: LearningHealth | undefined
  error: unknown
  onRetry: () => void
}) {
  // A failed fetch renders as an EMPTY STATE unless the error is read. The panel's whole
  // subject is "is this working?", so silently showing nothing is the worst possible answer.
  if (health === undefined && error) {
    return <LoadError what="flywheel health" error={error} onRetry={onRetry} />
  }
  if (!health) return null

  const { composite, utilization, judge, attribution, cost_by_op: costByOp, ablation } = health
  const [low, high] = composite.ideal_band

  return (
    <section className="flex flex-col gap-m" aria-labelledby="flywheel-health-heading">
      <div className="flex flex-wrap items-center gap-s">
        <Activity size={16} className="text-on-surface-var" />
        <span id="flywheel-health-heading" data-type="title-m" className="text-on-surface">
          Flywheel health, last {health.days} days
        </span>
        {composite.measured < composite.of && (
          <span
            className="inline-flex items-center gap-1.5 rounded-pill px-m h-6 text-[0.75rem]"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)', color: 'var(--color-warn)' }}
            title="Components with no data are excluded from the composite rather than scored as zero — an un-instrumented subsystem is not a broken one."
          >
            <AlertTriangle size={12} /> {composite.of - composite.measured} unmeasured
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-baseline gap-m rounded-lg bg-surface-container px-l py-l">
        <span data-type="display-s" className="text-on-surface" style={fvs(600)}>
          {composite.score === null ? '—' : composite.score}
        </span>
        <span className="text-on-surface-low text-[0.8125rem]">
          {composite.score === null
            ? 'not measured yet — nothing has run'
            : `of 100, from ${composite.measured} of ${composite.of} components`}
        </span>
      </div>

      <ul className="flex flex-col gap-s">
        {composite.components.map((component) => (
          <ComponentRow key={component.name} component={component} />
        ))}
      </ul>

      <p className="text-on-surface-low text-[0.75rem]">
        Context budget: {utilization.mean === null
          ? 'no ambient render recorded yet'
          : `${pct(utilization.mean)} used across ${utilization.samples} render${utilization.samples === 1 ? '' : 's'}`}
        {' · '}ideal band {pct(low)}–{pct(high)}
      </p>

      <MaePanel mae={judge.mae} runsScanned={judge.runs_scanned} verdicts={judge.verdicts} />

      <AttributionPanel attribution={attribution} />

      <CostPanel rows={costByOp} total={health.capture.cost_usd} />

      <AblationPanel ablation={ablation} />
    </section>
  )
}

/** A component's label. Closed `Record`, no default branch: a name the backend adds without a
 *  label here becomes a TYPE error rather than a row reading "undefined". */
const COMPONENT_LABEL: Record<HealthComponent['name'], string> = {
  precision: 'Surfacing precision',
  capture: 'Capture reliability',
  utilization: 'Budget utilization',
  judge: 'Judge trustworthiness',
}

function ComponentRow({ component }: { component: HealthComponent }) {
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-s rounded-md bg-surface-container px-l py-m">
      <span className="text-on-surface text-[0.8125rem]">
        {COMPONENT_LABEL[component.name]}
        <span className="text-on-surface-low"> · weight {pct(component.weight)}</span>
      </span>
      <span className="flex items-baseline gap-s">
        <span className="text-on-surface-var text-[0.75rem]">{component.detail}</span>
        <span data-type="title-s" className="text-on-surface" style={fvs(600)}>
          {component.score === null ? '—' : component.score}
        </span>
      </span>
    </li>
  )
}

/** R10d's MAE buckets. `labelled` is shown beside `n` on purpose: a bucket with verdicts but no
 *  human labels has no error to report, and printing 0.00 there would read as a perfect judge. */
function MaePanel({ mae, runsScanned, verdicts }: {
  mae: LearningHealth['judge']['mae']
  runsScanned: number
  verdicts: number
}) {
  return (
    <div className="flex flex-col gap-s">
      <span data-type="title-s" className="text-on-surface">Judge calibration</span>
      {mae.buckets.length === 0 || verdicts === 0 ? (
        <p className="text-on-surface-low text-[0.75rem]">
          No judge verdicts in the last {runsScanned} run{runsScanned === 1 ? '' : 's'} — nothing to calibrate yet.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-s">
            {mae.buckets.map((bucket) => <MaeBucketCell key={bucket.bucket} bucket={bucket} />)}
          </div>
          <p className="text-on-surface-low text-[0.75rem]">
            {mae.labelled} human-labelled of {verdicts} verdict{verdicts === 1 ? '' : 's'} across {runsScanned} run{runsScanned === 1 ? '' : 's'}
            {mae.unlabelled > 0 && ` · ${mae.unlabelled} awaiting a label`}
            {mae.no_confidence > 0 && ` · ${mae.no_confidence} recorded no samples`}
            . Error is only reported where a human overrode the judge — silence is not agreement.
          </p>
        </>
      )}
    </div>
  )
}

function MaeBucketCell({ bucket }: { bucket: MaeBucket }) {
  return (
    <div
      className="flex min-w-[92px] flex-1 flex-col items-center gap-1 rounded-lg bg-surface-container px-m py-m"
      title={`Predicted confidence ${bucket.bucket}: ${bucket.n} verdict${bucket.n === 1 ? '' : 's'}, ${bucket.labelled} human-labelled`}
    >
      <span className="text-on-surface-low text-[0.75rem]">{bucket.bucket}</span>
      <span data-type="title-s" className="text-on-surface" style={fvs(600)}>
        {bucket.mae === null ? '—' : bucket.mae.toFixed(2)}
      </span>
      <span className="text-on-surface-low text-[0.75rem]">
        {bucket.mae === null ? `${bucket.n} unlabelled` : `MAE · n=${bucket.labelled}`}
      </span>
    </div>
  )
}

/** Every verdict in LEARN-R16's closed five-way set, plus PENDING. Exhaustive by type. */
const VERDICT_LABEL: Record<AttributionVerdict, string> = {
  EFFECTIVE: 'Effective',
  PARTIALLY_EFFECTIVE: 'Partly effective',
  INEFFECTIVE: 'Ineffective',
  MIXED: 'Mixed',
  HARMFUL: 'Harmful',
  PENDING: 'Awaiting measurement',
}

const VERDICT_ORDER: AttributionVerdict[] = [
  'EFFECTIVE', 'PARTIALLY_EFFECTIVE', 'MIXED', 'INEFFECTIVE', 'HARMFUL', 'PENDING',
]

function AttributionPanel({ attribution }: { attribution: LearningHealth['attribution'] }) {
  const tally = new Map<AttributionVerdict, number>()
  for (const entry of attribution.history) {
    tally.set(entry.verdict, (tally.get(entry.verdict) ?? 0) + 1)
  }
  return (
    <div className="flex flex-col gap-s">
      <span data-type="title-s" className="text-on-surface">Accepted-change outcomes</span>
      {attribution.history.length === 0 ? (
        <p className="text-on-surface-low text-[0.75rem]">
          No accepted change has been graded yet. A verdict lands once enough runs have gone
          past the change to measure it.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-s">
            {VERDICT_ORDER.filter((verdict) => tally.has(verdict)).map((verdict) => (
              <span
                key={verdict}
                className="rounded-pill bg-surface-high px-m h-6 inline-flex items-center text-on-surface-var text-[0.75rem]"
              >
                {VERDICT_LABEL[verdict]} · {tally.get(verdict)}
              </span>
            ))}
          </div>
          {attribution.proposers.length > 0 && (
            <ul className="flex flex-col gap-1">
              {attribution.proposers.map((proposer) => (
                <li key={proposer.source} className="text-on-surface-var text-[0.75rem]">
                  {proposer.source}: {proposer.decided} decided of {proposer.total}
                  {' · '}{pct(proposer.effective_rate)} effective
                  {' · '}{pct(proposer.harm_rate)} harmful
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

/** R19e. A single total answers "was it expensive"; only the per-op split answers "at what". */
function CostPanel({ rows, total }: { rows: LearningHealth['cost_by_op']; total: number }) {
  return (
    <div className="flex flex-col gap-s">
      <span data-type="title-s" className="text-on-surface">LLM cost by operation</span>
      {rows.length === 0 ? (
        <p className="text-on-surface-low text-[0.75rem]">No metered operation in this window.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rows.map((row) => (
            <li key={row.op} className="text-on-surface-var text-[0.75rem]">
              {row.op}: ${row.cost_usd.toFixed(4)} over {row.passes} pass{row.passes === 1 ? '' : 'es'}
              {row.cost_usd === 0 && ' — unpriced or free'}
            </li>
          ))}
          <li className="text-on-surface-low text-[0.75rem]">Total ${total.toFixed(4)}</li>
        </ul>
      )}
    </div>
  )
}

const ABLATION_VERDICT_LABEL: Record<'no_effect' | 'earns_its_place', string> = {
  no_effect: 'no measurable effect — a candidate for removal',
  earns_its_place: 'changes what gets injected',
}

/** §2.5's ablation-delta rule: a heuristic measured at ~0 should be removed, and reporting the
 *  null result is the feature. */
function AblationPanel({ ablation }: { ablation: LearningHealth['ablation'] }) {
  const rows = ablation.rows ?? []
  return (
    <div className="flex flex-col gap-s">
      <span data-type="title-s" className="text-on-surface">Surfacing heuristics</span>
      {rows.length === 0 ? (
        <p className="text-on-surface-low text-[0.75rem]">
          No ablation sweep has run yet. One runs daily alongside an ambient render.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rows.map((row) => (
            <li key={row.heuristic} className="text-on-surface-var text-[0.75rem]">
              {row.heuristic}: delta {row.delta.toFixed(3)}
              {' — '}{ABLATION_VERDICT_LABEL[row.verdict as 'no_effect' | 'earns_its_place']}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function pct(value: number): string {
  return `${Math.round(value * 100)}%`
}
