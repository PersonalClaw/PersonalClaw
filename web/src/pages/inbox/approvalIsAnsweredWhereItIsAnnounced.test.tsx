/** An approval is answered on the surfaces that announce it: its Inbox row and that row's note.
 *
 *  A trigger's run asked to start a subagent, and the ask reached the Inbox ("Approval needed:
 *  subagent_run(…) — The trigger “Morning brief” is waiting for your decision on subagent_run(…)")
 *  and the bell. The row offered Investigate in chat, Mark handled and Dismiss; the note offered
 *  Mark read and Delete. None of them answers an approval, and neither named where to: a trigger's
 *  run asks with no chat behind it, so only Home › To triage carried the verbs. The run waited
 *  21 minutes while its owner read about it twice.
 *
 *  Both surfaces now carry Allow and Deny, through the decision path every surface outside a
 *  chat uses, and read the approval LIVE — so a row that outlived its approval says so instead of
 *  offering a decision nothing is waiting on. */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { InboxItem, NotificationItem, PendingApproval } from '../../lib/api'

const { API } = vi.hoisted(() => ({
  API: {
    approvals: vi.fn(),
    resolveApproval: vi.fn(),
    notifications: vi.fn(),
    ackNotification: vi.fn(),
    autonomyLadder: vi.fn(),
    updateInboxItem: vi.fn(),
    restoreInboxItem: vi.fn(),
    favoriteInboxItem: vi.fn(),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))

const { InboxDetail } = await import('./InboxDetail')
const { NotificationsPage } = await import('../notifications/NotificationsPage')

const ID = 'spawn:0c8db356'
const ASKS = 'The trigger “Morning brief” is waiting for your decision on subagent_run (risk: high).'

/** The registry entry a trigger's run raises when it asks to start a subagent. No `session`: a
 *  trigger's run has no chat behind it. */
const PENDING = {
  id: ID, request_id: ID, source: 'subagent', tool: 'subagent_run', session: '', ts: 1,
  session_title: '', agent: '', risk: 'high', grant_agent: '', trigger: 'schedule:clock:morning-brief',
  trigger_name: 'Morning brief', asked_by: 'trigger:schedule:clock:morning-brief',
  source_label: 'trigger “Morning brief”',
} as PendingApproval

/** Its Inbox row, as `DashboardApprovalState._raise_inbox_row` writes it. */
const ROW = {
  id: 'agent_request_1', channel: 'system', channel_name: 'system',
  message: `Approval needed: subagent_run\n\n${ASKS}`, sender_id: 'system', sender_name: 'system',
  item_kind: 'agent_request', classification: 'needs_reply', confidence: 'high', status: 'pending',
  refs: { approval: ID },
} as InboxItem

/** That row's one notification, which carries the row's refs. */
const NOTE: NotificationItem = {
  kind: 'agent_request', title: 'Approval needed: subagent_run', body: ASKS,
  ts: '2026-09-29T07:42:15+00:00', acked: false, approval: ID, inbox_item: ROW.id,
}

beforeEach(() => {
  sessionStorage.clear()
  for (const fn of Object.values(API)) fn.mockReset()
  API.approvals.mockResolvedValue([PENDING])
  API.resolveApproval.mockResolvedValue({ ok: true })
  API.notifications.mockResolvedValue({ notifications: [NOTE] })
  API.ackNotification.mockResolvedValue({ ok: true })
  API.autonomyLadder.mockRejectedValue(new Error('no ladder in this test'))
})

describe('the Inbox row that announces an approval', () => {
  it('approves it there', async () => {
    const onChanged = vi.fn()
    render(<InboxDetail item={ROW} onChanged={onChanged} navigate={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: /^Allow subagent_run/ }))
    await waitFor(() => expect(API.resolveApproval).toHaveBeenCalledWith(ID, 'approve'))
    expect(await screen.findByText(/^Approved\./)).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalled()
  })

  it('denies it there', async () => {
    render(<InboxDetail item={ROW} onChanged={() => {}} navigate={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: /^Deny subagent_run/ }))
    await waitFor(() => expect(API.resolveApproval).toHaveBeenCalledWith(ID, 'reject'))
    expect(await screen.findByText(/^Denied\./)).toBeInTheDocument()
  })

  it('says so, and offers nothing, once the approval has ended', async () => {
    API.approvals.mockResolvedValue([])
    render(<InboxDetail item={ROW} onChanged={() => {}} navigate={() => {}} />)
    expect(await screen.findByText(/Nothing is waiting on this any more/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Allow/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Deny/ })).toBeNull()
  })

  it('a row that announces no approval offers no answer (the vacuity leg)', async () => {
    const { refs: _approval, ...plain } = ROW
    render(<InboxDetail item={{ ...plain, refs: {} } as InboxItem} onChanged={() => {}} navigate={() => {}} />)
    await screen.findByRole('button', { name: /Mark handled/ })
    expect(screen.queryByRole('button', { name: /^Allow/ })).toBeNull()
    expect(API.approvals).not.toHaveBeenCalled()
  })
})

describe('the notification that announces an approval', () => {
  it('approves it there, and marks the note read', async () => {
    render(<NotificationsPage query={{ open: NOTE.ts }} setQuery={vi.fn()} navigate={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /^Allow subagent_run/ }))
    await waitFor(() => expect(API.resolveApproval).toHaveBeenCalledWith(ID, 'approve'))
    await waitFor(() => expect(API.ackNotification).toHaveBeenCalledWith(NOTE.ts))
  })

  it('denies it there', async () => {
    render(<NotificationsPage query={{ open: NOTE.ts }} setQuery={vi.fn()} navigate={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /^Deny subagent_run/ }))
    await waitFor(() => expect(API.resolveApproval).toHaveBeenCalledWith(ID, 'reject'))
  })
})
