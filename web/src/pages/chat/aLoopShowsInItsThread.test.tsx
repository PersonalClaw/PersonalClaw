import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { api, type Loop } from '../../lib/api'
import { OpenBesideProvider, BesideRegistry } from '../../ui/OpenBeside'
import { LOOP_ACTION_SOURCE_STATUSES, RUN_LOOP_ACTION_SOURCE_STATUSES } from '../../lib/loopStatus'
import { SdlcProgressCard, liveLoopCards, type SdlcRef } from './SdlcProgressCard'
import { detailPane } from './beside/detailBeside'
import { loopMatrix, matrixSummary, MATRIX_CYCLES } from '../loops/LoopMatrix'
import type { ChatTurn, Segment } from './chatTypes'

// ── A loop the chat started shows in its thread as one compact card, and beside it as a tile ──────
//
// #3824's amendment: a running goal loop appears in its thread as one compact live card (its name
// and kind, elapsed time and agent count, a phase matrix whose cells carry their states in words,
// and a disclosure that opens the detail tile), and the tile is a live supervision surface beside
// the conversation: the loop's strip with its Stop as an action of its own, its matrix, its steps
// and cycles. Closing the tile stops nothing; only Stop does. A loop the agent created, started and
// checked on is one card, where the chat first names it.

const REF: SdlcRef = { kind: 'loop', id: 'feed01', created: false }
const NAME = 'Watch the soil delivery'

function loop(over: Partial<Loop> = {}): Loop {
  return {
    id: REF.id, kind: 'goal', name: NAME, task: NAME, execution: 'solo', agent: 'claude-code', model: 'sonnet',
    attended: true, max_cycles: 10, idle_secs: 60, success_criteria: null, status: 'running', total_cycles: 3,
    error_message: null, created_at: 0, started_at: 1, completed_at: null, elapsed_seconds: 125, kind_config: { goal_type: 'monitor' },
    findings: [], ...over,
  } as unknown as Loop
}

