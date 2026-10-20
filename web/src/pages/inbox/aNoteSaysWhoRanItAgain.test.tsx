/** A denied call's note says who decided its retry, and a lifecycle hook's note opens the hook.
 *
 *  The gateway settles a "Denied, no answer" note when the same call is asked again and answered,
 *  and now also when it runs again because a standing grant approved it without asking — a chat's
 *  Trust, YOLO, an automation's own approval mode (`approval_state.settle_granted`). Who decided
 *  rides `refs.retry_by`: `you`, or the grant's name (`approval_grants`). The note used to say
 *  "Asked again: you allowed …" for every settled retry, so a call a grant ran read as a person's
 *  Allow.
 *
 *  A lifecycle hook is a trigger too (`refs.trigger` is `lifecycle:<id>`), but it fires on the
 *  agent's own events and has no Run now: its Test is a rehearsal. So its note says when it runs
 *  again and opens it, instead of offering a button that would not ask for the call again.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { InboxItem } from '../../lib/api'
import { InboxDetail } from './InboxDetail'

const triggers = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    triggers: () => triggers(),
    updateInboxItem: () => Promise.resolve({}),
    restoreInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
  },
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

function note(refs: Record<string, unknown>, status = 'pending'): InboxItem {
  return {
    id: 'system_1', channel: 'system', channel_name: 'system', message: 'Denied, no answer: bash',
    sender_id: 'system', sender_name: 'system', item_kind: 'system', classification: 'needs_reply',
    confidence: 'high', status, refs,
  } as InboxItem
}

afterEach(() => { vi.clearAllMocks() })

describe('a settled note says who decided the retry', () => {
  it.each([
    ['trust', 'Ran again without asking: this chat’s Trust allowed bash.'],
    ['yolo', 'Ran again without asking: YOLO allowed bash.'],
    ['approval_mode', 'Ran again without asking: its own approval mode allowed bash.'],
    ['you', 'Asked again: you allowed bash.'],
  ])('🔑 retry_by %s', (by, said) => {
    const handled = note({ auto_denied: 'expired', tool: 'bash', session: 'chat-a', chat: 'chat-a', retry: 'approved', retry_by: by }, 'handled')
    render(<InboxDetail item={handled} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText(said)).toBeTruthy()
  })

  it('a call that declares a read ran on its declaration, which is no permission at all', () => {
    const handled = note({ auto_denied: 'expired', tool: 'memory_recall', session: 'chat-a', chat: 'chat-a', retry: 'approved', retry_by: 'declared_read' }, 'handled')
    render(<InboxDetail item={handled} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText('Ran again without asking: memory_recall only reads, and a read asks nobody.')).toBeTruthy()
  })

  it('a call whose work asks for itself ran on that, which is no permission either', () => {
    const handled = note({ auto_denied: 'expired', tool: 'subagent_run', session: 'chat-a', chat: 'chat-a', retry: 'approved', retry_by: 'work_asks' }, 'handled')
    render(<InboxDetail item={handled} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText('Ran again without asking: what subagent_run starts asks you itself.')).toBeTruthy()
  })

  it('a grant it does not know by name reads as a permission, never as a person', () => {
    const handled = note({ auto_denied: 'expired', tool: 'bash', session: 'chat-a', chat: 'chat-a', retry: 'approved', retry_by: 'something_new' }, 'handled')
    render(<InboxDetail item={handled} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText('Ran again without asking: a standing permission allowed bash.')).toBeTruthy()
    expect(screen.queryByText(/you allowed/)).toBeNull()
  })
})

describe('a lifecycle hook’s note', () => {
  const hook = {
    kind: 'lifecycle', id: 'lifecycle:h-prompt', raw_id: 'h-prompt', name: 'Plan on every prompt',
    enabled: true, event: 'UserPromptSubmit', action: { provider: 'invoke-agent', config: {} },
  }

  it('🔑 says when the hook runs again, with no Run button, and the note opens the hook', async () => {
    triggers.mockResolvedValue({ triggers: [hook] })
    const navigate = vi.fn()
    render(<InboxDetail item={note({ auto_denied: 'expired', tool: 'bash', session: 'subagent:ab12', trigger: 'lifecycle:h-prompt' })} onChanged={() => {}} navigate={navigate} />)

    expect(await screen.findByText(/“Plan on every prompt” runs again on its next user prompt submit event/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Run it again/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /Open the trigger/ }))
    expect(navigate).toHaveBeenCalledWith('triggers?open=lifecycle%3Ah-prompt')
  })

  it('a hook that was switched off says it runs again once it is on', async () => {
    triggers.mockResolvedValue({ triggers: [{ ...hook, enabled: false }] })
    render(<InboxDetail item={note({ auto_denied: 'expired', tool: 'bash', session: 'subagent:ab12', trigger: 'lifecycle:h-prompt' })} onChanged={() => {}} navigate={() => {}} />)
    expect(await screen.findByText(/once it is switched on/)).toBeTruthy()
  })

  it('a hook that is gone says it will not run again', async () => {
    triggers.mockResolvedValue({ triggers: [] })
    render(<InboxDetail item={note({ auto_denied: 'expired', tool: 'bash', session: 'subagent:ab12', trigger: 'lifecycle:h-prompt' })} onChanged={() => {}} navigate={() => {}} />)
    expect(await screen.findByText(/no longer exists, so it will not run again/)).toBeTruthy()
  })
})
