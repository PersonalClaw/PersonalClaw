import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { invalidateKeys } from '../../lib/data'
import type { Loop } from '../../lib/api'

// ── A loop's cycle opens in the pane beside its thread (#3824) ────────────────────────────────────
//
// The loop page's Details rail lists the loop's cycles. A cycle used to replace that list, so reading
// one meant losing the others and coming Back to pick the next. It opens in the pane host beside the
// thread now (#3821's), under the same address (`?details=1&sel=cycle-N`), and the rail keeps its list
// with that cycle marked, so the next is one press away. The Findings Log and the returns per cycle
// still open in the rail. Closing the pane clears the address and stops nothing.

const { STORE, action } = vi.hoisted(() => ({ STORE: { loop: null as Loop | null }, action: vi.fn() }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loop ? [STORE.loop] : []),
    uLoop: () => (STORE.loop ? Promise.resolve(STORE.loop) : Promise.reject(new Error('no such loop'))),
    uLoopAction: (id: string, a: string) => action(id, a),
    uLoopReport: () => Promise.resolve({ report: '', log: '# Findings\n\nThe tracker moved twice.' }),
    artifacts: () => Promise.resolve([]),
    task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Test project' }),
    deleteULoop: () => Promise.resolve(),
    updateULoop: () => Promise.resolve(null),
    uLoopNudge: () => Promise.resolve(),
  },
}))
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))

const { LoopCockpitPage } = await import('./LoopCockpitPage')

const loop = (): Loop => ({
  id: 'w1', kind: 'goal', name: 'Watch the soil delivery', task: 'Say when the soil is here',
  execution: 'solo', agent: 'claude-code', model: 'sonnet', attended: true, max_cycles: 10, idle_secs: 60,
  success_criteria: null, status: 'running', total_cycles: 2, error_message: null,
  created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: null, kind_config: { goal_type: 'monitor' },
  findings: [
    { cycle: 1, summary: 'Checked the tracker: still at the depot', ts: 1_780_000_100 },
    { cycle: 2, summary: 'Out for delivery since nine', ts: 1_780_000_200 },
  ],
} as unknown as Loop)

/** The page under a query it owns, as the router gives it one. */
function Page({ start }: { start: Record<string, string> }) {
  const [query, setQ] = useState<Record<string, string>>(start)
  const setQuery = (next: Record<string, string | null>) => setQ((q) => {
    const merged: Record<string, string> = { ...q }
    for (const [k, v] of Object.entries(next)) { if (v == null) delete merged[k]; else merged[k] = v }
    return merged
  })
  return (
    <>
      <output data-testid="query">{new URLSearchParams(query).toString()}</output>
      <LoopCockpitPage id="w1" onBack={() => {}} query={query} setQuery={setQuery as never} />
    </>
  )
}

async function mount(start: Record<string, string> = { details: '1' }) {
  render(<Page start={start} />)
  await waitFor(() => screen.getByRole('button', { name: 'Details' }))
}
const address = () => screen.getByTestId('query').textContent
const cycleRow = (n: number, name: RegExp) => screen.getAllByRole('button', { name }).find((b) => b.textContent?.startsWith(String(n)))!

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  STORE.loop = loop()
  action.mockReset()
})

describe('a cycle opens beside the thread', () => {
  it('a press opens it in the pane, and the rail keeps its list with that cycle marked', async () => {
    await mount()
    fireEvent.click(await waitFor(() => cycleRow(1, /still at the depot/)))
    const pane = await screen.findByRole('region', { name: 'Cycle 1' })
    expect(within(pane).getByText(/still at the depot/)).toBeInTheDocument()
    expect(address()).toBe('details=1&sel=cycle-1')
    // The list is still there: the other cycle, and the Findings Log, one press away.
    expect(cycleRow(2, /Out for delivery/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Findings Log/ })).toBeInTheDocument()
    expect(cycleRow(1, /still at the depot/).getAttribute('aria-current')).toBe('true')
    expect(cycleRow(2, /Out for delivery/).getAttribute('aria-current')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Back to list' })).toBeNull()
  })

  it('the next cycle is one press away, in the same pane', async () => {
    await mount({ details: '1', sel: 'cycle-1' })
    await screen.findByRole('region', { name: 'Cycle 1' })
    fireEvent.click(cycleRow(2, /Out for delivery/))
    const pane = await screen.findByRole('region', { name: 'Cycle 2' })
    expect(within(pane).getByText(/Out for delivery since nine/)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Cycle 1' })).toBeNull()
  })

  it('its address opens it again', async () => {
    await mount({ details: '1', sel: 'cycle-2' })
    expect(await screen.findByRole('region', { name: 'Cycle 2' })).toBeInTheDocument()
  })

  it('closing the pane clears the address and stops nothing', async () => {
    await mount({ details: '1', sel: 'cycle-2' })
    const pane = await screen.findByRole('region', { name: 'Cycle 2' })
    fireEvent.click(within(pane).getByRole('button', { name: 'Close cycle' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Cycle 2' })).toBeNull())
    expect(address()).toBe('details=1')
    expect(action).not.toHaveBeenCalled()
  })

  it('the Findings Log still opens in the rail, and Back returns to its list', async () => {
    await mount()
    fireEvent.click(await screen.findByRole('button', { name: /Findings Log/ }))
    expect(await screen.findByText('The tracker moved twice.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /^Cycle/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Back to list' }))
    expect(await waitFor(() => cycleRow(1, /still at the depot/))).toBeInTheDocument()
  })
})
