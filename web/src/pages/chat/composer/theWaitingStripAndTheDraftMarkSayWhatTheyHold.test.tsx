import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { KeptDraftLine, WaitingSends } from './WaitingSends'
import { STALE_AFTER_MS, type OutboxItem } from '../../../lib/outbox'

// ── What the composer shows of what it kept (#4012) ─────────────────────────────────────────────
//
// A waiting send is a row of "Waiting to send", named by its words; after a day it asks Send now
// or Remove. Every row's controls are buttons, so Tab reaches them. A draft restored from this
// browser says so, with Discard draft.

afterEach(() => cleanup())

const item = (id: string, message: string, heldAgo = 0): OutboxItem =>
  ({ client_message_id: id, session: 'chat-1', message, held_at: Date.now() - heldAgo, chat_existed: true })

describe('the waiting strip', () => {
  it('names each waiting send, and offers only Remove until it has waited a day', () => {
    const onRemove = vi.fn()
    render(<WaitingSends items={[item('msg_a', 'Are the backups done?')]} onSendNow={vi.fn()} onRemove={onRemove} />)
    const strip = screen.getByRole('group', { name: 'Waiting to send' })
    expect(within(strip).getByText('Are the backups done?')).toBeTruthy()
    expect(within(strip).getByText('Waiting to send')).toBeTruthy()
    expect(within(strip).queryByRole('button', { name: 'Send now' })).toBeNull()
    fireEvent.click(within(strip).getByRole('button', { name: /^Remove “Are the backups done\?”/ }))
    expect(onRemove).toHaveBeenCalledWith('msg_a')
  })

  it('asks Send now or remove once a send has waited a day', () => {
    const onSendNow = vi.fn()
    render(<WaitingSends items={[item('msg_b', 'From yesterday', STALE_AFTER_MS + 1000)]} onSendNow={onSendNow} onRemove={vi.fn()} />)
    expect(screen.getByText(/^Waiting since .*\. Send now or remove it\.$/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Send now' }))
    expect(onSendNow).toHaveBeenCalled()
  })

  it('shows nothing when nothing waits', () => {
    const { container } = render(<WaitingSends items={[]} onSendNow={vi.fn()} onRemove={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })
})

describe('a restored draft', () => {
  it('says it is back, and Discard draft discards it', () => {
    const onDiscard = vi.fn()
    render(<KeptDraftLine onDiscard={onDiscard} />)
    expect(screen.getByText('Your draft from before is back.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }))
    expect(onDiscard).toHaveBeenCalled()
  })
})
