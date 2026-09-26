import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

// ── Settings › Chat says when a read failed ────────────────────────────────────────────────────────
//
// Two reads here resolved a failure to a value:
//
//   · the dashboard settings read kept `.catch(() => null)` on the belief that it only fed the starter
//     list. It is `cfg`, the Sessions and Messages sections' whole state, and the panel's gate waits
//     for `cfg` — so a failed read resolved the query with `cfg: null`, the LoadError branch never
//     fired (`data` was defined), and the panel drew its skeleton forever;
//   · the starter list set `[]` on a failure and said "No starters yet", with instructions for
//     making one, to a user whose starters could not be read.
//
// Each is now said, with a Retry, and the failure used is the one a browser really produces.

const offline = () => Promise.reject(new TypeError('Failed to fetch'))
const DASH = {
  restore_sessions: true, restore_window_minutes: 30, send_on_enter: true, show_timestamps: false,
  show_thinking_inline: false, simplified_tool_names: false, followup_chips: true,
  offer_check_work: true, stream_reveal: 'smooth', merge_queued_messages: true,
}
const PLAW = { session: {}, agents_routing: {}, resilience: {}, checkpoints: {}, tools: {}, rooms: {} }

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as Record<string, unknown>),
        dashboardConfig: () => Promise.resolve(DASH),
        personalclawConfig: () => Promise.resolve(PLAW),
        sessionTemplates: () => Promise.resolve([]),
        agentProviders: () => Promise.resolve([]),
        savedAgents: () => Promise.resolve([]),
        agents: () => Promise.resolve({ agents: [], default_agent: '' }),
        routingStatus: () => Promise.resolve({ enabled: true, muted: [], dismissals: {} }),
        ...over,
      },
    }
  })
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })
afterEach(cleanup)

describe('Settings › Chat', () => {
  it('a failed dashboard read is said, with a Retry, instead of a skeleton that never leaves', async () => {
    let calls = 0
    mockApi({ dashboardConfig: () => (++calls === 1 ? offline() : Promise.resolve(DASH)) })
    const { ChatPanel } = await import('./ChatPanel')
    render(<ChatPanel />)
    expect(await screen.findByText("Couldn't load your settings")).toBeTruthy()
    expect(screen.queryByRole('status', { busy: true }), 'no skeleton beside the failure').toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    expect(await screen.findByText('Chat starters')).toBeTruthy()
    expect(calls).toBe(2)
  })

  it('a failed starter list is said, never "No starters yet"', async () => {
    let calls = 0
    mockApi({ sessionTemplates: () => (++calls === 1 ? offline() : Promise.resolve([])) })
    const { ChatPanel } = await import('./ChatPanel')
    render(<ChatPanel />)
    expect(await screen.findByText("Couldn't load chat starters.")).toBeTruthy()
    expect(screen.queryByText(/No starters yet/), 'a list nobody could read is not an empty one').toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    expect(await screen.findByText(/No starters yet/)).toBeTruthy()
    expect(calls).toBe(2)
  })
})