beforeEach(() => { vi.useRealTimers() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('the phase matrix says every state in words', () => {
  const cycles = (l: Loop) => loopMatrix(l, l.status, { steps: [] })
  it('a running loop: its done cycles, the one under way, and the ones its cap leaves', () => {
    const m = cycles(loop())
    expect(m.unit).toBe('cycles')
    expect(m.cells.map((c) => c.state)).toEqual(['done', 'done', 'done', 'running', ...Array(6).fill('pending')])
    expect(matrixSummary(m)).toBe('3 done · 1 running · 6 to go')
  })
  it('a loop waiting on you is stalled at its cycle, and a failed one failed at it', () => {
    expect(matrixSummary(cycles(loop({ status: 'needs_input' } as Partial<Loop>)))).toBe('3 done · 1 stalled · 6 to go')
    expect(matrixSummary(cycles(loop({ status: 'failed' } as Partial<Loop>)))).toBe('3 done · 1 failed · 6 to go')
    expect(matrixSummary(cycles(loop({ status: 'paused' } as Partial<Loop>)))).toBe('3 done · 1 paused · 6 to go')
  })
  it('a finished loop is its done cycles alone: a stopped loop\'s unrun cycles never run', () => {
    expect(matrixSummary(cycles(loop({ status: 'complete' } as Partial<Loop>)))).toBe('3 done')
    expect(matrixSummary(cycles(loop({ status: 'stopped' } as Partial<Loop>)))).toBe('3 done')
  })
  it('a long loop with no cap draws its latest cycles and counts the earlier ones', () => {
    const m = cycles(loop({ max_cycles: 0, total_cycles: 30 } as Partial<Loop>))
    expect(m.cells).toHaveLength(MATRIX_CYCLES)
    expect(m.cells.at(-1)).toEqual({ label: 'Cycle 31', state: 'running' })
    expect(m.earlier).toBe(31 - MATRIX_CYCLES)
    expect(matrixSummary(m)).toBe('30 done · 1 running')
  })
  it('a loop whose stages are written gets a cell per stage', () => {
    const m = loopMatrix(loop({ status: 'blocked' } as Partial<Loop>), 'blocked', {
      steps: [{ label: 'Plan', state: 'done', key: 'a' }, { label: 'Build', state: 'active', key: 'b' }, { label: 'Ship', state: 'todo', key: 'c' }],
    })
    expect(m.unit).toBe('stages')
    expect(m.cells).toEqual([{ label: 'Plan', state: 'done' }, { label: 'Build', state: 'stalled' }, { label: 'Ship', state: 'pending' }])
  })
})

describe('the card in the thread', () => {
  it('names the loop, its kind, cycle, elapsed time and agents, and each cell\'s state', async () => {
    vi.spyOn(api, 'uLoop').mockResolvedValue(loop())
    render(<SdlcProgressCard refObj={REF} />)
    const card = await screen.findByRole('group', { name: `${NAME}, Goal Loop` })
    await waitFor(() => expect(within(card).getByText(/cycle 4\/10/)).toBeTruthy())
    expect(within(card).getByText('Goal Loop · Monitoring · cycle 4/10 · 2m · 1 agent')).toBeTruthy()
    const cells = within(card).getByRole('list', { name: 'Cycles' })
    expect(within(cells).getByText('Cycle 4: running')).toBeTruthy()
    expect(within(cells).getByText('Cycle 5: to go')).toBeTruthy()
    expect(within(card).getByText('3 done · 1 running · 6 to go')).toBeTruthy()
  })

  it('a roster\'s loop counts its agents', async () => {
    vi.spyOn(api, 'uLoop').mockResolvedValue(loop({ execution: 'multi_agent', roster: [{}, {}, {}] } as unknown as Partial<Loop>))
    render(<SdlcProgressCard refObj={REF} />)
    expect(await screen.findByText(/· 3 agents$/)).toBeTruthy()
  })

  it('its Details opens the loop beside the conversation, where the chat gives it a place', async () => {
    vi.spyOn(api, 'uLoop').mockResolvedValue(loop())
    const open = vi.fn()
    render(<OpenBesideProvider open={open} registry={new BesideRegistry()}><SdlcProgressCard refObj={REF} /></OpenBesideProvider>)
    fireEvent.click(await screen.findByRole('button', { name: `Open ${NAME}'s details beside the conversation` }))
    expect(open).toHaveBeenCalledWith(`loop:${REF.id}`)
    // Its only button: the card still changes nothing about the loop (`sdlcCardChangesNothing`).
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })

  it('inside the tile, it does not offer to open the tile again', async () => {
    vi.spyOn(api, 'uLoop').mockResolvedValue(loop())
    render(<OpenBesideProvider open={() => {}} registry={new BesideRegistry()}><SdlcProgressCard refObj={REF} inPane /></OpenBesideProvider>)
    await screen.findByText(NAME)
    expect(screen.queryByRole('button', { name: /details beside/ })).toBeNull()
  })
})

describe('one card per loop', () => {
  const tool = (id: string, tool: string, output: string): Segment => ({ kind: 'tool', id, tool, done: true, output } as unknown as Segment)
  it('a loop created, started and checked on is one card, where the chat first names it', () => {
    const create = tool('a', 'project_run_create', 'Created Goal Loop draft `feed01`. Open: /#/loops/feed01 .')
    const start = tool('b', 'project_run_start', 'Started loop `feed01`. Watch progress at /#/loops/feed01')
    const other = tool('c', 'project_run_create', 'Created Goal Loop draft `beef02`. Open: /#/loops/beef02 .')
    const status = tool('d', 'project_run_status', 'Loop `feed01` is running. /#/loops/feed01')
    const turns = [{ segments: [create, start] }, { segments: [other, status] }] as unknown as ChatTurn[]
    expect([...liveLoopCards(turns)]).toEqual([create, other])
  })
})

describe('the tile beside the conversation', () => {
  const pane = () => detailPane({ address: `loop:${REF.id}`, outputs: [], subagents: [], ready: true, open: () => {}, onClose: () => {} })

  it('is a loop\'s, at its own address', () => {
    expect(pane()).toMatchObject({ id: `loop:${REF.id}`, label: 'loop', title: 'Loop' })
  })

  it('carries the loop\'s strip: Stop asks first, and only the second press sends it', async () => {
    const read = vi.spyOn(api, 'uLoop').mockResolvedValue(loop())
    const action = vi.spyOn(api, 'uLoopAction').mockImplementation(async () => {
      // The press restarts the tile's read, which this mock would answer "running" and so replace
      // the press's own answer whenever it landed first. That read waits, so what shows is the
      // press's answer.
      read.mockReturnValue(new Promise(() => {}))
      return loop({ status: 'stopped' } as Partial<Loop>)
    })
    render(<>{pane()!.body}</>)
    const strip = await screen.findByRole('toolbar', { name: 'This loop' })
    expect(within(strip).getByRole('button', { name: 'Pause' })).toBeTruthy()
    fireEvent.click(within(strip).getByRole('button', { name: 'Stop' }))
    expect(action).not.toHaveBeenCalled()
    fireEvent.click(within(strip).getByRole('button', { name: 'Stop for good?' }))
    await waitFor(() => expect(action).toHaveBeenCalledWith(REF.id, 'stop'))
    expect(await screen.findByText('Stopped')).toBeTruthy()
  })

  it('closing it stops nothing', async () => {
    vi.spyOn(api, 'uLoop').mockResolvedValue(loop())
    const action = vi.spyOn(api, 'uLoopAction')
    const { unmount } = render(<>{pane()!.body}</>)
    await screen.findByRole('toolbar', { name: 'This loop' })
    unmount()
    expect(action).not.toHaveBeenCalled()
  })

  it('a run-backed loop is offered what its run\'s route accepts', async () => {
    vi.spyOn(api, 'uLoop').mockResolvedValue(loop({ kind: 'general', run_id: 'r1', status: 'needs_input' } as Partial<Loop>))
    render(<>{pane()!.body}</>)
    const strip = await screen.findByRole('toolbar', { name: 'This loop' })
    const offered = ['Start', 'Pause', 'Resume', 'Stop'].filter((name) => within(strip).queryByRole('button', { name }))
    const accepted = (['start', 'pause', 'resume', 'stop'] as const).filter((a) => RUN_LOOP_ACTION_SOURCE_STATUSES[a].has('needs_input'))
    expect(offered).toEqual(accepted.map((a) => a[0].toUpperCase() + a.slice(1)))
    // Planted against the plain map: the two disagree here, so the test tells them apart.
    expect(accepted).not.toEqual((['start', 'pause', 'resume', 'stop'] as const).filter((a) => LOOP_ACTION_SOURCE_STATUSES[a].has('needs_input')))
    // A run reports as a run does: the strip does not ask it.
    expect(within(strip).queryByRole('button', { name: /^Reports:/ })).toBeNull()
  })

  it('a goal loop\'s report choice is changed there too', async () => {
    vi.spyOn(api, 'uLoop').mockResolvedValue(loop({ report: 'as_it_goes' } as Partial<Loop>))
    const update = vi.spyOn(api, 'updateULoop').mockResolvedValue(loop({ report: 'if_stuck' } as Partial<Loop>))
    render(<>{pane()!.body}</>)
    fireEvent.click(await screen.findByRole('button', { name: 'Reports: As it goes' }))
    fireEvent.click(await screen.findByRole('menuitemradio', { name: 'Only if it gets stuck' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith(REF.id, { report: 'if_stuck' }))
  })

  it('lists its latest cycles, newest first, and links to its page', async () => {
    vi.spyOn(api, 'uLoop').mockResolvedValue(loop({
      findings: [{ cycle: 1, summary: 'Checked the tracker', ts: 1 }, { cycle: 2, summary: 'Still in transit', ts: 2 }],
    } as unknown as Partial<Loop>))
    render(<>{pane()!.body}</>)
    const cycles = await screen.findByRole('group', { name: 'Its latest cycles' })
    expect(within(cycles).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Cycle 2Still in transit', 'Cycle 1Checked the tracker'])
    expect(screen.getByRole('link', { name: `Open ${NAME}'s page` }).getAttribute('href')).toBe(`#/loops/${REF.id}`)
  })
})
