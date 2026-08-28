import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { HealthPanel } from './HealthPanel'
import type { LearningHealth } from '../../lib/api'

// ── The flywheel observability panel (LEARN-R14b / WF2LEA-9 part 3) ───────────────────
//
// Four metrics that already had a live writer and no reader. These tests assert each one
// reaches the DOM, and — the load-bearing half — that ABSENCE reaches the DOM as absence.
//
// Every score on this panel is `number | null`. A fresh install has no allocation samples,
// no metered flush, no judge verdict and no graded change, so a panel that rendered null as
// 0 would tell a new user their flywheel scores 0/100 — a broken reading of a working
// system, whose only apparent remedy is to generate traffic.

function health(overrides: Partial<LearningHealth> = {}): LearningHealth {
  return {
    days: 7,
    composite: {
      score: null,
      components: [
        { name: 'precision', score: null, weight: 0.4, detail: 'unmeasured — nothing surfaced yet' },
        { name: 'capture', score: null, weight: 0.3, detail: 'unmeasured — no capture pass has run' },
        { name: 'utilization', score: null, weight: 0.2, detail: 'unmeasured — no ambient render recorded' },
        { name: 'judge', score: null, weight: 0.1, detail: 'unmeasured — no judge verdicts with human labels' },
      ],
      measured: 0,
      of: 4,
      ideal_band: [0.5, 0.8],
    },
    utilization: { samples: 0, mean: null, ideal_band: [0.5, 0.8] },
    capture: { days: 7, passes: 0, errors: 0, cost_usd: 0, all_ok_streak: 0 },
    surfacing: { surfaced: 0, used: 0, precision: null },
    cost_by_op: [],
    judge: {
      runs_scanned: 0, verdicts: 0, divergences: 0, false_pass_rate: null,
      nodding_gates: [],
      mae: { buckets: [], labelled: 0, unlabelled: 0, no_confidence: 0 },
    },
    attribution: { proposers: [], history: [] },
    ablation: {},
    ...overrides,
  }
}

