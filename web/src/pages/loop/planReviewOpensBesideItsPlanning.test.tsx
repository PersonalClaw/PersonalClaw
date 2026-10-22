/** A plan in review opens beside its planning, in the pane host, not in place of it (#3821).
 *
 *  A goal loop or code project whose plan has landed (status `review`) used to swap its section for
 *  a Plan Review page. Its planning walkthrough now stays as the work, saying the plan is ready,
 *  and Plan Review opens beside it in the shared pane host. Closing the pane only hides it: the
 *  walkthrough offers it back. Launching from the pane is the same launch as before, through the
 *  same route, and goes to the launched loop's page. This renders each section on its route.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

const h = vi.hoisted(() => {
  const now = Date.now() / 1000
  const loops: Record<string, Record<string, unknown>> = {
    L1: {
      id: 'L1', name: 'Tidy the reading list', kind: 'goal', status: 'review', task: 'tidy the reading list',
      execution: 'solo', plan: [], intake_rigor: 'minimal', attended: true, kind_config: { goal_type: 'open_ended' },
    },
    P1: {
      id: 'P1', name: 'Feed refetch fix', kind: 'code', status: 'review', task: 'fix the feed refetch',
      plan: [{ stage: 'build', title: 'Build' }], intake_rigor: 'minimal', attended: true,
      kind_config: { project_kind: 'greenfield', entry_stage: 'build' },
    },
  }
  const session = (id: string) => ({
    project_id: id, created_at: now, updated_at: now,
    steps: [{ id: 's1', kind: 'goal', title: 'Understand the goal', status: 'approved', artifact: { markdown: 'Tag every saved article.' } }],
  })
  const calls: { name: string; args: unknown[] }[] = []
  const reads: Record<string, (...args: unknown[]) => Promise<unknown>> = {
    uLoop: (id) => Promise.resolve(loops[id as string]),
    uLoopPlanSession: (id) => Promise.resolve(session(id as string)),
    uLoopAction: () => Promise.resolve(undefined),
    saveULoopSpec: (id) => Promise.resolve(loops[id as string]),
    savedAgents: () => Promise.resolve([]),
    skills: () => Promise.resolve([]),
    approvals: () => Promise.resolve([]),
  }
  return { calls, reads }
})

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  const api = new Proxy(real.api as unknown as Record<string, unknown>, {
    get: (_target, prop: string) => (...args: unknown[]) => {
      h.calls.push({ name: prop, args })
      return prop in h.reads ? h.reads[prop](...args) : Promise.resolve({})
    },
  })
  return { ...real, api }
})
vi.mock('../loops/useRunStream', () => ({ useRunStream: () => ({ connected: true }) }))
vi.mock('../../lib/useChatSocket', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/useChatSocket')>()
  return { ...real, useChatSocket: () => {} }
})

const { LoopsSection } = await import('../loops/LoopsSection')
const { CodeSection } = await import('../code/CodeSection')

const navigate = vi.fn()
beforeEach(() => {
  localStorage.clear()
  h.calls.length = 0
  navigate.mockReset()
  Element.prototype.scrollTo = (() => {}) as unknown as typeof Element.prototype.scrollTo
})
afterEach(cleanup)

const SECTIONS = [
  ['a goal loop', 'L1', () => <LoopsSection sub="L1" navigate={navigate} query={{}} setQuery={() => {}} navEpoch={0} />, 'loops/L1'],
  ['a code project', 'P1', () => <CodeSection sub="P1" navigate={navigate} query={{}} setQuery={() => {}} navEpoch={0} />, 'code/P1'],
] as const

const review = () => screen.findByRole('region', { name: 'Review the plan' })
/** The planning walkthrough: its steps, and its header saying the plan is ready. */
const planning = () => screen.findByText(/Planning steps \(1\/1 approved\)/)

describe.each(SECTIONS)('%s in review', (_what, _id, Section, launched) => {
  it('shows its planning, saying the plan is ready, with Plan Review in the pane beside it', async () => {
    render(<Section />)
    const pane = await review()
    expect(await planning()).toBeInTheDocument()
    expect(screen.getByText('Plan ready')).toBeInTheDocument()
    expect(screen.queryByText('Planning…'), 'the walkthrough no longer says it is planning').toBeNull()
    expect(pane.contains(screen.getByText('Plan ready')), 'the walkthrough is the work, not the pane').toBe(false)
    expect(within(pane).queryByRole('button', { name: 'Back' }), 'the pane has its own close, not a page Back').toBeNull()
  })

  it('closes the pane only, and the walkthrough offers it back', async () => {
    render(<Section />)
    await review()
    fireEvent.click(screen.getByRole('button', { name: 'Close plan review' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Review the plan' })).toBeNull())
    expect(await planning(), 'the planning stays').toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
    expect(h.calls.map((c) => c.name)).not.toContain('uLoopAction')
    fireEvent.click(screen.getByRole('button', { name: 'Review the plan' }))
    expect(await review()).toBeInTheDocument()
  })

  it('launches from the pane through the same route, and goes to the launched page', async () => {
    render(<Section />)
    const pane = await review()
    // The review has read its loop once its step bar (or, for a single-step review, Launch) shows.
    await within(pane).findByRole('button', { name: /^Launch$|Capabilities|Review plan|Answer questions|Continue/ })
    for (let i = 0; i < 4 && !within(pane).queryByRole('button', { name: /^Launch$/ }); i++) {
      fireEvent.click(within(pane).getByRole('button', { name: /Capabilities|Review plan|Answer questions|Continue/ }))
    }
    fireEvent.click(await within(pane).findByRole('button', { name: /^Launch$/ }))
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(launched))
    expect(h.calls.filter((c) => c.name === 'uLoopAction').map((c) => c.args)).toEqual([[_id, 'start']])
  })
})
