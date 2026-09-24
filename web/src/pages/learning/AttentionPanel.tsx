import { Eye, TrendingDown, TrendingUp } from 'lucide-react'
import { LoadError } from '../../ui/ListScaffold'
import { StatusPill } from '../../ui/StatusPill'
import { Table, THead, Th, Td } from '../../ui/Table'
import type { AttentionScope } from '../../lib/api'

/** The §4.4 human-attention table (EVALUATION-SUBSTRATE, ES-16).
 *
 *  Autonomy's honest objective is attention saved without outcome regression, so this
 *  panel publishes what each workflow template still costs the user: attention events
 *  per run (human gate answers, mid-flight edits, judge divergences — auto-approved
 *  gates deliberately count for nothing), how long a gate holds attention (p50 dwell),
 *  the decayed pending-attention debt, and the trend the promotion proposal cites.
 *
 *  **Nothing is re-decided here.** The trend verdict arrives computed; a UI that
 *  re-derived it from the visible numbers would eventually disagree with the note the
 *  proposal carried, and the copy shipping the permissive answer would be this one.
 *
 *  **A rising trend is the warning.** Falling attention is a template earning trust; a
 *  RISING trend after a grant is the §4.4 demotion signal, so it is the one verdict
 *  rendered in the warn tone. */
export function AttentionPanel({ scopes, error, onRetry }: {
  scopes: AttentionScope[] | undefined
  error: unknown
  onRetry: () => void
}) {
  if (scopes === undefined && error) {
    return <LoadError what="attention accounting" error={error} onRetry={onRetry} />
  }
  if (!scopes) return null

  return (
    <section className="flex flex-col gap-m" aria-labelledby="attention-heading">
      <Heading />

      {scopes.length === 0 ? (
        <p className="text-on-surface-low text-[0.8125rem]">
          No workflow runs recorded yet. Once workflows run, each template's attention
          cost — gate answers, mid-flight edits, judge overrides — is tallied here, per
          run, with a trend the graduation proposal cites.
        </p>
      ) : (
        <>
          <p className="text-on-surface-low text-[0.75rem]">
            Attention events are human gate answers, mid-flight edits, and judge
            divergences — an auto-approved gate counts for nothing. Debt decays on a
            7-day half-life, so last night's intervention weighs on today's graduation
            question and last month's does not.
          </p>
          <Table
            caption="Human-attention accounting per workflow template"
            wrapClassName="rounded-lg bg-surface-container"
          >
            <THead>
              <tr>
                <Th>Template</Th>
                <Th align="right">Runs</Th>
                <Th align="right">Events/run</Th>
                <Th align="right">p50 dwell</Th>
                <Th align="right">Debt</Th>
                <Th>Trend</Th>
              </tr>
            </THead>
            <tbody>
              {scopes.map((row) => <Row key={row.scope} row={row} />)}
            </tbody>
          </Table>
        </>
      )}
    </section>
  )
}

function Heading() {
  return (
    <div className="flex flex-wrap items-center gap-s">
      <Eye size={16} className="text-on-surface-var" />
      <span id="attention-heading" data-type="title-m" className="text-on-surface">
        Attention
      </span>
    </div>
  )
}

function Row({ row }: { row: AttentionScope }) {
  return (
    <tr className="border-t border-outline-variant/30 align-top">
      <Td className="text-on-surface">{row.scope}</Td>
      <Td align="right" className="text-on-surface-var">{row.runs}</Td>
      <Td align="right" className="text-on-surface-var">{row.events_per_run.toFixed(2)}</Td>
      <Td align="right" className="text-on-surface-var">{fmtDwell(row.dwell_p50_secs)}</Td>
      <Td align="right" className="text-on-surface-var">{row.debt.toFixed(2)}</Td>
      <Td><TrendChip trend={row.trend} /></Td>
    </tr>
  )
}

/** The trend, in the direction's own tone: rising attention is the demotion signal, so
 *  it alone gets the warn treatment. An empty verdict is UNMEASURED — "too few runs" must
 *  never dress up as "flat", which is a claim the sample cannot support. */
function TrendChip({ trend }: { trend: AttentionScope['trend'] }) {
  if (!trend) {
    return <span className="text-on-surface-low">too few runs</span>
  }
  if (trend === 'rising') {
    return (
      <StatusPill tone="warn" className="gap-1.5 h-6 px-m w-fit">
        <TrendingUp size={12} /> rising
      </StatusPill>
    )
  }
  if (trend === 'falling') {
    return (
      <span className="inline-flex w-fit items-center gap-1.5 text-on-surface-var">
        <TrendingDown size={12} /> falling
      </span>
    )
  }
  return <span className="text-on-surface-var">flat</span>
}

/** Dwell is 0 when no human gate carried a stamp — render the absence, not a zero that
 *  would read as "gates resolve instantly". */
function fmtDwell(secs: number): string {
  if (!secs) return '—'
  if (secs >= 60) return `${(secs / 60).toFixed(1)}m`
  return `${secs.toFixed(1)}s`
}
