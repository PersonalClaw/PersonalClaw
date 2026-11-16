import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api, ApiError } from '../../lib/api'
import { invalidateKeys, resetDataStore } from '../../lib/data'
import { waitingItem } from '../../test/needsYouHarness'
import { mountPage, PID } from '../../test/projectPageHarness'
import { NeedCard } from '../dashboard/NeedCards'
import { NEEDS_YOU_KEY } from '../../lib/useNeedsYou'
import { ARM_MS } from '../../ui/dialog/DialogShell'

// ── Answering on a project's page is Home's answer (§5.4) ───────────────────────────────────────
//
// Needs you on a project's page draws Home's own cards (`NeedCards.tsx`, the `NeedCard` imported
// above), so an answer there is Home's: Allow and Deny go by Home's path (`useNeedActions`), an
// approval that names a second confirmation asks once more and sends nothing before it, and an ask
// answered or ended elsewhere says so rather than "try again". A chat's question is answered in its
// detail, opened beside the work at `?open=need:<key>`. An ask arriving never moves the focus.
// While PersonalClaw can't be reached each answer is off (`off`), as on Home.

void NeedCard

/** The gateway connection the page reads (`useGatewayConnection`), by test: connected unless a case
 *  says the gateway can't be reached. */
const conn = vi.hoisted(() => ({ state: 'connected' as 'connected' | 'disconnected' }))
vi.mock('../../app/gatewayConnection', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useGatewayConnection: () => ({ state: conn.state, lastReachedAt: null, lostAt: null }),
}))

beforeEach(() => { resetDataStore(); localStorage.clear(); conn.state = 'connected' })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

const needs = async () => (await screen.findByRole('heading', { level: 2, name: 'Needs you' })).closest('section')!

const destructive = waitingItem({
  id: 'approval:ap-1', work: 'loop:l-1', session: '', project_id: PID, consequence: 'destructive', risk: 'destructive',
  brief: { tool: 'bash', tool_input: 'rm -rf build', tool_purpose: 'Clear the build folder', source_label: 'loop “Tidy”', confirms: ['deletes'] },
  answer: { method: 'POST', path: '/api/approvals/ap-1/{answer}', takes: ['approve', 'reject'], body: {} },
  route: '#/loops/l-1',
})

const chatQuestion = waitingItem({
  id: 'question:c-1:q-1', kind: 'question', work: 'chat:c-1', session: 'c-1', project_id: PID, consequence: 'asks',
  brief: { questions: [{ question: 'Which bed first?' }], asked_by: 'Seed ideas' },
  answer: { method: 'POST', path: '/api/chat/sessions/c-1/questions/q-1/answer', takes: ['text'], body: {} },
  route: '#/chat/c-1',
})

describe('answering on a project page', () => {
  it('Allow asks once more for a call that deletes, sends nothing before Yes, then answers by Home\'s path', async () => {
    const resolve = vi.spyOn(api, 'resolveApproval').mockResolvedValue({ ok: true } as never)
    mountPage({ waiting: [destructive] })
    const card = await within(await needs()).findByRole('group', { name: 'Permission needed to run bash' })
    await userEvent.click(within(card).getByRole('button', { name: /^Allow bash/ }))
    expect(await within(card).findByText(/It deletes files\./)).toBeTruthy()
    expect(resolve, 'nothing is sent before the second confirmation').not.toHaveBeenCalled()
    // The Yes answers only a press begun after it has been in view and quiet a moment (`ARM_MS`).
    await act(async () => { await new Promise((r) => setTimeout(r, ARM_MS + 100)) })
    await userEvent.click(within(card).getByRole('button', { name: /^Yes, run it/ }))
    await waitFor(() => expect(resolve).toHaveBeenCalledWith('ap-1', 'approve'))
    expect(await within(await needs()).findByText('Approved.')).toBeTruthy()
  })

  it('Deny sends the reject, and an ask answered elsewhere says so, not "try again"', async () => {
    const resolve = vi.spyOn(api, 'resolveApproval').mockRejectedValue(new ApiError('gone', 404))
    mountPage({ waiting: [destructive] })
    const card = await within(await needs()).findByRole('group', { name: 'Permission needed to run bash' })
    await userEvent.click(within(card).getByRole('button', { name: /^Deny bash/ }))
    await waitFor(() => expect(resolve).toHaveBeenCalledWith('ap-1', 'reject'))
    // Home's own sentence for any ask or proposal decided elsewhere (`ENDED_ELSEWHERE`).
    expect(await within(await needs()).findByText('It is no longer waiting: it was decided or ended somewhere else.')).toBeTruthy()
    expect(within(await needs()).queryByText(/try again/)).toBeNull()
  })

  it("a chat's question is answered in its detail, opened beside the work at ?open=need:<key>", async () => {
    vi.spyOn(api, 'chatSessionDetail').mockResolvedValue({ messages: [] } as never)
    const answer = vi.spyOn(api, 'answerChatQuestion').mockResolvedValue({ outcome: 'answered' } as never)
    const { address } = mountPage({ waiting: [chatQuestion] })
    await within(await needs()).findByText('Which bed first?')
    await userEvent.click(within(await needs()).getByRole('button', { name: /^Show the details of / }))
    expect(address.query.open).toMatch(/^need:/)
    const field = await screen.findByPlaceholderText('Reply in one line: Enter sends, Escape closes')
    await userEvent.type(field, 'The north bed{Enter}')
    await waitFor(() => expect(answer).toHaveBeenCalledWith('c-1', 'q-1', { answers: [{ selected: [], other: 'The north bed' }] }))
  })

  it("while PersonalClaw can't be reached each answer is off, and with it back each is on", async () => {
    const resolve = vi.spyOn(api, 'resolveApproval').mockResolvedValue({ ok: true } as never)
    const allowOf = async () => within(await within(await needs()).findByRole('group', { name: 'Permission needed to run bash' }))
      .getByRole('button', { name: /^Allow bash/ })
    const isOff = (b: HTMLElement) => (b as HTMLButtonElement).disabled || b.getAttribute('aria-disabled') === 'true'
    conn.state = 'disconnected'
    mountPage({ waiting: [destructive] })
    const off = await allowOf()
    expect(isOff(off), 'an answer offered while the gateway is unreachable').toBe(true)
    await userEvent.click(off)
    expect(resolve).not.toHaveBeenCalled()
    cleanup(); resetDataStore()
    conn.state = 'connected'
    mountPage({ waiting: [destructive] })
    expect(isOff(await allowOf()), 'the control: online, the answer is on').toBe(false)
  })

  it('an ask arriving never moves the focus', async () => {
    let list = [destructive]
    mountPage({ waiting: [destructive] })
    vi.mocked(api.waiting).mockImplementation(async () => ({ waiting: list, unreadable: [] }))
    await needs()
    const search = screen.getByRole('searchbox', { name: /Search Garden/ })
    search.focus()
    list = [destructive, chatQuestion]
    await act(async () => { invalidateKeys(NEEDS_YOU_KEY) })
    await within(await needs()).findByText('Which bed first?')
    expect(document.activeElement, 'the new ask took the focus').toBe(search)
  })
})
