import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop, PendingApproval } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'
import { approvalDestination, loopApprovalOf } from '../../app/approvalDestination'

// ── An Attended loop's workers ask before they act, and the loop's own page answers them ─────────
//
// Measured: an Attended code loop ran about 55 shell commands in one cycle and asked for none. Now
// its workers ask through the chat's approval path, and a worker's session is no chat anyone
// opened, so the loop's page carries the card, the nudge and the Inbox row lead there, and the
// card's scopes say what they do for a loop: "This loop" lets every worker of this run act without
// asking until the run ends.

const { STORE, approve } = vi.hoisted(() => ({
  STORE: { loop: null as Loop | null, pending: [] as PendingApproval[] },
  approve: vi.fn(),
}))
let socket: ((m: WsMessage) => void) | null = null

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoop: () => (STORE.loop ? Promise.resolve(STORE.loop) : Promise.reject(new Error('no such loop'))),
    uLoopReport: () => Promise.resolve({ report: '', log: '' }),
    artifacts: () => Promise.resolve([]),
    task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Feeds' }),
    approvals: () => Promise.resolve(STORE.pending),
    approve: (...args: unknown[]) => approve(...args),
  },
}))
vi.mock('../../lib/useChatSocket', () => ({
  useChatSocket: (cb: (m: WsMessage) => void) => { socket = cb },
}))
// jsdom has no EventSource, and a live stream is not what this measures.
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))

const { LoopApprovals } = await import('./LoopApprovals')
const { LoopCockpitPage } = await import('./LoopCockpitPage')

const LOOP_ID = '0a1b2c3d'

function ask(over: Partial<PendingApproval> = {}): PendingApproval {
  return {
    id: `chat:loop-${LOOP_ID}:req-1`, request_id: 'req-1', source: '', tool: 'bash',
    tool_input: '{"command": "sed -i s/escape/plain/ src/feedsmith/digest.py"}', tool_purpose: '',
    session: `loop-${LOOP_ID}`, ts: 0, session_title: '', agent: 'personalclaw-coder',
    risk: 'caution', grant_agent: '', ...over,
  }
}

function attendedLoop(): Loop {
  return {
    id: LOOP_ID, kind: 'goal', name: 'Digest titles', task: 'Stop escaping digest titles twice',
    execution: 'solo', agent: 'default', model: '', attended: true, max_cycles: 30, idle_secs: 60,
    success_criteria: null, status: 'running', total_cycles: 1, error_message: null,
    created_at: 1_780_000_000, started_at: null, completed_at: null, kind_config: {},
  } as Loop
}

const cards = () => screen.queryAllByText('Permission needed')

beforeEach(() => {
  invalidateKeys('loop:', true)
  STORE.loop = null
  STORE.pending = []
  approve.mockReset()
  approve.mockResolvedValue({ ok: true })
  socket = null
})
afterEach(() => cleanup())

