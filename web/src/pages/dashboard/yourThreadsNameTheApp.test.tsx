import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── Your threads name the app that started a chat ────────────────────────────────────────────────
//
// Home's Your threads (under Recent and completed) reopen your most recent chats, and an app's conversation is one of
// them (#3632 puts them in your history). Opening one means speaking under the APP's permissions,
// not yours — and a bare title cannot tell you that. The history rows already say "Started by
// <App>"; these links are the other place you pick a chat up again, so they say it too, in the
// same words (`StartedByApp`).

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))

const SESSIONS = [
  { key: 'chat-1-x', title: 'Weekly review', updated: '2026-09-26T09:00:00+00:00', run_state: runState({ session: 'chat-1-x' }) },
  { key: 'chat-2-x', title: 'Inbox triage', updated: '2026-09-26T10:00:00+00:00', run_state: runState({ session: 'chat-2-x' }),
    created_by_app: 'probe-alpha', created_by_app_name: 'Probe Alpha' },
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
  it('says "Started by <App>" on the app\'s chat', async () => {
    const links = await jumpLinks()
    const app = within(links).getByRole('button', { name: /Inbox triage/ })
    expect(app.closest('li')?.textContent).toContain('Started by Probe Alpha')
    // …and says it in the row's name, so a screen reader hears it before choosing.
    expect(app.getAttribute('aria-label')).toContain('Started by Probe Alpha')
  })

  it('names no app on one of yours', async () => {
    const links = await jumpLinks()
    const mine = within(links).getByRole('button', { name: /Weekly review/ })
    expect(mine.closest('li')?.textContent).not.toMatch(/Started by /)
    expect(mine.getAttribute('aria-label')).not.toMatch(/Started by /)
  })
})
