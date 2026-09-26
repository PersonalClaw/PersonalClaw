/**
 * How the chat page stages its light: ONE source (the composer), inside the chat column.
 *
 * Owner report (2026-09-25): "weird shadow shapes that change from time to time" on the chat page.
 * Identified by 1-second timed frames of a paced stream at 1440×900, diffed on background pixels
 * only: the single moving shape was the "glow-travel" light — ChatPage handed DotGlow a second
 * target (a 1px anchor at the top of the streaming turn), and DotGlow parked a ~180px bloom plus a
 * pool of lit dots behind the transcript there. It climbed with the auto-scroll (measured
 * 638px → 192px over one 20s reply), jumped when a turn appended, and faded on done. Light with no
 * surface to come from reads as a smudge; on a light canvas a tinted glow LOWERS luminance, so it
 * was literally a grey shadow ((240,244,248) → (239,239,242) at its core).
 *
 * And the stage: DotGlow was mounted on the PAGE root, so its box — the box the halo is faded out
 * inside of — included the header and ran under every docked `SidePanel` (Activity, file peek,
 * chat history, the mobile map drawer), all opaque. With a panel open, the halo was sliced at the
 * panel's rounded edge and spilled out below it.
 *
 * `DotGlow` is mocked to a marker that records its props: what is under test here is what the page
 * gives it and where the page puts it. The painted result is `e2e/chatHalo.spec.ts`'s.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

const h = vi.hoisted(() => ({
  detail: {
    key: 'chat-6-x', title: 'Some chat', messages: [] as unknown[], running: false,
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  },
  chatSessionDetail: vi.fn(),
  glowProps: [] as Record<string, unknown>[],
}))

vi.mock('../../ui/DotGlow', () => ({
  DotGlow: (props: Record<string, unknown>) => {
    h.glowProps.push(props)
    return <div data-testid="dot-glow" />
  },
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
  return { ...actual, api }
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

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
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
  h.glowProps.length = 0
  h.chatSessionDetail.mockReset().mockResolvedValue({
    ...h.detail,
    messages: [
      { role: 'user', content: 'question one', ts: '2026-09-25T10:01:00+00:00' },
      { role: 'assistant', content: 'ANSWER ONE', ts: '2026-09-25T10:01:30+00:00' },
      { role: 'user', content: 'question two', ts: '2026-09-25T10:02:00+00:00' },
      { role: 'assistant', content: 'ANSWER TWO', ts: '2026-09-25T10:02:30+00:00' },
    ],
  })
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const page = (query: Record<string, string> = {}) =>
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-6-x" navigate={() => {}} query={query} setQuery={() => {}} />
    </AppearanceProvider>,
  )

/** The props DotGlow may be given: the composer is the one light, and nothing else is a target. */
const ONE_LIGHT = new Set(['className', 'intensity', 'focused', 'composerRef'])

describe('the chat page lights itself from ONE source — the composer', () => {
  it('gives DotGlow no second target, so nothing in the transcript can carry a light', async () => {
    page()
    await screen.findByText('ANSWER TWO')
    expect(h.glowProps.length, 'DotGlow never rendered').toBeGreaterThan(0)
    for (const props of h.glowProps) {
      const extra = Object.keys(props).filter((k) => !ONE_LIGHT.has(k))
      expect(extra, 'DotGlow was handed a light target other than the composer').toEqual([])
      expect(props.composerRef, 'the one light must be the composer').toBeTruthy()
    }
  })
})

describe('the chat page stages its light in the chat COLUMN, not the page', () => {
  it('the halo’s stage holds the composer but not the page header', async () => {
    const { container } = page()
    await screen.findByText('ANSWER TWO')
    const stage = screen.getByTestId('dot-glow').parentElement!
    expect(stage.querySelector('[data-tour="chat"]'), 'the stage does not hold the composer').not.toBeNull()
    expect(stage.querySelector('header'), 'the stage is the whole page: it shares a box with the header').toBeNull()
    expect(container.querySelector('header'), 'the precondition: the page renders a header at all').not.toBeNull()
  })

  it('and a docked side panel sits BESIDE the stage, never inside it', async () => {
    page({ activity: '1' })
    await screen.findByText('ANSWER TWO')
    const panel = await screen.findByRole('region', { name: 'Activity' })
    const stage = screen.getByTestId('dot-glow').parentElement!
    await waitFor(() => expect(stage.contains(panel), 'the Activity panel is inside the halo’s stage, so the halo runs under it').toBe(false))
  })
})
