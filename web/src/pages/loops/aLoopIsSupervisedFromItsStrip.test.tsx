import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop } from '../../lib/api'
import { LOOP_ACTION_SOURCE_STATUSES } from '../../lib/loopStatus'

// ── A goal loop is supervised from the strip above its composer (#3824) ────────────────────────────
//
// A goal loop is a thread: its plan and cycles above, and at its foot a thin progress strip over the
// composer, as a chat has its composer. The strip says how many cycles the loop has used ("cycle 3/10",
// or "cycle 3 · ongoing" when it has no cap) and carries the loop's controls: Start, Pause or Resume,
// and Stop with its two-step confirm, each offered exactly when the action route would accept it
// (LOOP_ACTION_SOURCE_STATUSES, the mirror of the route's guard). The header keeps the rest (Rename,
// the link, Delete and Details), so Pause and Stop have one home. The composer under the strip is the
// loop's nudge: guidance for its next cycle, sent with `POST /api/loops/{id}/nudge`. The strip also
// says how the loop reports, the choice its start asked for, and changes it while the loop has cycles
// ahead (`PUT /api/loops/{id} {report}`).

const { STORE, action, nudge, update } = vi.hoisted(() => ({
  STORE: { loops: [] as Loop[] },
  action: vi.fn(),
  nudge: vi.fn(),
  update: vi.fn(),
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loops),
    uLoop: (id: string) => {
      const hit = STORE.loops.find((l) => l.id === id)
      return hit ? Promise.resolve(hit) : Promise.reject(new Error('no such loop'))
    },
    uLoopAction: (id: string, a: string) => action(id, a),
    uLoopReport: () => Promise.resolve({ report: '', log: '' }),
    artifacts: () => Promise.resolve([]),
    task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Test project' }),
    deleteULoop: () => Promise.resolve(),
    updateULoop: (id: string, body: Record<string, unknown>) => update(id, body),
    uLoopNudge: (id: string, text: string) => nudge(id, text),
  },
}))
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))

const { LoopCockpitPage } = await import('./LoopCockpitPage')

let seq = 0
function fixture(status: string, over: Partial<Loop> = {}): Loop {
  seq += 1
  return {
    id: `loop-${status}-${seq}`, kind: 'goal', name: `The ${status} loop`, task: `Something to do while ${status}`,
    execution: 'solo', agent: 'claude-code', model: 'sonnet', attended: true, max_cycles: 10, idle_secs: 60,
    success_criteria: null, status: status as Loop['status'], total_cycles: 3, error_message: null,
    created_at: 1_780_000_000, started_at: null, completed_at: null, kind_config: {}, ...over,
  } as Loop
}

async function mount(loop: Loop) {
  STORE.loops = [loop]
  render(<LoopCockpitPage id={loop.id} onBack={() => {}} query={{}} setQuery={() => {}} />)
  await waitFor(() => screen.getByRole('button', { name: 'Details' }))
  return { strip: () => screen.getByRole('toolbar', { name: 'This loop' }), header: () => document.querySelector('header') as HTMLElement }
}

const LIFECYCLE = ['Start', 'Pause', 'Resume', 'Stop', 'Stop for good?', 'Nudge']

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  STORE.loops = []
  action.mockReset()
  nudge.mockReset()
  update.mockReset()
})

