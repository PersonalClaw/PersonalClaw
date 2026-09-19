/**
 * #583 — the inbox stops offering Approve on a run that died holding the question.
 *
 * The API advertised live resume tokens for a terminal run (fixed server-side in
 * `tests/test_workflows_terminal_run_has_no_gate.py`), so this component faithfully rendered a
 * filled **Approve** beside a "Handled" chip on a run that failed 13 hours earlier. Clicking it
 * returned `WF_RUN_NOT_LIVE`.
 *
 * The second half is here: with nothing answerable, the component used to print **"This request was
 * already answered."** for every empty list — including a run that failed holding the question, and
 * including a lookup that simply failed. Three different facts, one sentence. Someone told their
 * question was already answered goes looking for an answer that was never given.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

let continuations: ReturnType<typeof vi.fn>

async function mountGate(res: unknown, opts: { reject?: boolean } = {}) {
  continuations = vi.fn(() => (opts.reject ? Promise.reject(new Error('boom')) : Promise.resolve(res)))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: { ...(real.api as object), workflowContinuations: continuations, resumeWorkflowRun: vi.fn() },
    }
  })
  const { WorkflowGateActions } = await import('./WorkflowGateActions')
  render(<WorkflowGateActions runId="b246d785" onChanged={() => {}} navigate={() => {}} />)
  await waitFor(() => expect(continuations).toHaveBeenCalled())
}

const ASK = {
  resume_token: 'tok-1', node_id: 'g', instance_path: 'root.g',
  ask: { kind: 'approval', prompt: 'approve?' }, handoff: null, expires_at: '', expired: false,
}

beforeEach(() => { cleanup(); vi.resetModules() })
afterEach(() => cleanup())

describe('a terminal run', () => {
  it('says the run ended rather than claiming the question was answered', async () => {
    // 🔑 The measured case: a failed run. "Already answered" sends the user looking for an answer
    // that was never given.
    await mountGate({ continuations: [], run_status: 'failed' })
    await waitFor(() => expect(screen.getByText(/this run failed/i)).toBeTruthy())
    expect(screen.queryByText(/already answered/i)).toBeNull()
  })

  it('names each ending in its own words', async () => {
    for (const [status, phrase] of [['cancelled', /was cancelled/i], ['escalated', /was escalated/i], ['complete', /finished/i]] as const) {
      cleanup(); vi.resetModules()
      await mountGate({ continuations: [], run_status: status })
      await waitFor(() => expect(screen.getByText(phrase)).toBeTruthy())
    }
  })

  it('offers no Approve or Deny at all', async () => {
    // The defect as the user met it: a filled primary button that could only ever fail.
    await mountGate({ continuations: [], run_status: 'failed' })
    await waitFor(() => expect(screen.getByText(/no longer be answered/i)).toBeTruthy())
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /deny/i })).toBeNull()
  })
})

describe('the other empty reasons keep their own sentences', () => {
  it('an answered gate on a LIVE run still reads as answered', async () => {
    // Vacuity floor: a change that said "this run ended" for every empty list would be a new lie.
    await mountGate({ continuations: [], run_status: 'needs_input' })
    await waitFor(() => expect(screen.getByText(/already answered/i)).toBeTruthy())
  })

  it('a failed lookup says it could not check, not that it was answered', async () => {
    await mountGate(null, { reject: true })
    await waitFor(() => expect(screen.getByText(/couldn.t check/i)).toBeTruthy())
    expect(screen.queryByText(/already answered/i)).toBeNull()
  })

  it('a response with no run_status degrades to the answered sentence', async () => {
    // An older gateway (or any caller that omits the field) must not produce "this run undefined".
    await mountGate({ continuations: [] })
    await waitFor(() => expect(screen.getByText(/already answered/i)).toBeTruthy())
  })
})

describe('a live gate is untouched', () => {
  it('still hands its continuation to the ask renderer', async () => {
    // The whole feature: WF2-R7 answers gates in the inbox. A filter that hid a live gate would
    // break the surface it exists to serve.
    //
    // `WorkflowAsk` is stubbed on purpose — it is the shared typed-ask renderer with its own
    // tests, and mounting it for real here would make this rail fail on ITS fixture needs rather
    // than on the routing decision under test.
    vi.doMock('../workflows/WorkflowAsk', () => ({
      WorkflowAsk: ({ continuation }: { continuation: { resume_token: string } }) =>
        <div data-testid="ask">{continuation.resume_token}</div>,
    }))
    await mountGate({ continuations: [ASK], run_status: 'needs_input' })
    await waitFor(() => expect(screen.getByTestId('ask').textContent).toBe('tok-1'))
    expect(screen.queryByText(/already answered|no longer be answered/i)).toBeNull()
  })
})

describe('the mirrored status vocabulary cannot drift', () => {
  it('matches TERMINAL_RUN_STATUSES in the Python source', () => {
    // 🪤 The wire carries a bare string, so the set is duplicated in TS. Derived here from the
    // Python definition (`ENDED - RESUMABLE_ENDED`, with the resumable set deliberately empty) so
    // a fifth ending, or a status that becomes resumable, reds this instead of silently arming a
    // dead button again.
    const models = readFileSync(join(process.cwd(), '../src/personalclaw/workflows/models.py'), 'utf8')
    const phases = models.slice(models.indexOf('RUN_PHASES'), models.indexOf('ENDED_RUN_STATUSES'))
    const ended = [...phases.matchAll(/RunStatus\.([A-Z_]+): LifecyclePhase\.ENDED/g)].map((m) => m[1].toLowerCase())
    expect(ended.length, 'no ENDED statuses parsed — the mapping moved').toBeGreaterThan(2)

    const src = readFileSync(join(process.cwd(), 'src/pages/inbox/WorkflowGateActions.tsx'), 'utf8')
    const declared = [...src.slice(src.indexOf('TERMINAL_RUN_STATUSES = new Set(')).matchAll(/'([a-z_]+)'/g)]
      .map((m) => m[1])
      .slice(0, ended.length)
    expect(new Set(declared)).toEqual(new Set(ended))

    // And every one of them has a verb, or the sentence renders "This run ended" for a status the
    // map forgot — true but vague where a specific word was available.
    const verbs = src.slice(src.indexOf('const ENDED_VERB'), src.indexOf('/** Answer a workflow'))
    for (const status of ended) expect(verbs, `${status} has no verb`).toContain(`${status}:`)
  })
})
