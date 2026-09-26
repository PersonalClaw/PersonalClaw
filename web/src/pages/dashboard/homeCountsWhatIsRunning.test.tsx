import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Loop } from '../../lib/api'

// ── Home says what is RUNNING, in the numbers every other loop surface uses ─────────────────────
//
// Measured 2026-09-25, Home beside a paused loop and a running one:
//   • the hero read "1 loop running" for a loop that was PAUSED — it counted every active status
//     (parked ones included) under a label that says "running";
//   • Active Work read "cycle 1/30" while the cockpit read "Cycle 2/30": it printed the raw
//     COMPLETED count, bypassing the one `shownCycle` every other surface uses (issue 274's shape);
//   • a run-backed loop (a General loop is a workflow run, PP-16) opened `#/loops/<id>`, and its
//     Answer typed into a nudge box — which steers a run and answers no gate.

const loops: Loop[] = []

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    approvals: () => Promise.resolve([]),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [] }),
    uLoops: () => Promise.resolve(loops),
    readyTasks: () => Promise.resolve([]),
    notifications: () => Promise.resolve({ notifications: [] }),
    triggersHistory: () => Promise.resolve({ runs: [], did_ids: [] }),
    status: () => Promise.resolve({}),
    system: () => Promise.resolve({}),
    discover: () => Promise.resolve({ tips: [] }),
    dismissDiscoverTip: () => Promise.resolve({}),
    doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
    uLoopNudge: vi.fn(() => Promise.resolve()),
  },
}))

const { DashboardLiveProvider } = await import('./DashboardLive')
const { HeroPulse } = await import('./widgets/HeroPulse')
const { ActiveWork } = await import('./widgets/ActiveWork')

function loop(over: Partial<Loop>): Loop {
  return {
    id: 'l1', kind: 'goal', name: 'Packing note', task: 'write a packing note',
    execution: 'solo', agent: '', model: '', attended: false, max_cycles: 30, idle_secs: 60,
    success_criteria: null, status: 'running', total_cycles: 1, error_message: null,
    created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: null, kind_config: {},
    ...over,
  } as Loop
}

function mount(node: 'hero' | 'active', navigate = vi.fn()) {
  const props = { sub: '', navigate, navEpoch: 0, setQuery: () => {}, query: {} }
  render(
    <DashboardLiveProvider>
      {node === 'hero' ? <HeroPulse {...props} /> : <ActiveWork {...props} />}
    </DashboardLiveProvider>,
  )
  return navigate
}

beforeEach(() => { loops.length = 0; sessionStorage.clear() })

describe('the hero counts loops that are running', () => {
  it('a paused loop and a loop waiting on the user are not "running"', async () => {
    loops.push(loop({ id: 'a' }), loop({ id: 'b', status: 'paused' }), loop({ id: 'c', status: 'needs_input' }))
    mount('hero')
    await waitFor(() => expect(screen.getByLabelText('1 loop running')).toBeInTheDocument())
  })

  it('opens the loops, not the Projects landing that had no way to them', async () => {
    loops.push(loop({ id: 'a' }))
    const navigate = mount('hero')
    fireEvent.click(await screen.findByLabelText('1 loop running'))
    expect(navigate).toHaveBeenCalledWith('loops/history')
  })
})

describe('Active Work reads the same cycle as the cockpit', () => {
  it('counts the open cycle: one completed of thirty is "cycle 2/30"', async () => {
    loops.push(loop({ total_cycles: 1, max_cycles: 30 }))
    mount('active')
    expect(await screen.findByText(/cycle 2\/30/)).toBeTruthy()
    expect(screen.queryByText(/cycle 1\/30/)).toBeNull()
  })

  it('opens a run-backed loop on its run page', async () => {
    loops.push(loop({ id: 'r1', run_id: 'r1', kind: 'general', name: 'Weekly checklist' }))
    const navigate = mount('active')
    fireEvent.click(await screen.findByText('Weekly checklist'))
    expect(navigate).toHaveBeenCalledWith('workflows/runs/r1')
  })

  it("answers a run-backed loop's question on its run page, not in a nudge box", async () => {
    loops.push(loop({ id: 'r1', run_id: 'r1', kind: 'general', name: 'Weekly checklist', status: 'needs_input' }))
    const navigate = mount('active')
    fireEvent.click(await screen.findByRole('button', { name: /Answer: Weekly checklist/ }))
    expect(navigate).toHaveBeenCalledWith('workflows/runs/r1')
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it("a loops-table loop's Answer still opens the answer box — the control", async () => {
    loops.push(loop({ status: 'needs_input' }))
    const navigate = mount('active')
    fireEvent.click(await screen.findByRole('button', { name: /Answer: Packing note/ }))
    expect(await screen.findByRole('textbox')).toBeTruthy()
    expect(navigate).not.toHaveBeenCalled()
  })
})