describe('the strip above the composer', () => {
  it('a running loop: cycle N/M, Pause and Stop in the strip, and none of them in the header', async () => {
    // Three cycles done, so the one under way is the fourth.
    const { strip, header } = await mount(fixture('running'))
    expect(within(strip()).getByText('cycle 4/10')).toBeInTheDocument()
    expect(within(strip()).getByRole('button', { name: 'Pause' })).toBeInTheDocument()
    expect(within(strip()).getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    for (const name of LIFECYCLE) expect(within(header()).queryByRole('button', { name }), `the header has no ${name}`).toBeNull()
    expect(within(header()).getByRole('button', { name: 'Details' })).toBeInTheDocument()
  })

  it('a loop with no cap counts its cycles as ongoing', async () => {
    const { strip } = await mount(fixture('running', { max_cycles: 0 }))
    expect(within(strip()).getByText('cycle 4 · ongoing')).toBeInTheDocument()
  })

  it('Pause sends the pause action, and the strip turns to Resume', async () => {
    const loop = fixture('running')
    action.mockImplementation(() => Promise.resolve({ ...loop, status: 'paused' }))
    const { strip } = await mount(loop)
    fireEvent.click(within(strip()).getByRole('button', { name: 'Pause' }))
    expect(action).toHaveBeenCalledWith(loop.id, 'pause')
    expect(await within(strip()).findByRole('button', { name: 'Resume' })).toBeInTheDocument()
    expect(within(strip()).queryByRole('button', { name: 'Pause' })).toBeNull()
  })

  it('Stop asks first, and only the second press sends it', async () => {
    const loop = fixture('running')
    action.mockImplementation(() => Promise.resolve({ ...loop, status: 'stopped' }))
    const { strip } = await mount(loop)
    fireEvent.click(within(strip()).getByRole('button', { name: 'Stop' }))
    expect(action).not.toHaveBeenCalled()
    fireEvent.click(within(strip()).getByRole('button', { name: 'Stop for good?' }))
    expect(action).toHaveBeenCalledWith(loop.id, 'stop')
  })

  it.each(['ready', 'review', 'running', 'paused', 'blocked', 'needs_input', 'failed', 'stagnant', 'complete', 'stopped'])(
    'a %s loop is offered exactly the actions the route accepts',
    async (status) => {
      const { strip } = await mount(fixture(status))
      const offered = ['Start', 'Pause', 'Resume', 'Stop'].filter((name) => within(strip()).queryByRole('button', { name }))
      const accepted = (['start', 'pause', 'resume', 'stop'] as const)
        .filter((a) => LOOP_ACTION_SOURCE_STATUSES[a].has(status as Loop['status']))
        .map((a) => a[0].toUpperCase() + a.slice(1))
      expect(offered).toEqual(accepted)
    },
  )
})

describe('the loop\'s mode, at the foot of its thread', () => {
  // A chat says its permission mode in its composer's footer (#3808); a loop's composer says its own.
  it.each([[true, 'Attended'], [false, 'Unattended']] as const)('attended %s reads %s in the strip', async (attended, label) => {
    const { strip } = await mount(fixture('running', { attended }))
    const said = within(strip()).getByText(label)
    expect(said.getAttribute('title'), 'what the mode means, on the word').toMatch(new RegExp(`^${label}:`))
  })
})

describe('how the loop reports, in the strip', () => {
  it('shows the choice the loop holds, and a change is saved on the loop', async () => {
    const loop = fixture('running', { report: 'as_it_goes' })
    update.mockImplementation((_id: string, body: Record<string, unknown>) => Promise.resolve({ ...loop, ...body }))
    const { strip } = await mount(loop)
    fireEvent.click(within(strip()).getByRole('button', { name: 'Reports: As it goes' }))
    fireEvent.click(await screen.findByRole('menuitemradio', { name: /Only if it gets stuck/ }))
    await waitFor(() => expect(update).toHaveBeenCalledWith(loop.id, { report: 'if_stuck' }))
    expect(await within(strip()).findByRole('button', { name: 'Reports: Only if it gets stuck' })).toBeInTheDocument()
  })

  it('a loop from a gateway that predates the choice reads as the default', async () => {
    const { strip } = await mount(fixture('ready'))
    expect(within(strip()).getByRole('button', { name: 'Reports: Only when it’s done' })).toBeInTheDocument()
  })

  it('a refused change keeps the choice the loop holds', async () => {
    const loop = fixture('paused', { report: 'when_done' })
    update.mockRejectedValue(new Error('refused'))
    const { strip } = await mount(loop)
    fireEvent.click(within(strip()).getByRole('button', { name: 'Reports: Only when it’s done' }))
    fireEvent.click(await screen.findByRole('menuitemradio', { name: /As it goes/ }))
    await waitFor(() => expect(update).toHaveBeenCalled())
    expect(within(strip()).getByRole('button', { name: 'Reports: Only when it’s done' })).toBeInTheDocument()
  })

  it.each(['complete', 'stopped'])('a %s loop has nothing left to report, so it is not asked', async (status) => {
    const { strip } = await mount(fixture(status))
    expect(within(strip()).queryByRole('button', { name: /^Reports:/ })).toBeNull()
  })

  it('a general loop reports as its workflow run does, so the strip does not ask it', async () => {
    const { strip } = await mount(fixture('running', { kind: 'general' }))
    expect(within(strip()).queryByRole('button', { name: /^Reports:/ })).toBeNull()
  })
})

describe('the composer under it is the loop\'s nudge', () => {
  it('a running loop takes guidance for its next cycle there, without opening anything first', async () => {
    const loop = fixture('running')
    nudge.mockResolvedValue(undefined)
    await mount(loop)
    const box = screen.getByRole('textbox', { name: 'Nudge this loop' })
    fireEvent.change(box, { target: { value: 'Look at the supplier list first.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send nudge' }))
    await waitFor(() => expect(nudge).toHaveBeenCalledWith(loop.id, 'Look at the supplier list first.'))
    await waitFor(() => expect(box).toHaveValue(''))
  })

  it('a finished loop has no composer: there is no next cycle to guide', async () => {
    await mount(fixture('complete'))
    expect(screen.queryByRole('textbox', { name: 'Nudge this loop' })).toBeNull()
  })
})
