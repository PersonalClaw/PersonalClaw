import { describe, it, expect, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { toThreads, type Thread, type ThreadChat } from '../lib/useThreads'
import { runState } from '../test/runState'
import { ThreadRail } from './ThreadRail'

// ── The sidebar says which channel a chat came in on ────────────────────────────────────────────
//
// A chat that came in on Telegram sits among your chats. Home's Your chats said "From Telegram" on
// it, in the chat history's words (`FromChannel`); Your chats left Home, and the sidebar is where
// your chats are listed, so its row says it now: at the row's end, in its name and in its tip.

afterEach(cleanup)

const SESSIONS: ThreadChat[] = [
  { key: 'chat-1-x', title: 'Weekly review', run_state: runState({ session: 'chat-1-x' }), origin: 'manual', project_id: '' } as ThreadChat,
  { key: 'chat-2-x', title: 'Daily agenda', run_state: runState({ session: 'chat-2-x' }), project_id: '',
    origin: 'channel', source_id: '5550001234', source_label: 'Telegram' } as ThreadChat,
]

const rows = (): Thread[] => toThreads({ sessions: SESSIONS, loops: [], items: [], approvals: [] }).flatMap((g) => g.threads)

function rail() {
  render(<ThreadRail rows={rows()} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}} />)
}

describe('a chat that came in on a channel, in the sidebar', () => {
  it('its thread carries the channel it came in on; one of yours carries none', () => {
    const [mine, phone] = ['chat:chat-1-x', 'chat:chat-2-x'].map((key) => rows().find((t) => t.key === key))
    expect(phone?.channel).toBe('Telegram')
    expect(mine?.channel).toBeUndefined()
  })

  it('says "From <channel>" at its row’s end, in its name and in its tip', () => {
    rail()
    const phone = screen.getByRole('button', { name: 'Daily agenda, from Telegram' })
    expect(phone.querySelector('[data-from-channel]')?.textContent).toBe('From Telegram')
    act(() => { fireEvent.focus(phone) })
    expect(document.querySelector('[data-tooltip-bubble]')?.textContent).toContain('This chat came in on Telegram.')
    expect(phone.hasAttribute('title'), 'the tip is the app’s own').toBe(false)
  })

  it('says nothing of a channel on one of yours', () => {
    rail()
    const mine = screen.getByRole('button', { name: 'Weekly review' })
    expect(mine.querySelector('[data-from-channel]')).toBeNull()
    expect(mine.textContent).not.toContain('From ')
  })
})