describe('a failed load is not an empty flywheel', () => {
  it('reads `error` and offers a retry instead of rendering nothing', () => {
    const onRetry = vi.fn()
    render(<HealthPanel health={undefined} error={new Error('gateway timed out')} onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByText(/gateway timed out/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalled()
  })
})

describe('unmeasured renders as unmeasured', () => {
  it('shows an em dash and says nothing has run, never 0', () => {
    render(<HealthPanel health={health()} error={null} onRetry={() => {}} />)
    expect(screen.getByText(/not measured yet — nothing has run/)).toBeTruthy()
    expect(screen.queryByText('0')).toBeNull()
  })

  it('counts the unmeasured components so exclusion is visible', () => {
    render(<HealthPanel health={health()} error={null} onRetry={() => {}} />)
    expect(screen.getByText(/4 unmeasured/)).toBeTruthy()
  })

  it('states the ideal band even with no samples', () => {
    render(<HealthPanel health={health()} error={null} onRetry={() => {}} />)
    expect(screen.getByText(/no ambient render recorded yet/)).toBeTruthy()
    expect(screen.getByText(/ideal band 50%–80%/)).toBeTruthy()
  })
})

describe('the four metrics reach the DOM', () => {
  const measured = health({
    composite: {
      score: 82.5,
      components: [
        { name: 'precision', score: 60, weight: 0.4, detail: '60% of surfacings were used' },
        { name: 'capture', score: 90, weight: 0.3, detail: '9 of 10 passes clean' },
        { name: 'utilization', score: 100, weight: 0.2, detail: '65% of the context budget used (ideal 50%-80%)' },
        { name: 'judge', score: 90, weight: 0.1, detail: '10% of judged work was wrongly passed' },
      ],
      measured: 4,
      of: 4,
      ideal_band: [0.5, 0.8],
    },
    utilization: { samples: 12, mean: 0.65, ideal_band: [0.5, 0.8] },
    cost_by_op: [
      { op: 'session_end', passes: 4, cost_usd: 0.1234 },
      { op: 'run_end', passes: 2, cost_usd: 0 },
    ],
    capture: { days: 7, passes: 10, errors: 1, cost_usd: 0.1234, all_ok_streak: 3 },
    judge: {
      runs_scanned: 12, verdicts: 5, divergences: 1, false_pass_rate: 0.1,
      nodding_gates: [],
      mae: {
        buckets: [
          { bucket: '0.00-0.25', n: 0, labelled: 0, mae: null },
          { bucket: '0.25-0.50', n: 1, labelled: 0, mae: null },
          { bucket: '0.50-0.75', n: 2, labelled: 1, mae: 0.33 },
          { bucket: '0.75-1.00', n: 2, labelled: 0, mae: null },
        ],
        labelled: 1, unlabelled: 4, no_confidence: 0,
      },
    },
    attribution: {
      proposers: [{ source: 'refiner', counts: { EFFECTIVE: 2, HARMFUL: 1 }, total: 4, decided: 3, harm_rate: 0.3333, effective_rate: 0.6667 }],
      history: [
        { source: 'refiner', verdict: 'EFFECTIVE' },
        { source: 'refiner', verdict: 'EFFECTIVE' },
        { source: 'refiner', verdict: 'HARMFUL' },
      ],
    },
    ablation: {
      at: '2026-08-11T00:00:00+00:00',
      rows: [
        { heuristic: 'rank_decay', delta: 0, verdict: 'no_effect', items: 6 },
        { heuristic: 'diversification', delta: 0.5, verdict: 'earns_its_place', items: 6 },
      ],
    },
  })

  it('renders the 0-100 composite with its ideal band', () => {
    render(<HealthPanel health={measured} error={null} onRetry={() => {}} />)
    expect(screen.getByText('82.5')).toBeTruthy()
    expect(screen.getByText(/of 100, from 4 of 4 components/)).toBeTruthy()
    // 🔁 Was pinned as `12 render(s)`. The parenthetical is gone surface-wide; what this rail
    // cares about is that the mean and its sample size both reach the DOM, so it now asserts the
    // AGREEMENT (12 → plural) rather than one spelling of it.
    expect(screen.getByText(/65% used across 12 renders\b/)).toBeTruthy()
    expect(screen.queryByText(/render\(s\)/), 'the parenthetical form is retired here').toBeNull()
  })

  it('renders every component under its label', () => {
    render(<HealthPanel health={measured} error={null} onRetry={() => {}} />)
    for (const label of ['Surfacing precision', 'Capture reliability', 'Budget utilization', 'Judge trustworthiness']) {
      expect(screen.getByText(new RegExp(label))).toBeTruthy()
    }
  })

  it('renders the MAE buckets, and marks an unlabelled bucket as unlabelled', () => {
    render(<HealthPanel health={measured} error={null} onRetry={() => {}} />)
    expect(screen.getByText('0.33')).toBeTruthy()
    expect(screen.getByText('MAE · n=1')).toBeTruthy()
    // A bucket with verdicts but no human label must NOT read as a perfect 0.00.
    expect(screen.getByText('2 unlabelled')).toBeTruthy()
    expect(screen.getByText(/silence is not agreement/)).toBeTruthy()
  })

  it('renders the attribution verdict history', () => {
    render(<HealthPanel health={measured} error={null} onRetry={() => {}} />)
    expect(screen.getByText(/Effective · 2/)).toBeTruthy()
    expect(screen.getByText(/Harmful · 1/)).toBeTruthy()
    expect(screen.getByText(/refiner: 3 decided of 4/)).toBeTruthy()
  })

  it('renders per-op cost aggregates, and says when an op is unpriced', () => {
    render(<HealthPanel health={measured} error={null} onRetry={() => {}} />)
    // 🔁 Was `4 pass(es)`. 4 is plural, so the sentence reads "4 passes".
    expect(screen.getByText(/session_end: \$0\.1234 over 4 passes\b/)).toBeTruthy()
    expect(screen.getByText(/run_end: \$0\.0000 over 2 passes — unpriced or free/)).toBeTruthy()
  })

  it('renders the ablation sweep, naming a heuristic that earns nothing', () => {
    render(<HealthPanel health={measured} error={null} onRetry={() => {}} />)
    expect(screen.getByText(/rank_decay: delta 0\.000 — no measurable effect/)).toBeTruthy()
    expect(screen.getByText(/diversification: delta 0\.500 — changes what gets injected/)).toBeTruthy()
  })
})
