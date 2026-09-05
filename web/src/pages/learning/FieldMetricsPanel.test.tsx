import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { FieldMetricsPanel } from './FieldMetricsPanel'
import type { FieldMetricsRow } from '../../lib/api'

/** ES-9's lab-vs-field table, on the claims a summary of absences gets wrong.
 *
 *  1. `null` rates are UNMEASURED — a subject nobody thumbed must not render 0%, and an
 *     action type's edit-before-approve cell (no record source exists) must be a dash.
 *  2. An unmeasured field trend ('') is "too few signals", never "flat".
 *  3. `lab_field_divergence` arrives decided and gets the warn treatment — it is the one
 *     verdict here a demotion was filed on.
 *  4. An absent lab score / gate run renders as an absence, not as a zero. */

function row(over: Partial<FieldMetricsRow> = {}): FieldMetricsRow {
  return {
    subject_kind: 'template',
    subject: 'weekly-report',
    lab: {
      score: 0.7,
      previous: 0.4,
      rose: true,
      verdict: 'win',
      study_id: 'st-1',
      model_fp: 'abc123',
      ts: 1000,
    },
    gate: null,
    field: {
      ups: 1,
      downs: 3,
      thumb_rate: 0.25,
      edited_runs: 1,
      clean_approved_runs: 1,
      edit_before_approve_rate: 0.5,
      approvals: 0,
      rejections: 0,
      undos: 0,
      approval_rate: null,
      signals: 6,
      trend: 'falling',
    },
    lab_field_divergence: false,
    divergence_reason: '',
    ...over,
  }
}

describe('the lab-vs-field table', () => {
  it('renders one row per subject with lab, thumbs, edits and the trend', () => {
    render(<FieldMetricsPanel rows={[row()]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('weekly-report')).toBeTruthy()
    expect(screen.getByText('0.700')).toBeTruthy()
    expect(screen.getByText('25% (1/4)')).toBeTruthy()
    expect(screen.getByText('50% (1/2)')).toBeTruthy()
    expect(screen.getByText('falling')).toBeTruthy()
  })

  it('renders an unmeasured rate as a dash, never as 0%', () => {
    const r = row({
      subject_kind: 'action_type',
      subject: 'inbox.reply_draft',
      field: { ...row().field, thumb_rate: null, ups: 0, downs: 0, edit_before_approve_rate: null },
    })
    render(<FieldMetricsPanel rows={[r]} error={undefined} onRetry={() => {}} />)
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText(/0% \(0\/0\)/)).toBeNull()
  })

  it('renders an unmeasured trend as "too few signals", never as flat', () => {
    render(
      <FieldMetricsPanel
        rows={[row({ field: { ...row().field, trend: '', signals: 2 } })]}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('too few signals')).toBeTruthy()
    expect(screen.queryByText('flat')).toBeNull()
  })

  it('marks a divergent subject with the warn pill the demotion was filed on', () => {
    render(
      <FieldMetricsPanel
        rows={[row({ lab_field_divergence: true, divergence_reason: 'lab score rose while…' })]}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    const pill = screen.getByText('lab_field_divergence')
    expect(pill.closest('span')?.getAttribute('style')).toContain('--color-warn')
    expect(screen.getByText('1 diverged')).toBeTruthy()
  })

  it('renders a missing lab score and a missing gate run as absences, not zeros', () => {
    render(
      <FieldMetricsPanel rows={[row({ lab: null, gate: null })]} error={undefined} onRetry={() => {}} />,
    )
    expect(screen.getByText('not measured')).toBeTruthy()
    expect(screen.getByText('no gate run')).toBeTruthy()
    expect(screen.queryByText('0.000')).toBeNull()
  })

  it('says something actionable when there are no subjects at all', () => {
    render(<FieldMetricsPanel rows={[]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/No subjects yet/)).toBeTruthy()
  })
})
