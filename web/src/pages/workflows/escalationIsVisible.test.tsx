/**
 * #565 — a failed run now explains itself.
 *
 * Measured server-side (`tests/test_workflow_escalation_is_reported.py`): a run whose node
 * exhausts its retries reports `error: ''` — `_finish(status)` takes no `error` on that path — and
 * the escalation on `attention` holds the reason, the cause and the per-attempt evidence. The run
 * page's one explanation slot is `run.error && <p>`, so that run rendered a failed run with a
 * completely blank reason. 13 of 16 terminal-failed runs on the reporter's instance were in this
 * state.
 *
 * These drive the real components against that real payload. The load-bearing pair:
 *
 * • the diagnosis renders on a TERMINAL run (that is the only kind that carries one), and
 * • nothing ANSWERABLE renders with it — the fold no longer deletes the record on a terminal
 *   status, so "the run holds something" must not be read as "you can act on it".
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { EscalationPanel } from './EscalationPanel'
import { attentionLine, readAttention, type EscalationRead } from './attentionMeta'
import { foldEvent, foldSnapshot } from './workflowFold'

/** The measured payload, verbatim from the probe in the issue's investigation. */
const ATTENTION = {
  kind: 'escalation',
  node_id: 'consume',
  reason: 'retries_exhausted',
  detail: 'ConnectionError: network down',
  options: ['reassign', 'decompose', 'revise', 'accept_with_limitations', 'defer'],
  attempts: [
    {
      attempt: 1, failure_class: 'network', error: 'ConnectionError: network down',
      expected: '', actual: '', evidence: '',
      fix_instruction: 'check connectivity; the engine will retry',
      severity: 'error', error_signature: '201cd4f86a47', tokens: 0, duration_secs: 0.002,
    },
    {
      attempt: 2, failure_class: 'network', error: 'ConnectionError: network down',
      expected: '', actual: '', evidence: '',
      fix_instruction: 'check connectivity; the engine will retry',
      severity: 'error', error_signature: '201cd4f86a47', tokens: 0, duration_secs: 0,
    },
  ],
}

function read(): EscalationRead {
  const r = readAttention(ATTENTION)
  if (r?.kind !== 'escalation') throw new Error('fixture did not read as an escalation')
  return r
}

beforeEach(() => cleanup())
afterEach(() => cleanup())

describe('the panel', () => {
  it('names the reason, the node, and the engine’s suggested fix', () => {
    render(<EscalationPanel read={read()} />)
    expect(screen.getByText(/needs a decision/i)).toBeTruthy()
    expect(screen.getByText(/every retry was spent and the step still failed/i)).toBeTruthy()
    expect(screen.getByText('consume')).toBeTruthy()
    expect(screen.getAllByText(/check connectivity/i)).toHaveLength(2)
  })

  it('lists every attempt, not just the last', () => {
    // Two attempts with the SAME signature: a de-dup on signature would collapse them and lose
    // "it failed the same way twice", which is exactly what makes retrying pointless.
    render(<EscalationPanel read={read()} />)
    expect(screen.getByText('Attempt 1')).toBeTruthy()
    expect(screen.getByText('Attempt 2')).toBeTruthy()
  })

  it('offers NO control for the five options', () => {
    // 🪤 `ESCALATION_OPTIONS` is a module constant — the same five strings on every escalation
    // ever produced — and no endpoint accepts one back. Five buttons that cannot succeed would be
    // worse than the silence this fixes, and five inert words carry no information about THIS
    // run. The decision surface that consumes them is engine work.
    render(<EscalationPanel read={read()} />)
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    for (const option of ATTENTION.options) {
      expect(screen.queryByText(option), `${option} rendered with nothing to click`).toBeNull()
    }
  })

  it('does not print the cause twice when the run already reported it', () => {
    // On the loud-failure paths `_finish(FAILED, error=…)` DOES carry a message, and it can be
    // the same string as the escalation's detail. Two copies of one sentence reads as two
    // problems.
    render(<EscalationPanel read={read()} runError="ConnectionError: network down" />)
    // Still once — as the attempt's own error line, which is labelled by its attempt number.
    expect(screen.getAllByText('ConnectionError: network down')).toHaveLength(2)
    cleanup()
    render(<EscalationPanel read={read()} runError="" />)
    // The standalone detail line is back: 2 attempts + 1 detail.
    expect(screen.getAllByText('ConnectionError: network down')).toHaveLength(3)
  })

  it('renders a breaker escalation that has no attempts at all', () => {
    const r = readAttention({ kind: 'escalation', reason: 'token_cap', node_id: 'draft' })
    if (r?.kind !== 'escalation') throw new Error('not an escalation')
    render(<EscalationPanel read={r} />)
    expect(screen.getByText(/reached its token budget/i)).toBeTruthy()
    expect(screen.queryByText(/^Attempt/)).toBeNull()
  })
})

