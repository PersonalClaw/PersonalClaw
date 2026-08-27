import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JudgeBenchPanel } from './JudgeBenchPanel'
import { ApiError, type JudgeBenchRow, type JudgeBenchView } from '../../lib/api'

/** ES-4's judge tier table, on the two things a table of measurements gets wrong.
 *
 *  1. An UNMEASURED metric must not render as a zero. `separation: null` is precisely why a
 *     row is inadequate; drawing "0.00" for it reads as a flawless score.
 *  2. A 404 is this panel's ORDINARY state — no benchmark has run — and must render as
 *     guidance, not as a failure or (worse) as nothing at all. */

function row(over: Partial<JudgeBenchRow> = {}): JudgeBenchRow {
  return {
    rubric_class: 'convergence',
    tier: 'fast',
    samples: 1,
    agreement: 1,
    scored_cells: 4,
    verifier_absent: 0,
    protocol_errors: 0,
    separation: 4,
    flip_rate: 0,
    swapped_fixtures: 2,
    false_passes: 0,
    false_rejects: 0,
    forbidden_missed: 0,
    cost_usd: 0.01,
    wall_secs: 1.2,
    calls: 4,
    adequate: true,
    inadequate_reasons: [],
    notes: [],
    ...over,
  }
}

function view(over: Partial<JudgeBenchView> = {}): JudgeBenchView {
  return {
    bench_id: 'judge-bench-20260816T000000Z',
    columns: [],
    rows: [row()],
    floors: { agreement: 0.9, separation: 1.5, flip_rate: 0.1 },
    recommendations: [{
      rubric_class: 'convergence',
      verdict: 'recommended',
      tier: 'fast',
      samples: 1,
      use_case: 'background',
      model_ref: 'Acme:small',
      cost_usd: 0.01,
      notes: ['cheapest adequate: fast at samples=1'],
    }],
    pin: null,
    runs: ['judge-bench-20260816T000000Z'],
    ...over,
  }
}

describe('the judge tier-recommendation table', () => {
  it('renders an unmeasured metric as unmeasured, never as a zero', () => {
    render(<JudgeBenchPanel
      bench={view({
        rows: [row({
          separation: null,
          flip_rate: null,
          adequate: false,
          inadequate_reasons: ['strong-vs-null separation was never measured (no paired fixture scored)'],
        })],
        recommendations: [],
      })}
      error={undefined}
      onRetry={() => {}}
    />)
    expect(screen.getAllByText('not measured').length).toBe(2)
    expect(screen.queryByText('0.00')).toBeNull()
    expect(screen.getByText(/never measured/)).toBeTruthy()
  })

  it('renders an unpriced row as unknown, so it cannot read as free', () => {
    render(<JudgeBenchPanel bench={view({ rows: [row({ cost_usd: null })], recommendations: [] })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('unknown')).toBeTruthy()
    expect(screen.queryByText('$0.0000')).toBeNull()
  })

  it('names the use case and the exact model ref to rebind', () => {
    render(<JudgeBenchPanel bench={view()} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('background')).toBeTruthy()
    expect(screen.getByText('Acme:small')).toBeTruthy()
    // The harness recommends; the panel points at the panel that binds.
    expect(screen.getByRole('link', { name: /Settings → Models/ })).toBeTruthy()
  })

  it('shows every inadequacy reason, not just the first', () => {
    render(<JudgeBenchPanel
      bench={view({
        rows: [row({ adequate: false, inadequate_reasons: ['reason one', 'reason two'] })],
        recommendations: [],
      })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('reason one')).toBeTruthy()
    expect(screen.getByText('reason two')).toBeTruthy()
  })

  it('renders "no benchmark yet" as guidance rather than as a load failure', () => {
    render(<JudgeBenchPanel bench={undefined} error={new ApiError('No judge benchmark has run yet. Run `personalclaw judge-bench` to produce one.', 404, 'judge_bench_absent')} onRetry={() => {}} />)
    expect(screen.getByText(/personalclaw judge-bench/)).toBeTruthy()
    expect(screen.queryByText(/Retry/)).toBeNull()
  })

  it('surfaces a REAL failure instead of swallowing it', () => {
    render(<JudgeBenchPanel bench={undefined} error={new Error('boom')} onRetry={() => {}} />)
    expect(screen.getByText(/judge benchmark/)).toBeTruthy()
    expect(screen.queryByText(/personalclaw judge-bench/)).toBeNull()
  })

  it('says the refusal out loud when no tier is adequate', () => {
    render(<JudgeBenchPanel
      bench={view({
        recommendations: [{
          rubric_class: 'convergence', verdict: 'no_adequate_tier', tier: '', samples: 0,
          use_case: '', model_ref: '', cost_usd: null,
          notes: ['fast/samples=1: agreement 0.50 < 0.9'],
        }],
      })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/no adequate tier/)).toBeTruthy()
    expect(screen.getByText(/agreement 0.50/)).toBeTruthy()
  })
})
