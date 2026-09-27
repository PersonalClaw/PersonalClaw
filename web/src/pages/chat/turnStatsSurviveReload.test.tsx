import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { hydrateTurns, type HistMsg } from './chatTypes'

// ── A turn's "Turn complete" line is still there after a reload (F-38) ─────────────────────────
//
// Measured before: the line (events, tool calls, context, cost, tokens) arrived only as a live
// `activity_event`, so the turn's details lost their Telemetry row the moment the chat was
// reloaded, although the turn's telemetry record was persisted on its last assistant message.
// That record now carries the sentence the live line showed, and hydration reads it back.

const LINE = 'Turn complete: 3 events, 1 tool calls, context 42% · $0.0123 · 1,200 in / 340 out tokens'

const transcript: HistMsg[] = [
  { role: 'user', content: 'What changed?', ts: '2026-09-26T10:00:00Z' },
  { role: 'assistant', content: 'Two files changed.', ts: '2026-09-26T10:00:04Z', meta: { turn_telemetry: { line: LINE } } },
]

describe('hydrateTurns', () => {
  it("restores the turn's telemetry line as the stats activity the live frame produced", () => {
    const turns = hydrateTurns(transcript)
    const reply = turns.find((t) => t.role === 'assistant')!
    expect(reply.segments).toContainEqual({ kind: 'activity', text: LINE, activityKind: 'stats' })
  })

  it('adds nothing for a record written before the line was persisted', () => {
    const old: HistMsg[] = [transcript[0], { ...transcript[1], meta: { turn_telemetry: {} } }]
    const reply = hydrateTurns(old).find((t) => t.role === 'assistant')!
    expect(reply.segments.some((s) => s.kind === 'activity')).toBe(false)
  })
})

// ── and the reloaded chat shows it ─────────────────────────────────────────────────────────────

const SESSION = 'chat-38-x'
const hd = vi.hoisted(() => ({ detail: null as unknown }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: () => Promise.resolve(hd.detail),
    useCaseSettings: () => Promise.resolve({}),
    personalclawConfig: () => Promise.resolve({ voice: {} }),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  return { ...real, api }
})

class FakeSocket {
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  Element.prototype.scrollIntoView ??= () => {}
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
  sessionStorage.clear()
  hd.detail = {
    key: SESSION, title: SESSION, messages: transcript, running: false, queue: [], task_mode: 'agent',
    approval: 'normal', memory_mode: 'persistent',
  }
})

afterEach(() => { vi.unstubAllGlobals() })

describe('a reloaded chat', () => {
  it("shows each finished turn's telemetry in its details", async () => {
    const { ChatPage } = await import('../ChatPage')
    const { AppearanceProvider } = await import('../../app/appearance')
    render(
      <AppearanceProvider>
        <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
      </AppearanceProvider>,
    )
    expect(await screen.findByText('Two files changed.')).toBeTruthy()
    const details = await screen.findByRole('button', { name: /telemetry/ })
    await act(async () => { await userEvent.setup().click(details) })
    await waitFor(() => expect(screen.getByText(LINE)).toBeTruthy())
  })
})