// ── the run page ─────────────────────────────────────────────────────────────────────────────

let workflowRun: ReturnType<typeof vi.fn>

async function mountRun(over: Record<string, unknown>) {
  workflowRun = vi.fn(() => Promise.resolve({
    run_id: 'b246d785', workflow: 'triage', status: 'failed', spec_version: 1,
    error: '', attention: null, tokens: 0, elapsed_secs: 4, nodes: [], ...over,
  }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        workflowRun,
        workflowContinuations: vi.fn(() => Promise.resolve({ continuations: [] })),
      },
    }
  })
  const { WorkflowRunDetail } = await import('./WorkflowRunDetail')
  render(<WorkflowRunDetail runId="b246d785" onBack={() => {}} onOpenRun={() => {}} />)
  await waitFor(() => expect(workflowRun).toHaveBeenCalled())
}

describe('the run page', () => {
  beforeEach(() => { cleanup(); vi.resetModules() })

  it('🔑 explains a failed run whose error line is EMPTY', async () => {
    // The measured case, end to end: status failed, error '', diagnosis only on `attention`.
    await mountRun({ status: 'failed', error: '', attention: ATTENTION })
    await waitFor(() => expect(screen.getByTestId('escalation-panel')).toBeTruthy())
    expect(screen.getByText(/every retry was spent/i)).toBeTruthy()
  })

  it('shows nothing extra for a run holding a gate ASK', async () => {
    // Vacuity floor: the panel keys on the record's KIND, not on the run being terminal. A
    // version that rendered for any attention would put a diagnosis card on every waiting gate.
    await mountRun({
      status: 'needs_input', attention: { kind: 'approval', prompt: 'Ship it?' },
    })
    await waitFor(() => expect(workflowRun).toHaveBeenCalled())
    expect(screen.queryByTestId('escalation-panel')).toBeNull()
  })

  it('shows nothing when the run carries no attention at all', async () => {
    await mountRun({ status: 'complete', attention: null })
    await waitFor(() => expect(workflowRun).toHaveBeenCalled())
    expect(screen.queryByTestId('escalation-panel')).toBeNull()
  })
})

// ── the fold's contract, as this page depends on it ──────────────────────────────────────────

describe('the record reaches a terminal run at all', () => {
  it('🔑 survives the REAL event sequence a failing run emits', () => {
    // The card mounts a snapshot, so its own tests never touch `applyRunStatus` — and the fold's
    // deletion only bit on the EVENT path. This composes the three real pieces in the order the
    // controller drives them, which is where the record was lost:
    //
    //   `_escalate` publishes `workflow_attention` … then `_finish` publishes the terminal status.
    //
    // Pre-fix, the second frame nulled what the first delivered, so a card watching a live run
    // that failed ended with nothing to show — while a page that REFETCHED got the record from
    // the snapshot. Two paths, two answers, for the same run.
    let vm = foldSnapshot({
      run_id: 'a1b2c3d4', workflow: 'triage', status: 'running', spec_version: 1,
      error: '', attention: null, tokens: 0, elapsed_secs: 0, nodes: [],
    })
    vm = foldEvent(vm, 'workflow_attention', {
      run_id: 'a1b2c3d4', event_id: 'e1', seq: 1, epoch: 0, ask: ATTENTION,
    })
    vm = foldEvent(vm, 'workflow_run_update', {
      run_id: 'a1b2c3d4', event_id: 'e2', seq: 2, epoch: 0, status: 'failed',
    })

    expect(vm.status).toBe('failed')
    expect(vm.needsInput, 'a dead run is not answerable').toBe(false)
    // …and what the card renders off that view-model.
    expect(attentionLine(vm.attention))
      .toBe('Stopped: every retry was spent and the step still failed')
  })

  it('the fold no longer nulls attention on a terminal status', () => {
    // Labelled a SOURCE PIN: the behavioural half lives in `workflowFold.test.ts` ("a terminal
    // run KEEPS the record"). What that cannot see is a re-introduction of the deletion in a
    // NEW arm of the same switch — which is how the record was lost in the first place, since
    // `applyRunStatus` runs for every status transition.
    const src = readFileSync(join(process.cwd(), 'src/pages/workflows/workflowFold.ts'), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code, 'a terminal status is nulling attention again').not.toMatch(
      /TERMINAL_RUN\.has\([^)]*\)\s*\?\s*null/,
    )
  })
})
