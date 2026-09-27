/** A call denied without an answer is in the Inbox, with a way to run it again (F-33).
 *
 *  The gateway leaves one `system` item per denial (`dashboard/auto_denials.py`): an approval
 *  nobody answered in time (`refs.auto_denied: 'expired'`), or a call an unattended run could not
 *  ask about (`'unattended'`). `refs.session` is where it happened and `refs.chat` is set only when
 *  that is a chat a person can answer in — which is when asking it to try again means anything:
 *  the retry asks for the approval again, and this time someone is there.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { InboxItem } from '../../lib/api'
import { InboxDetail } from './InboxDetail'

const sendChat = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    sendChat: (...a: unknown[]) => sendChat(...a),
    updateInboxItem: () => Promise.resolve({}),
    restoreInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
  },
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

function note(refs: Record<string, unknown>, message = 'Denied, no answer: bash'): InboxItem {
  return {
    id: 'system_1', channel: 'system', channel_name: 'system', message, sender_id: 'system', sender_name: 'system',
    item_kind: 'system', classification: 'needs_reply', confidence: 'high', status: 'pending', refs,
  } as InboxItem
}

afterEach(() => { vi.clearAllMocks() })

describe('a denied call in the Inbox', () => {
  it('🔑 an expired approval in a chat offers to ask the chat to try again, and says what it will send', async () => {
    sendChat.mockResolvedValue({ ok: true })
    const navigate = vi.fn()
    render(<InboxDetail item={note({ auto_denied: 'expired', tool: 'bash', session: 'chat-a', chat: 'chat-a' })} onChanged={() => {}} navigate={navigate} />)

    expect(screen.getByText(/please try that again/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Ask it to try again' }))
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('chat/chat-a'))
    const [message, session] = sendChat.mock.calls[0]
    expect(session).toBe('chat-a')
    expect(message).toContain('bash')
    expect(message).toMatch(/please try that again/)
  })

  it('a failed ask says so and stays put', async () => {
    sendChat.mockRejectedValue(new Error('the chat is busy'))
    const navigate = vi.fn()
    render(<InboxDetail item={note({ auto_denied: 'expired', tool: 'bash', session: 'chat-a', chat: 'chat-a' })} onChanged={() => {}} navigate={navigate} />)
    await userEvent.click(screen.getByRole('button', { name: 'Ask it to try again' }))
    expect(await screen.findByText(/the chat is busy/)).toBeTruthy()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('🔑 a workflow step’s denial opens the step, not a chat that does not exist', async () => {
    const navigate = vi.fn()
    render(<InboxDetail item={note({ auto_denied: 'unattended', tool: 'write_file', session: 'workflow:run-7:write' }, 'Denied, no one to ask: write_file')} onChanged={() => {}} navigate={navigate} />)
    expect(screen.queryByRole('button', { name: 'Ask it to try again' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /Open the workflow run/ }))
    expect(navigate).toHaveBeenCalledWith('workflows/runs/run-7?node=write')
  })

  it('an unattended denial has nobody to ask again, so it only says where it happened', () => {
    render(<InboxDetail item={note({ auto_denied: 'unattended', tool: 'write_file', session: 'cron:nightly' }, 'Denied, no one to ask: write_file')} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.queryByRole('button', { name: 'Ask it to try again' })).toBeNull()
  })
})
