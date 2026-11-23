import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

// ── "Keep my question in view" is a 44 px target on a phone ────────────────────────────────────
//
// The switch (#3820) was new to Settings › Chat, and the UI quality matrix measured it under 44 px
// at 320, 390, 412 and 768 px in both themes, with no allowance in the page's baseline (#3905, the
// triage's R3). It was the first switch to take the phone's box: the same track in a transparent
// 44 px box at the compact widths. Every switch has that box now (`toggleHitArea.test.tsx`), so the
// switches beside it have it too.
//
// jsdom has no layout, so the classes that set the box are pinned; the matrix measures the box.

const DASH = {
  restore_sessions: true, restore_window_minutes: 30, send_on_enter: true, show_timestamps: false,
  show_thinking_inline: false, simplified_tool_names: false, followup_chips: true,
  offer_check_work: true, stream_reveal: 'smooth', merge_queued_messages: true, keep_question_in_view: false,
}
const PLAW = { session: {}, agents_routing: {}, resilience: {}, checkpoints: {}, tools: {}, rooms: {} }

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
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
      },
    }
  })
})
afterEach(() => { cleanup(); vi.doUnmock('../../lib/api') })

const classes = (el: Element) => el.className.toString().split(/\s+/)

describe('Keep my question in view on a phone', () => {
  it('its switch has a 44 px box below the large breakpoint, both ways', async () => {
    const { ChatPanel } = await import('./ChatPanel')
    render(<ChatPanel />)
    const sw = await screen.findByRole('switch', { name: 'Keep my question in view' })
    expect(classes(sw)).toEqual(expect.arrayContaining(['max-lg:min-h-11', 'max-lg:min-w-11']))
  })

  it('as do the switches beside it, which no longer wait for it (every switch has the box)', async () => {
    const { ChatPanel } = await import('./ChatPanel')
    render(<ChatPanel />)
    const sw = await screen.findByRole('switch', { name: /^Send on Enter/ })
    expect(classes(sw)).toEqual(expect.arrayContaining(['max-lg:min-h-11', 'max-lg:min-w-11']))
  })
})
