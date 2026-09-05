import { ArrowLeftRight, TrendingDown, TrendingUp } from 'lucide-react'
import { LoadError } from '../../ui/ListScaffold'
import { StatusPill } from '../../ui/StatusPill'
import { Table, THead, Th, Td } from '../../ui/Table'
import { api, hasApiCode, type FieldMetricsRow } from '../../lib/api'
import { EvalsOff } from './EvalsOff'

/** The lab-vs-field table (EVALUATION-SUBSTRATE amendment E3 / ES-9).
 *
 *  One row per subject answers "lab says better — is it?": lab score (Loop 1, pinned) |
 *  gate status (Loop 2) | field trend (Loop 3, the user's own 👍/👎, edit-before-approve
 *  and approval/undo record). A subject whose lab score rose while its field trend fell
 *  carries the `lab_field_divergence` flag — the one warn-toned verdict here, because it
 *  is the verdict the gateway sweep files a §4.2 trust-record demotion on.
 *
 *  Three rendering rules, inherited from the panels beside this one:
 *
 *  1. **`null` is "not measured", never 0.** A subject nobody has thumbed has no thumb
 *     rate; an action type has no edit-before-approve record anywhere, so that cell is
 *     an em dash for every such row rather than a reassuring 0%.
 *  2. **An unmeasured trend is "too few signals", never "flat".** Flat is a verdict; a
 *     thin sample cannot support one.
 *  3. **Nothing is re-decided here.** The divergence verdict arrives computed — it is
 *     what the sweep demoted on, and a UI re-deriving it from the visible numbers would
 *     eventually disagree with what actually happened to the subject's autonomy. */
export function FieldMetricsPanel({ rows, error, onRetry }: {
  rows: FieldMetricsRow[] | undefined
  error: unknown
  onRetry: () => void
}) {
  if (rows === undefined && error) {
    if (hasApiCode(error, 'evals_disabled')) {
      return (
        <section className="flex flex-col gap-s" aria-labelledby="field-metrics-heading">
          <Heading />
          <EvalsOff what="lab-vs-field table" />
        </section>
      )
    }
    return <LoadError what="lab vs field" error={error} onRetry={onRetry} />
  }
  if (!rows) return null

  return (
    <section className="flex flex-col gap-m" aria-labelledby="field-metrics-heading">
      <Heading count={rows.filter((r) => r.lab_field_divergence).length} />
      {rows.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">
          No subjects yet. Once templates run or action types collect verdicts, each gets
          one row here: its lab score, its gate status, and what the field — your own
          thumbs, edits and approvals — says about it.
        </p>
      ) : (
        <>
          <p data-type="caption" className="text-on-surface-low">
            Lab is the newest pinned study result; gate is the newest Loop-2 report; field
            is derived from your 👍/👎, edit-before-approve and approval/undo records —
            computed per request, stored nowhere. A lab rise over a falling field trend is
            flagged and mechanically files an autonomy demotion.
          </p>
          <Table
            caption="Lab results beside live field metrics, one row per subject"
            wrapClassName="rounded-lg bg-surface-container"
          >
            <THead>
              <tr>
                <Th>Subject</Th>
                <Th align="right">Lab</Th>
                <Th>Gate</Th>
                <Th align="right">👍/👎</Th>
                <Th align="right">Edited before approve</Th>
                <Th align="right">Approvals</Th>
                <Th>Field trend</Th>
              </tr>
            </THead>
            <tbody>
              {rows.map((row) => <Row key={`${row.subject_kind}:${row.subject}`} row={row} />)}
            </tbody>
          </Table>
        </>
      )}
    </section>
  )
}

/** The fetch + panel pair the Learning page mounts (its key has one reader, so a refetch
 *  IS the invalidation — the `ABLATION_KEY` reasoning). Split so tests can drive the
 *  presentational half without a network. */
export const FIELD_METRICS_KEY = 'learning:field-metrics'
export const fetchFieldMetrics = () => api.evalFieldMetrics()

function Heading({ count = 0 }: { count?: number }) {
  return (
    <div className="flex flex-wrap items-center gap-s">
      <ArrowLeftRight size={16} className="text-on-surface-var" />
      <span id="field-metrics-heading" data-type="title-m" className="text-on-surface">
        Lab vs field
      </span>
      {count > 0 && (
        <StatusPill tone="warn" className="gap-1.5 h-6 px-m w-fit">
          <TrendingDown size={12} /> {count} diverged
        </StatusPill>
      )}
    </div>
  )
}

