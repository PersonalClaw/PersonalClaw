import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { CallbackRow } from '../../lib/api'

// ── A callback the agent registered is listed, shows what it starts from, and asks to be allowed ─
//
// The chat's `hook_register` saves a callback: an outside system's post to `/api/hooks/agent`
// naming its session starts an agent turn, with the agent's tools, from the context the agent
// saved (`webhook_callbacks.py`). It runs only once the owner allows it, and the switch is that
// yes. Measured before this: no surface listed a callback at all, so a turn the agent had set up
// to start from its own words ran with nobody having seen it.

const { API } = vi.hoisted(() => ({
  API: {
    schedules: vi.fn(() => Promise.resolve({ jobs: [] })),
    hooks: vi.fn(() => Promise.resolve([])),
    storeTriggers: vi.fn(() => Promise.resolve([])),
    callbacks: vi.fn(() => Promise.resolve([] as CallbackRow[])),
    triggerReview: vi.fn(() => Promise.resolve([])),
    actionProviders: vi.fn(() => Promise.resolve([])),
    autonomyLadder: vi.fn(() => Promise.reject(new Error('no ladder in this test'))),
    triggerVariables: vi.fn(() => Promise.resolve({ lifecycle: [], schedule: [], event: [], app_sources: [] })),
    toggleCallback: vi.fn(() => Promise.resolve({ ok: true })),
    deleteCallback: vi.fn(() => Promise.resolve({ ok: true })),
    channels: vi.fn(() => Promise.resolve([])),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

function row(over: Partial<CallbackRow> = {}): CallbackRow {
  return {
    kind: 'callback', id: 'callback:review:pr-1', raw_id: 'review:pr-1', name: 'review:pr-1',
    enabled: false, created_by: 'agent', needs_grant: ['Agent turn'],
    context_summary: 'When CI answers, merge the PR and delete the branch.',
    session_key: 'hook:review:pr-1', url: 'http://127.0.0.1:10000/api/hooks/agent',
    reach: 'It takes requests only from programs on the machine PersonalClaw runs on.',
    registered_at: Date.now() / 1000 - 120, seal: 'abc123',
    ...over,
  }
}

async function mountTriggers(query: Record<string, string> = {}) {
  const { TriggersSection } = await import('./TriggersSection')
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={query} setQuery={() => {}} />)
}

beforeEach(() => {
  sessionStorage.clear()
  API.toggleCallback.mockReset()
  API.toggleCallback.mockImplementation(() => Promise.resolve({ ok: true }))
  API.callbacks.mockImplementation(() => Promise.resolve([] as CallbackRow[]))
})

describe('a callback the agent registered', () => {
  it('is listed, badged as not allowed to run — and one the owner allowed is not', async () => {
    API.callbacks.mockImplementation(() => Promise.resolve([
      row(),
      row({ id: 'callback:deploy', raw_id: 'deploy', name: 'deploy', enabled: true, needs_grant: [] }),
    ]))
    await mountTriggers()
    await waitFor(() => expect(screen.getByText('review:pr-1')).toBeInTheDocument())
    expect(screen.getByText('deploy')).toBeInTheDocument()
    expect(screen.getAllByText('· not allowed to run')).toHaveLength(1)
    // Its switch is the yes, so an off one is not ALSO called "disabled".
    expect(screen.queryByText('· disabled')).not.toBeInTheDocument()
  })

  it('opens showing the context its turn starts from, and switching it on names that context', async () => {
    API.callbacks.mockImplementation(() => Promise.resolve([row()]))
    await mountTriggers({ open: 'callback:review:pr-1' })
    await waitFor(() => expect(screen.getByText('When CI answers, merge the PR and delete the branch.')).toBeInTheDocument())
    expect(screen.getByText('Not allowed to use “Agent turn”')).toBeInTheDocument()
    expect(screen.getByText('hook:review:pr-1')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('switch', { name: 'Allow this callback to run' }))

    await waitFor(() => expect(API.toggleCallback).toHaveBeenCalledWith('review:pr-1', true, 'abc123'))
  })

  it('a refusal — the context moved, or the owner declined — is shown, not swallowed', async () => {
    API.callbacks.mockImplementation(() => Promise.resolve([row()]))
    API.toggleCallback.mockImplementation(() =>
      Promise.reject(new Error('“review:pr-1” was registered again with other context since this page read it.')))
    await mountTriggers({ open: 'callback:review:pr-1' })
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Allow this callback to run' })).toBeInTheDocument())

    fireEvent.click(screen.getByRole('switch', { name: 'Allow this callback to run' }))

    expect(await screen.findByText(/registered again with other context/)).toBeInTheDocument()
  })

  it('says where its address answers and what a program sends to it', async () => {
    API.callbacks.mockImplementation(() => Promise.resolve([row()]))
    await mountTriggers({ open: 'callback:review:pr-1' })
    await waitFor(() => expect(screen.getByText('http://127.0.0.1:10000/api/hooks/agent')).toBeInTheDocument())
    expect(screen.getByText('It takes requests only from programs on the machine PersonalClaw runs on.')).toBeInTheDocument()
    expect(screen.getByText('Authorization: Bearer <webhook token>')).toBeInTheDocument()
    expect(screen.getByText('personalclaw config set hooks.webhook_token <token>')).toBeInTheDocument()
  })

  it('has no editor: its context is the agent’s', async () => {
    API.callbacks.mockImplementation(() => Promise.resolve([row({ enabled: true, needs_grant: [] })]))
    await mountTriggers({ open: 'callback:review:pr-1' })
    await waitFor(() => expect(screen.getByText('Allowed to run')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /Edit/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/Not allowed to use/)).not.toBeInTheDocument()
  })
})
