import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PendingApproval } from '../../lib/api'

// ── A room member's call that asks is answered in the room it was asked from ─────────────────────
//
// The round now asks you through the approval registry, so a member's call is listed where every
// approval is. Its session is the member's own (`room:<room>:<member>`), no chat anyone opened, so
// the room has to claim it out of `/api/approvals` itself, or the room would read "Answering:
// talk-editor" with nothing on screen to answer.
//
// 🪤 THE FILTER IS THE WHOLE MECHANISM. `/api/approvals` is global: a block that rendered every
// pending approval would pass the positive leg while showing one room another room's question, so
// the other-room and chat legs below are load-bearing.

const approvals = vi.fn<() => Promise<PendingApproval[]>>()
const resolveApproval = vi.fn<(id: string, action: 'approve' | 'reject') => Promise<{ ok: boolean }>>()

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, approvals, resolveApproval } }
})

const ROOM = 'is-the-live-crash-demo-worth-the-risk'

const approval = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  id: `room:${ROOM}:talk-editor:c1`, request_id: `room:${ROOM}:talk-editor:c1`, source: 'room',
  tool: 'edit_file', tool_input: '{"path": "~/Notes/Talks/incident-notes.md"}',
  session: `room:${ROOM}:talk-editor`, ts: 0, session_title: '', agent: '', risk: 'caution',
  grant_agent: '', source_label: 'room “Is the live crash demo worth the risk?” · member “talk-editor”',
  ...over,
})

async function mountFor(roomId: string, queue: PendingApproval[], onCount?: (n: number) => void) {
  approvals.mockReset(); resolveApproval.mockReset()
  approvals.mockResolvedValue(queue)
  resolveApproval.mockResolvedValue({ ok: true })
  const { RoomApprovals } = await import('./RoomApprovals')
  await act(async () => {
    render(<RoomApprovals roomId={roomId} onCount={onCount} />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

beforeEach(() => { cleanup() })

describe("a room member's call that asks is answered in its room", () => {
  it('shows the call with Allow and Deny, and who in which room asked', async () => {
    await mountFor(ROOM, [approval()])
    expect(await screen.findByRole('button', { name: /^Allow edit_file/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Deny edit_file/ })).toBeTruthy()
    expect(screen.getAllByText(/member “talk-editor”/).length).toBeGreaterThan(0)
  })

  it('Allow resolves that approval', async () => {
    await mountFor(ROOM, [approval()])
    await userEvent.click(await screen.findByRole('button', { name: /^Allow edit_file/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledWith(`room:${ROOM}:talk-editor:c1`, 'approve'))
  })

  it('Deny resolves it the other way', async () => {
    await mountFor(ROOM, [approval()])
    await userEvent.click(await screen.findByRole('button', { name: /^Deny edit_file/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledWith(`room:${ROOM}:talk-editor:c1`, 'reject'))
  })

  it('tells the room how many are waiting, so it can keep the newest in view', async () => {
    const counts: number[] = []
    await mountFor(ROOM, [approval()], (n) => counts.push(n))
    await waitFor(() => expect(counts.at(-1)).toBe(1))
  })
})

describe('🪤 VACUITY: it claims ONLY its own members\' asks', () => {
  it("another room's member's ask is not shown here", async () => {
    await mountFor(ROOM, [approval({ session: 'room:another-room:talk-editor' })])
    await waitFor(() => expect(approvals).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /^Allow / })).toBeNull()
  })

  it("a chat's ask is not shown here, even one whose key starts with the word", async () => {
    await mountFor(ROOM, [approval({ session: 'room-planning' }), approval({ id: 'x', session: `room:${ROOM}` })])
    await waitFor(() => expect(approvals).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /^Allow / })).toBeNull()
  })

  it('an empty queue renders nothing at all', async () => {
    await mountFor(ROOM, [])
    await waitFor(() => expect(approvals).toHaveBeenCalled())
    expect(document.body.querySelectorAll('button')).toHaveLength(0)
  })
})
