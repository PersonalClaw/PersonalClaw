/**
 * The two plan reviews say when the plan could not be read, instead of waiting on it forever.
 *
 *   · Loop plan review: the loop read was `.catch(() => {})`, and `!loop` rendered "Analyzing the
 *     plan…" — a planner that never finishes, on a read that had already failed.
 *   · Code plan review: the failure set `error`, which is only drawn INSIDE the loaded branch, so it
 *     was set where nothing could show it and `!project` kept the spinner up.
 *
 * Both now draw the platform's `LoadError` in place of the wait, with a Retry that re-reads — and the
 * loop review keeps its Back, since the header it normally draws needs the loop it could not read.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { Loop } from '../../lib/api'
import type { LoopDraft } from './loopDraft'
import type { CodeDraft } from '../code/codeDraft'

const offline = () => Promise.reject(new TypeError('Failed to fetch'))

const LOOP = {
  id: 'L1', name: 'ZZ research', kind: 'research', status: 'review', granularity: 'balanced',
  attended: true, task: 'research the competitive landscape for on-device agents', kind_config: {},
} as unknown as Loop

const loopDraft: LoopDraft = {
  loopId: 'L1',
  classification: { kind: 'research', execution: 'solo', kind_config: {} },
  rigor: 'minimal', agent: 'a', model: 'm', granularity: 'balanced', attended: true,
}

const codeDraft = {
  projectId: 'P1',
  classification: { marketplace_suggestions: [] },
  rigor: 'minimal',
  attended: true,
} as unknown as CodeDraft

function mockApi(uLoop: () => Promise<unknown>) {
  vi.doMock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        uLoop,
        savedAgents: () => Promise.resolve([]),
        skills: () => Promise.resolve([]),
        uLoopPlanSession: () => Promise.resolve(null),
      },
    }
  })
}

beforeEach(() => vi.resetModules())
afterEach(() => { cleanup(); vi.doUnmock('./useRunStream'); vi.doUnmock('../../lib/api') })

describe('the loop plan review', () => {
  it('says the read failed, with a Retry and the way back, instead of "Analyzing the plan…"', async () => {
    let calls = 0
    mockApi(() => (++calls === 1 ? offline() : Promise.resolve(LOOP)))
    const onBack = vi.fn()
    const { LoopPlanReview } = await import('./LoopPlanReview')
    render(<LoopPlanReview draft={loopDraft} onLaunched={() => {}} onBack={onBack} />)
    expect(await screen.findByText("Couldn't load your plan")).toBeTruthy()
    expect(screen.queryByText(/Analyzing the plan/), 'a failed read is not a planner still working').toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(onBack).toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    await waitFor(() => expect(screen.getByText('Step 1 / 3')).toBeTruthy())
    expect(calls).toBe(2)
  })
})

describe('the code plan review', () => {
  it('says the read failed, with a Retry, instead of a spinner that never stops', async () => {
    let calls = 0
    mockApi(() => (++calls === 1 ? offline() : Promise.resolve({ ...LOOP, id: 'P1', kind: 'code', plan: [] })))
    const { CodePlanReview } = await import('../code/CodePlanReview')
    render(<CodePlanReview draft={codeDraft} onBack={() => {}} onLaunched={() => {}} />)
    expect(await screen.findByText("Couldn't load your project")).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    await waitFor(() => expect(screen.getByText(LOOP.task)).toBeTruthy())
    expect(calls).toBe(2)
  })
})