describe('where a loop worker’s ask is answered', () => {
  it('the nudge, the Inbox row and the link lead to the loop, for every worker of it and its planner', () => {
    for (const session of [`loop-${LOOP_ID}`, `loop-${LOOP_ID}-t-1a2b3c4d`, `loop-plan-${LOOP_ID}`]) {
      const dest = approvalDestination(session)
      expect(dest.href, session).toBe(`#/loops/${LOOP_ID}`)
      expect(dest.linkLabel, session).toBe('Open the loop')
      expect(loopApprovalOf(session, LOOP_ID), session).toBe(true)
    }
  })

  it('🪤 VACUITY: a chat and another loop keep their own places', () => {
    expect(approvalDestination('main').href).toBe('#/chat/main')
    expect(approvalDestination('loop-plan-notaloop').href).toBe('#/chat/loop-plan-notaloop')
    expect(loopApprovalOf('loop-99999999', LOOP_ID)).toBe(false)
    expect(loopApprovalOf('loop-plan-99999999', LOOP_ID)).toBe(false)
  })

  it('the nudge says a loop is asking, and links to it', async () => {
    let onMessage: ((m: WsMessage) => void) | null = null
    vi.resetModules()
    vi.doMock('../../lib/useChatSocket', () => ({
      useChatSocket: (cb: (m: WsMessage) => void) => { onMessage = cb },
    }))
    const { useApprovalToasts } = await import('../../app/useApprovalToasts')
    const toasts: Array<{ message: string; href: string }> = []
    const onToast = (e: Event) => {
      const d = (e as CustomEvent).detail || {}
      toasts.push({ message: String(d.message ?? ''), href: String(d.href ?? '') })
    }
    window.addEventListener('ne:toast', onToast)
    try {
      renderHook(() => useApprovalToasts(''))
      act(() => onMessage!({ type: 'approval', data: { session: `loop-${LOOP_ID}-t-1a2b3c4d`, id: 'ap-1', tool: 'bash' } }))
    } finally {
      window.removeEventListener('ne:toast', onToast)
    }
    expect(toasts).toHaveLength(1)
    expect(toasts[0].message).toMatch(/^A loop needs approval to run bash/)
    expect(toasts[0].message).toContain(`open loop ${LOOP_ID} to respond.`)
    expect(toasts[0].href).toBe(`#/loops/${LOOP_ID}`)
  })
})

describe('the loop’s page answers its workers’ asks', () => {
  it('shows every worker’s ask of this loop, and nobody else’s', async () => {
    STORE.pending = [
      ask(),
      ask({ id: 'x2', request_id: 'req-2', session: `loop-${LOOP_ID}-t-1a2b3c4d`, tool: 'edit_file' }),
      ask({ id: 'x3', session: 'loop-99999999' }),
      ask({ id: 'x4', session: 'main' }),
    ]
    render(<LoopApprovals loopId={LOOP_ID} />)

    await waitFor(() => expect(cards()).toHaveLength(2))
    expect(screen.getByRole('button', { name: /^Allow bash/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Allow edit_file/ })).toBeTruthy()
  })

  it('“This loop” says how far it reaches, and posts the grant to the asking worker', async () => {
    STORE.pending = [ask({ session: `loop-${LOOP_ID}-t-1a2b3c4d`, request_id: 'req-7' })]
    render(<LoopApprovals loopId={LOOP_ID} />)

    fireEvent.click(await screen.findByRole('radio', { name: 'This loop' }))
    expect(screen.getByText(/^Every worker of this loop, and its planner, runs its tools without asking until this run ends/)).toBeTruthy()
    expect(screen.queryByRole('radio', { name: 'This chat' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Allow bash — this loop/ }))

    await waitFor(() => expect(approve).toHaveBeenCalledWith(`loop-${LOOP_ID}-t-1a2b3c4d`, 'trust', 'req-7'))
  })

  it('Deny answers once, and an answered ask leaves the page', async () => {
    STORE.pending = [ask()]
    render(<LoopApprovals loopId={LOOP_ID} />)
    fireEvent.click(await screen.findByRole('button', { name: /^Deny bash/ }))
    await waitFor(() => expect(approve).toHaveBeenCalledWith(`loop-${LOOP_ID}`, 'rejected', 'req-1'))

    STORE.pending = []
    await act(async () => { socket!({ type: 'approval_resolved', data: {} } as WsMessage) })
    await waitFor(() => expect(cards()).toHaveLength(0))
  })

  it('the loop cockpit carries the card, so the place the nudge sends you can answer', async () => {
    STORE.loop = attendedLoop()
    STORE.pending = [ask()]
    render(<LoopCockpitPage id={LOOP_ID} onBack={() => {}} query={{}} setQuery={() => {}} />)

    expect(await screen.findByRole('button', { name: /^Allow bash/ })).toBeTruthy()
    expect(screen.getByRole('group', { name: 'Waiting on your approval' })).toBeTruthy()
  })
})
