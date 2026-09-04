import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AttentionPanel } from './AttentionPanel'
import type { AttentionScope } from '../../lib/api'

/** ES-16's attention table, on the claims a summary of absences gets wrong.
 *
 *  1. An UNMEASURED trend ('' — too few runs) must not render as "flat": flat is a
 *     verdict, and the sample cannot support one.
 *  2. A zero dwell means no human gate carried a stamp — rendering "0.0s" would claim
 *     gates resolve instantly, which is the opposite of "nothing was measured".
 *  3. Rising is the demotion signal and must be visually distinct, not just text. */

function scope(over: Partial<AttentionScope> = {}): AttentionScope {
  return {
    scope: 'weekly-report',
    runs: 8,
    attention_events: 4,
    events_per_run: 0.5,
    dwell_p50_secs: 12.5,
    debt: 1.75,
    trend: 'falling',
    ...over,
  }
}

describe('the attention accounting table', () => {
  it('renders per-scope events, dwell, debt, and the trend', () => {
    render(<AttentionPanel scopes={[scope()]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('weekly-report')).toBeTruthy()
    expect(screen.getByText('0.50')).toBeTruthy()
    expect(screen.getByText('12.5s')).toBeTruthy()
    expect(screen.getByText('1.75')).toBeTruthy()
    expect(screen.getByText('falling')).toBeTruthy()
  })

  it('renders an unmeasured trend as "too few runs", never as flat', () => {
    render(<AttentionPanel scopes={[scope({ trend: '' })]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('too few runs')).toBeTruthy()
    expect(screen.queryByText('flat')).toBeNull()
  })

  it('renders a missing dwell as an absence, not as an instant gate', () => {
    render(<AttentionPanel scopes={[scope({ dwell_p50_secs: 0 })]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('—')).toBeTruthy()
    expect(screen.queryByText('0.0s')).toBeNull()
  })

  it('marks a rising trend as the warning it is', () => {
    render(<AttentionPanel scopes={[scope({ trend: 'rising' })]} error={undefined} onRetry={() => {}} />)
    const chip = screen.getByText('rising')
    // The warn treatment rides the chip's own element, not the table cell.
    expect(chip.closest('span')?.getAttribute('style')).toContain('--color-warn')
  })

  it('renders long dwells in minutes so a stuck gate reads at a glance', () => {
    render(<AttentionPanel scopes={[scope({ dwell_p50_secs: 300 })]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('5.0m')).toBeTruthy()
  })

  it('renders "no runs yet" as guidance rather than an empty table', () => {
    render(<AttentionPanel scopes={[]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/No workflow runs recorded yet/)).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('surfaces a REAL failure instead of swallowing it', () => {
    render(<AttentionPanel scopes={undefined} error={new Error('boom')} onRetry={() => {}} />)
    expect(screen.getByText(/attention accounting/)).toBeTruthy()
    expect(screen.queryByText(/No workflow runs recorded yet/)).toBeNull()
  })
})
