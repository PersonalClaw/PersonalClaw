import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── Your threads say which channel a chat came in on ─────────────────────────────────────────────
//
// Home's Your threads (under Recent and completed) reopen your most recent chats, and a chat that came in on Telegram is
// one of them. It read as one of yours, opened here; the link now says "From Telegram", in the same
// words as the chat history's row (`FromChannel`).

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))

const SESSIONS = [
  { key: 'chat-1-x', title: 'Weekly review', updated: '2026-09-26T09:00:00+00:00', run_state: runState({ session: 'chat-1-x' }), origin: 'manual' },
  { key: 'chat-2-x', title: 'Daily agenda', updated: '2026-09-26T10:00:00+00:00', run_state: runState({ session: 'chat-2-x' }),
    origin: 'channel', source_id: '5550001234', source_label: 'Telegram' },
]

/** Envelope-shaped reads the dashboard destructures; anything else resolves `[]`. */
const ENVELOPES: Record<string, unknown> = {
  agents: { agents: [] },
  onboarding: { needs_model: false, has_model_provider: true, has_chat_binding: true },
  discover: { enabled: false, visible_count: 0, areas: [] },
  doctor: { ok: true, capabilities: {} },
  skillProposals: { proposals: [], lastReview: null },
  modelsLoaded: {
    loaded: [], providers: [],
    pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 90, warn: false, source: 'unavailable' },
  },
  chatSessions: SESSIONS,
}

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const api = new Proxy({}, {
    get: (_t, prop: string) => () => Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : []),
  })
  return { ...real, api }
})

const { HomeHarness } = await import('../../test/homeHarness')
const { AppearanceProvider } = await import('../../app/appearance')

afterEach(cleanup)

const jumpLinks = async () => {
  render(<AppearanceProvider><HomeHarness /></AppearanceProvider>)
  return screen.findByRole('list', { name: 'Your threads' })
}

describe('Your threads, on Home', () => {
  it('says "From <channel>" on a chat that came in on one', async () => {
    const links = await jumpLinks()
    const phone = within(links).getByRole('button', { name: /Daily agenda/ })
    expect(phone.closest('li')?.textContent).toContain('From Telegram')
    expect(phone.getAttribute('aria-label')).toContain('From Telegram')
  })

  it('says nothing of a channel on one of yours', async () => {
    const links = await jumpLinks()
    const mine = within(links).getByRole('button', { name: /Weekly review/ })
    expect(mine.closest('li')?.textContent).not.toContain('From ')
  })
})
