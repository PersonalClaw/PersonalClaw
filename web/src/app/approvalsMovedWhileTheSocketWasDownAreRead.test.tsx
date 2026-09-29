import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, renderHook, screen } from '@testing-library/react'
import type { InboxItem, InboxStatus, PendingApproval } from '../lib/api'
import type { WsMessage } from '../lib/useChatSocket'

// ── What moved in the approval queue while the socket was down is read when it comes back ─────────
//
// A restart drops every socket. The run it resumes asks for its step's approval at once, while the
// pages are still reconnecting, so the `approval` frame goes to nobody. The surfaces that answer an
// approval read the queue on frames only: the run page open across the restart showed no Approve,
// the out-of-context nudge never came, and an approval answered elsewhere during the outage still
// offered Approve and Deny. These drive the tab's real shared socket through a drop and a reopen.

const approvals = vi.fn<() => Promise<PendingApproval[]>>()
const inbox = vi.fn<() => Promise<InboxItem[]>>()
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      approvals,
      inbox,
      inboxStatus: () => Promise.resolve({
        enabled: true, open_count: 0, total_count: 0, health: { running: true }, sources: [], watched_channels: [],
      } as InboxStatus),
      markInboxSeen: () => Promise.resolve({ ok: true, seen: 0 }),
      openInboxItem: () => Promise.resolve({ ok: true }),
    },
  }
})
vi.mock('../pages/inbox/InboxDetail', () => ({ InboxDetail: () => null }))
vi.mock('../pages/inbox/InboxSettingsPanel', () => ({ InboxSettingsPanel: () => null }))
vi.mock('../pages/inbox/ProposalsLens', () => ({ ProposalsLens: () => null }))
vi.mock('../pages/inbox/TriageDigestCard', () => ({ TriageDigestCard: () => null }))

class FakeSocket {
  static all: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeSocket.all.push(this) }
  close(): void { this.closed = true }
  open(): void { act(() => { this.onopen?.() }) }
  frame(m: WsMessage): void { act(() => { this.onmessage?.({ data: JSON.stringify(m) }) }) }
  drop(): void { act(() => { this.onclose?.() }) }
}
const latest = () => FakeSocket.all[FakeSocket.all.length - 1]

const asked: PendingApproval = {
  id: 'spawn:5c0ffee1', source: 'subagent',
  tool: 'subagent_run(Judge the draft in two sentences.)', tool_purpose: '',
  session: 'workflow:df5827ca:judge', ts: 0, request_id: 'spawn:5c0ffee1',
  session_title: '', agent: '', risk: '', grant_agent: '',
}

// The approval's Inbox row, raised with it.
const row = {
  id: 'inb-5c0ffee1', status: 'pending', item_kind: 'agent_request',
  channel: 'system', channel_name: 'system', thread_ts: null,
  message: 'Approval needed: subagent_run\n\nA workflow step is waiting for your decision.',
  sender_id: 'system', sender_name: 'system', classification: 'needs_reply', confidence: 'high',
  draft: '', created_at: 1000, source: 'system', can_reply: false,
  refs: { approval: asked.id, session: asked.session },
} as unknown as InboxItem

/** Let the reads the reopen started land, and the renders they cause. */
const settle = () => act(async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve() })

/** The gateway goes away and comes back: the socket drops, and its reconnect opens after the
 *  backoff. `meanwhile` is what the new process did before this tab was listening again. */
