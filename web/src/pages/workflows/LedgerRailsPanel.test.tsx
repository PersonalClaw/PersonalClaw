import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowLedgerRails } from '../../lib/api'
import { EM_DASH, LedgerRailsPanel } from './LedgerRailsPanel'

// ── PP-16 seam 4, the ledger-rails third: the run side renders both rails, and ABSENT IS NOT ZERO ──
//
// The backend rail (`tests/test_pp16_ledger_rails.py`) proves the projection reports `null` for a
// cell whose ledger row carried no key. That guarantee is worth nothing if the panel then prints
// `null` as `0` — a `toFixed()` on a nullish value, or a `?? 0` — so this file pins the RENDERING.
//
// The trap is specific and this project has hit it six times: a loop-shaped `step_completed` carries
// no cost or token key at all (loop money lives in `usage/turns.jsonl`), and PP-16 retires the loop
// noun onto the run noun, so those rows will flow through this panel. A `$0.0000` in the cost column
// would tell a user their work was free.

let rails: (id: string) => Promise<WorkflowLedgerRails>

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: { ...actual.api, workflowRunLedgerRails: (id: string) => rails(id) },
  }
})

/** A RUN-shaped finding row: the engine's emitter writes every field. */
function runFinding(over: Record<string, unknown> = {}) {
  return {
    ts: '2026-09-06T02:00:10Z', node_id: 'draft', instance_path: 'main.draft', epoch: 0,
    state: 'done', model: 'claude-sonnet', provider: 'anthropic', tokens: 900,
    cost_usd: 0.0234, duration_secs: 8, retries: 0, degraded_reason: '',
    output_ref: 'outputs/draft.json', cycle: null, ...over,
  } as WorkflowLedgerRails['findings'][number]
}

/** A LOOP-shaped finding row: the cost and token keys are ABSENT, not zero. */
function loopFinding() {
  return runFinding({
    node_id: 'cycle', instance_path: null, epoch: null, state: null, model: null,
    provider: null, tokens: null, cost_usd: null, duration_secs: null, retries: null,
    degraded_reason: null, output_ref: null, cycle: 3,
  })
}

function payload(over: Partial<WorkflowLedgerRails> = {}): WorkflowLedgerRails {
  return {
    run_id: 'r1',
    workflow: 'weekly-report',
    findings: [runFinding()],
    verdicts: [{
      ts: '2026-09-06T02:01:00Z', node_id: 'gate', instance_path: 'main.gate', epoch: 0,
      template: 'weekly-report', verdict: 'PASS', status: 'kept', overall: 4.25,
      sample_count: 3, shortfalls: null, marginal_value: null, quality_score: null,
    }],
    totals: {
      steps_completed: 1, verdicts: 1, cost_usd: 0.0234, cost_recorded: true,
      tokens: 900, tokens_recorded: true, duration_secs: 8,
      verdicts_by_word: { PASS: 1 }, overall_series: [4.25],
      absent_scores: ['marginal_value', 'quality_score'],
    } as WorkflowLedgerRails['totals'],
    coverage: [
      { kind: 'breaker_trip', producer: 'none', events: null },
      { kind: 'judge_verdict', producer: 'engine', events: 1 },
      { kind: 'step_completed', producer: 'engine', events: 1 },
      { kind: 'watcher_reaped', producer: 'engine', events: 0 },
    ],
    ...over,
  }
}

