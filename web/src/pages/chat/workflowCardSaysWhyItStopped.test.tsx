/**
 * #565 — the chat card stops saying "Failed" and nothing else.
 *
 * This card is the fold's ONLY consumer, so it is where the fold change has to be visible: the
 * fold used to null `attention` on any terminal status, which deleted the escalation on the very
 * event that carries the failure. And on the retries-exhausted path `run.error` is empty
 * (measured — `tests/test_workflow_escalation_is_reported.py`), so the card's error line rendered
 * nothing too.
 *
 * The card gets ONE line — the reason. The per-attempt evidence is on the run page behind Open: a
 * chat card is a glance, and an attempt table inside a message stream is a wall.
 *
 * A terminal run does not subscribe to the stream (its own rule), so mounting one needs no
 * EventSource — only the snapshot fetch.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'

const ESCALATION = {
  kind: 'escalation', node_id: 'consume', reason: 'retries_exhausted',
  detail: 'ConnectionError: network down',
  options: ['reassign', 'decompose', 'revise', 'accept_with_limitations', 'defer'],
  attempts: [{
    attempt: 1, failure_class: 'network', error: 'ConnectionError: network down',
    fix_instruction: 'check connectivity; the engine will retry',
    severity: 'error', error_signature: '201cd4f86a47',
  }],
}

let workflowRun: ReturnType<typeof vi.fn>

async function mountCard(over: Record<string, unknown>) {
  workflowRun = vi.fn(() => Promise.resolve({
    run_id: 'b246d785', workflow: 'triage', status: 'failed', spec_version: 1,
    error: '', attention: null, tokens: 0, elapsed_secs: 3,
    nodes: [{ instance_path: 'root', node_id: 'consume', state: 'failed' }],
    ...over,
  }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return { ...real, api: { ...(real.api as object), workflowRun } }
  })
  const { WorkflowProgressCard } = await import('./WorkflowProgressCard')
  render(<WorkflowProgressCard refObj={{ runId: 'b246d785', created: true }} />)
  await waitFor(() => expect(workflowRun).toHaveBeenCalled())
}

beforeEach(() => { cleanup(); vi.resetModules() })
afterEach(() => cleanup())

describe('a run that gave up', () => {
  it('🔑 says why, on a run whose error line is empty', async () => {
    await mountCard({ status: 'failed', error: '', attention: ESCALATION })
    await waitFor(() =>
      expect(screen.getByText(/Stopped: every retry was spent and the step still failed/i)).toBeTruthy())
  })

  it('does not claim the user is being waited on', async () => {
    // 🪤 The pre-fix reading of this record: `attention.prompt` is undefined, so the ask line
    // printed the generic 'Waiting on you'. The run is dying, not asking.
    await mountCard({ status: 'failed', error: '', attention: ESCALATION })
    await waitFor(() => expect(screen.getByText(/Stopped:/)).toBeTruthy())
    expect(screen.queryByText(/waiting on you/i)).toBeNull()
  })

  it('keeps the card to ONE line — no attempt table in a chat turn', async () => {
    await mountCard({ status: 'failed', error: '', attention: ESCALATION })
    await waitFor(() => expect(screen.getByText(/Stopped:/)).toBeTruthy())
    expect(screen.queryByText(/check connectivity/i)).toBeNull()
    expect(screen.queryByText(/Attempt 1/)).toBeNull()
  })
})

describe('a run that is genuinely waiting', () => {
  it('shows the ask’s own prompt', async () => {
    // Vacuity floor: the surface that already worked must keep working. The prompt now arrives
    // through `attentionLine` instead of a hand-reached `attention.prompt`.
    await mountCard({ status: 'needs_input', attention: { kind: 'approval', prompt: 'Ship it?' } })
    await waitFor(() => expect(screen.getByText('Ship it?')).toBeTruthy())
    expect(screen.queryByText(/Stopped:/)).toBeNull()
  })

  it('falls back when the ask carries no words', async () => {
    await mountCard({ status: 'needs_input', attention: { kind: 'approval' } })
    await waitFor(() => expect(screen.getByText(/waiting on you/i)).toBeTruthy())
  })

  it('says nothing about attention on an ordinary running run', async () => {
    await mountCard({ status: 'running', attention: null })
    await waitFor(() => expect(workflowRun).toHaveBeenCalled())
    expect(screen.queryByText(/waiting on you/i)).toBeNull()
    expect(screen.queryByText(/Stopped:/)).toBeNull()
  })
})

describe('a completed run', () => {
  it('shows no diagnosis — there is nothing to diagnose', async () => {
    await mountCard({ status: 'complete', attention: null })
    await waitFor(() => expect(workflowRun).toHaveBeenCalled())
    expect(screen.queryByText(/Stopped:/)).toBeNull()
  })

  it('and an ANSWERED gate on a finished run does not re-offer itself', async () => {
    // The record now survives a terminal status, so this is the case the old deletion was
    // protecting against: a leftover ask on a run nobody can answer. `needsInput` is what gates
    // it, and a complete run is not that.
    await mountCard({ status: 'complete', attention: { kind: 'approval', prompt: 'Ship it?' } })
    await waitFor(() => expect(workflowRun).toHaveBeenCalled())
    expect(screen.queryByText('Ship it?')).toBeNull()
    expect(screen.queryByText(/waiting on you/i)).toBeNull()
  })
})