function Row({ row }: { row: FieldMetricsRow }) {
  return (
    <tr className="border-t border-outline-variant/30 align-top">
      <Td className="text-on-surface">
        <div className="flex flex-wrap items-center gap-s">
          <span className="break-all">{row.subject}</span>
          <span data-type="caption" className="rounded-pill bg-surface-high px-m h-5 inline-flex items-center text-on-surface-low">
            {row.subject_kind === 'template' ? 'template' : 'action type'}
          </span>
          {row.lab_field_divergence && (
            <StatusPill tone="warn" className="gap-1 h-5 px-m w-fit" title={row.divergence_reason}>
              lab_field_divergence
            </StatusPill>
          )}
        </div>
      </Td>
      <Td align="right" className="text-on-surface-var">
        <LabCell lab={row.lab} />
      </Td>
      <Td className="text-on-surface-var">
        <GateCell gate={row.gate} />
      </Td>
      <Td align="right" className="text-on-surface-var">
        {row.field.thumb_rate === null
          ? '—'
          : `${pct(row.field.thumb_rate)} (${row.field.ups}/${row.field.ups + row.field.downs})`}
      </Td>
      <Td align="right" className="text-on-surface-var">
        {row.field.edit_before_approve_rate === null
          ? '—'
          : `${pct(row.field.edit_before_approve_rate)} (${row.field.edited_runs}/${row.field.edited_runs + row.field.clean_approved_runs})`}
      </Td>
      <Td align="right" className="text-on-surface-var">
        {row.field.approval_rate === null
          ? '—'
          : `${pct(row.field.approval_rate)}${row.field.undos ? ` · ${row.field.undos} undone` : ''}`}
      </Td>
      <Td><TrendChip trend={row.field.trend} /></Td>
    </tr>
  )
}

/** The Loop-1 cell: the newest pinned score, with the direction the divergence reads.
 *  An absent or unmeasured score is an absence — a `0.000` here would count as a fall. */
function LabCell({ lab }: { lab: FieldMetricsRow['lab'] }) {
  if (!lab || lab.score === null) {
    return <span className="text-on-surface-low">not measured</span>
  }
  return (
    <span title={lab.model_fp ? `pinned ${lab.model_fp}` : undefined}>
      {lab.score.toFixed(3)}
      {lab.rose === true && <TrendingUp size={12} className="ml-1 inline" aria-label="rose" />}
      {lab.rose === false && <TrendingDown size={12} className="ml-1 inline" aria-label="fell" />}
    </span>
  )
}

/** The Loop-2 cell, from the same summary the proposal card renders. Absence says so. */
function GateCell({ gate }: { gate: FieldMetricsRow['gate'] }) {
  if (!gate) return <span className="text-on-surface-low">no gate run</span>
  if (gate.state !== 'gated') return <span className="text-on-surface-low">ungated</span>
  const delta = gate.delta
  return (
    <span>
      gated{delta === null ? '' : ` (${delta >= 0 ? '+' : ''}${delta.toFixed(3)})`}
    </span>
  )
}

/** Falling quality is the warning here — the inverse of the Attention panel, whose
 *  warn-toned verdict is RISING attention. Same rule about thin samples though: an
 *  empty verdict is unmeasured and must never dress up as flat. */
function TrendChip({ trend }: { trend: FieldMetricsRow['field']['trend'] }) {
  if (!trend) {
    return <span className="text-on-surface-low">too few signals</span>
  }
  if (trend === 'falling') {
    return (
      <StatusPill tone="warn" className="gap-1.5 h-6 px-m w-fit">
        <TrendingDown size={12} /> falling
      </StatusPill>
    )
  }
  if (trend === 'rising') {
    return (
      <span className="inline-flex w-fit items-center gap-1.5 text-on-surface-var">
        <TrendingUp size={12} /> rising
      </span>
    )
  }
  return <span className="text-on-surface-var">flat</span>
}

function pct(rate: number): string {
  return `${Math.round(rate * 100)}%`
}