async function restart(meanwhile: () => void) {
  latest().drop()
  meanwhile()
  await act(async () => { vi.advanceTimersByTime(1_000) })
  expect(FakeSocket.all.length, 'the tab never reconnected').toBeGreaterThan(1)
  latest().open()
  await settle()
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  approvals.mockReset()
  inbox.mockReset()
})
afterEach(async () => {
  cleanup()
  await act(async () => { await Promise.resolve() })  // the last consumer's socket closes
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('an approval raised while the socket was down', () => {
  it('appears, answerable, on the run page that was open across the restart', async () => {
    approvals.mockResolvedValue([])
    const { RunToolApprovals } = await import('../pages/workflows/RunToolApprovals')
    render(<RunToolApprovals runId="df5827ca" />)
    latest().open()
    await settle()
    expect(screen.queryByRole('button', { name: /Approve: subagent_run/ })).toBeNull()

    await restart(() => approvals.mockResolvedValue([asked]))

    expect(
      screen.queryByRole('button', { name: /Approve: subagent_run/ }),
      'the run page shows no Approve for the approval its resumed step raised',
    ).toBeTruthy()
  })

  it('is nudged on a page that does not answer it', async () => {
    approvals.mockResolvedValue([])
    const toasts: string[] = []
    const onToast = (e: Event) => toasts.push(String((e as CustomEvent).detail?.message ?? ''))
    window.addEventListener('ne:toast', onToast)
    try {
      const { useApprovalToasts } = await import('./useApprovalToasts')
      renderHook(() => useApprovalToasts(''))
      latest().open()
      await settle()

      await restart(() => approvals.mockResolvedValue([asked]))
      expect(toasts, 'no nudge for the approval raised during the reconnect').toHaveLength(1)
      expect(toasts[0]).toMatch(/subagent_run/)

      // Its frame can still arrive (a re-broadcast, another reconnect): one nudge per approval.
      latest().frame({ type: 'approval', data: asked as unknown as Record<string, unknown> })
      await restart(() => {})
      expect(toasts).toHaveLength(1)
    } finally {
      window.removeEventListener('ne:toast', onToast)
    }
  })

  it("is on the page of the loop whose worker asked", async () => {
    const workerAsked: PendingApproval = {
      ...asked, id: 'loop-0c0ffee1:7', request_id: '7', source: '',
      tool: 'write_file', session: 'loop-0c0ffee1',
    }
    approvals.mockResolvedValue([])
    const { LoopApprovals } = await import('../pages/loops/LoopApprovals')
    render(<LoopApprovals loopId="0c0ffee1" />)
    latest().open()
    await settle()
    expect(screen.queryByRole('group', { name: 'Waiting on your approval' })).toBeNull()

    await restart(() => approvals.mockResolvedValue([workerAsked]))

    expect(screen.queryByRole('group', { name: 'Waiting on your approval' }),
      "the loop's page does not show the ask its worker raised during the reconnect").toBeTruthy()
  })

  it('has its row in an Inbox that was open across the restart', async () => {
    approvals.mockResolvedValue([])
    inbox.mockResolvedValue([])
    const { InboxPage } = await import('../pages/inbox/InboxPage')
    render(<InboxPage query={{}} setQuery={() => {}} navigate={() => {}} />)
    latest().open()
    await settle()
    expect(screen.queryByText(/Approval needed: subagent_run/)).toBeNull()

    await restart(() => { approvals.mockResolvedValue([asked]); inbox.mockResolvedValue([row]) })

    expect(screen.queryByText(/Approval needed: subagent_run/),
      'the open Inbox does not list the approval raised during the reconnect').toBeTruthy()
  })
})

describe('an approval answered while the socket was down', () => {
  it('stops offering Approve and Deny', async () => {
    approvals.mockResolvedValue([asked])
    const { ApprovalDecision } = await import('./ApprovalDecision')
    render(<ApprovalDecision approvalId={asked.id} />)
    latest().open()
    await settle()
    expect(screen.queryByRole('button', { name: /Approve: subagent_run/ })).toBeTruthy()

    await restart(() => approvals.mockResolvedValue([]))

    expect(screen.queryByRole('button', { name: /Approve: subagent_run/ }),
      'an approval answered elsewhere still offers Approve').toBeNull()
    expect(screen.getByText(/Nothing is waiting on this any more/)).toBeTruthy()
  })
})