describe('the run-side ledger rails panel', () => {
  it('renders both rails from one read', async () => {
    rails = async () => payload()
    render(<LedgerRailsPanel runId="r1" />)
    // The findings rail paints first: its node, and the output ref the introspection timeline drops.
    await waitFor(() => expect(screen.getByText('Findings rail')).toBeTruthy())
    expect(screen.getByText(/outputs\/draft\.json/)).toBeTruthy()
    expect(screen.getByText(/claude-sonnet/)).toBeTruthy()
    // And the verdict rail is one toggle away, carrying the judge's own word.
    fireEvent.click(screen.getByRole('radio', { name: /Verdict \/ ROI/ }))
    await waitFor(() => expect(screen.getByText('PASS')).toBeTruthy())
  })

  it('renders an ABSENT cost as an em dash, never as a zero', async () => {
    // The retirement-critical case: a loop-shaped row carries no cost or token key.
    rails = async () => payload({
      findings: [loopFinding()],
      totals: {
        steps_completed: 1, verdicts: 0, cost_usd: null, cost_recorded: false,
        tokens: null, tokens_recorded: false, duration_secs: null,
        verdicts_by_word: {}, overall_series: null,
        absent_scores: ['marginal_value', 'quality_score'],
      } as WorkflowLedgerRails['totals'],
    })
    const { container } = render(<LedgerRailsPanel runId="r1" />)
    await waitFor(() => expect(screen.getByText('Cost (est.)')).toBeTruthy())
    const body = container.textContent ?? ''
    expect(body).toContain(EM_DASH)
    // The specific lie this rail exists to prevent, in every shape a formatter could produce.
    expect(body).not.toContain('$0.0000')
    expect(body).not.toContain('~$0.00')
    // The step COUNT is still real — a step did complete, and that fact needs no cost key.
    expect(screen.getByText('Steps completed')).toBeTruthy()
    expect(screen.getByText('1')).toBeTruthy()
  })

  it('renders a MEASURED zero as a zero, not as an em dash', async () => {
    // The mirror. A run on a free local model carries `cost_usd: 0.0` — a real observation, and
    // reporting it as unknown would be the same failure inverted.
    rails = async () => payload({
      findings: [runFinding({ cost_usd: 0, tokens: 0 })],
      totals: {
        steps_completed: 1, verdicts: 0, cost_usd: 0, cost_recorded: true,
        tokens: 0, tokens_recorded: true, duration_secs: 0,
        verdicts_by_word: {}, overall_series: null,
        absent_scores: ['marginal_value', 'quality_score'],
      } as WorkflowLedgerRails['totals'],
    })
    const { container } = render(<LedgerRailsPanel runId="r1" />)
    await waitFor(() => expect(screen.getByText('Cost (est.)')).toBeTruthy())
    expect(container.textContent ?? '').toContain('~$0.0000')
  })

  it('renders a MIXED run’s tokens/cost as a disclosed floor, never a bare number (#3400)', async () => {
    // The defect: one step carried the key, one did not — the same fact `IntrospectPanel`'s Tokens
    // cell already discloses as `≥N` (#3218) over the identical `step_completed` rows. Before this
    // fix, `RailTotals` had no flag at all, so this same total rendered here as a bare "100" one
    // panel over from the "≥100" on the SAME run page.
    rails = async () => payload({
      totals: {
        steps_completed: 2, verdicts: 0, cost_usd: 0.1, cost_recorded: false,
        tokens: 100, tokens_recorded: false, duration_secs: 8,
        verdicts_by_word: {}, overall_series: null,
        absent_scores: ['marginal_value', 'quality_score'],
      } as WorkflowLedgerRails['totals'],
    })
    render(<LedgerRailsPanel runId="r1" />)
    await waitFor(() => expect(screen.getByText('Cost (est.)')).toBeTruthy())
    // The disclosed floor — routed through the SAME helpers `IntrospectPanel` uses.
    expect(screen.getByText('≥100')).toBeTruthy()
    expect(screen.getByText('≥~$0.1000')).toBeTruthy()
    // The precise defect: neither figure may ALSO render bare, with no `≥` disclosing it as a
    // floor — `getByText` is exact-match, so a "100" node distinct from "≥100" would be the bug.
    expect(screen.queryByText('100')).toBeNull()
    expect(screen.queryByText('~$0.1000')).toBeNull()
  })

  it('renders a zero DURATION as 0s, so the label is never left with nothing after it', async () => {
    // The same measured-vs-absent rule, one column over — and the column that was still wrong.
    // Measured on a fresh container: `knowledge-health`'s two steps recorded `duration_secs` of
    // `0.007` and `0.0`; the first rounded to `0s` and rendered, the second went through
    // `fmtElapsed`'s empty-string return and rendered a bare `Took` with nothing after it. One
    // panel, one label, two renderings, seven milliseconds apart — and the blank is also
    // indistinguishable from this rail's deliberate em dash, which claims the opposite.
    rails = async () => payload({
      findings: [runFinding({ node_id: 'fast', duration_secs: 0.007 }), runFinding({ node_id: 'instant', duration_secs: 0 })],
      totals: {
        // Both steps recorded their keys, so neither aggregate is a partial FLOOR — the `*_recorded`
        // flags are `true` (#3400's disclosure is a different fact from this test's zero duration).
        steps_completed: 2, verdicts: 0, cost_usd: 0, cost_recorded: true,
        tokens: 0, tokens_recorded: true, duration_secs: 0,
        verdicts_by_word: {}, overall_series: null,
        absent_scores: ['marginal_value', 'quality_score'],
      } as WorkflowLedgerRails['totals'],
    })
    const { container } = render(<LedgerRailsPanel runId="r1" />)
    await waitFor(() => expect(screen.getByText('Findings rail')).toBeTruthy())
    // Read each `Took` cell's own value: a `0s` elsewhere on the panel would satisfy a text query
    // while this cell stayed blank, which is exactly how the defect survived.
    const took = [...container.querySelectorAll('dt')].filter((dt) => dt.textContent?.trim() === 'Took')
    expect(took.length).toBe(2)
    for (const dt of took) {
      expect(dt.parentElement?.querySelector('dd')?.textContent?.trim()).toBe('0s')
    }
    // …and the totals cell above them, from the same formatter.
    const stepTime = screen.getByText('Step time').parentElement?.querySelector('dd')
    expect(stepTime?.textContent?.trim()).toBe('0s')
  })

  it('names a kind with no run-side producer instead of showing it as zero events', async () => {
    rails = async () => payload()
    render(<LedgerRailsPanel runId="r1" />)
    // `breaker_trip` is written only by the loop watchdog. Saying so is the whole point: a surface
    // that silently omitted it would let a reader conclude the breaker never tripped.
    await waitFor(() => expect(screen.getByText(/No run-side producer for breaker_trip/)).toBeTruthy())
  })

  it('names the ROI axis it cannot plot rather than plotting zeros', async () => {
    rails = async () => payload()
    render(<LedgerRailsPanel runId="r1" />)
    await waitFor(() => expect(screen.getByRole('radio', { name: /Verdict \/ ROI/ })).toBeTruthy())
    fireEvent.click(screen.getByRole('radio', { name: /Verdict \/ ROI/ }))
    await waitFor(() => expect(screen.getByText(/carries no marginal_value or quality_score/)).toBeTruthy())
  })

  it('distinguishes "no judge ran" from "the judge scored nothing"', async () => {
    rails = async () => payload({
      verdicts: [],
      totals: {
        steps_completed: 1, verdicts: 0, cost_usd: 0.02, cost_recorded: true,
        tokens: 900, tokens_recorded: true, duration_secs: 8,
        verdicts_by_word: {}, overall_series: null,
        absent_scores: ['marginal_value', 'quality_score'],
      } as WorkflowLedgerRails['totals'],
    })
    render(<LedgerRailsPanel runId="r1" />)
    await waitFor(() => expect(screen.getByRole('radio', { name: /Verdict \/ ROI/ })).toBeTruthy())
    fireEvent.click(screen.getByRole('radio', { name: /Verdict \/ ROI/ }))
    // A null series with no verdicts is "no judge ran" — NOT a flat-zero chart.
    await waitFor(() => expect(screen.getByText(/No judge has run on this run yet/)).toBeTruthy())
    expect(screen.queryByRole('img', { name: /Judge scores/ })).toBeNull()
  })

  it('states the empty findings case in words rather than collapsing', async () => {
    rails = async () => payload({
      findings: [],
      totals: {
        steps_completed: 0, verdicts: 0, cost_usd: null, cost_recorded: true,
        tokens: null, tokens_recorded: true, duration_secs: null,
        verdicts_by_word: {}, overall_series: null,
        absent_scores: ['marginal_value', 'quality_score'],
      } as WorkflowLedgerRails['totals'],
    })
    render(<LedgerRailsPanel runId="r1" />)
    // A young run is not a broken panel, and blank space cannot say which it is.
    await waitFor(() => expect(screen.getByText(/No step has completed yet/)).toBeTruthy())
  })

  it('surfaces a failed read instead of rendering empty rails', async () => {
    rails = async () => { throw new Error('no run "r1"') }
    render(<LedgerRailsPanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/no run "r1"/)).toBeTruthy())
  })
})
