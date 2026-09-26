import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'

// ── The chat header names the app that started the chat, where you look ──────────────────────────
//
// #3632 labelled an app's conversation in the history row and above the composer, and kept it OUT
// of the chat's header on purpose: that row was already over-full below ~1300px, and a chip there
// slid under the Task and Permission pills. The header now has a context line under its row
// (`TopBar.below`), which wraps, so the chip goes where a reader looks first — and the chips that
// already overflowed the row (cost, "Branched from") moved there with it.
//
// Geometry is `e2e/chatHeaderGeometry.spec.ts`'s job: jsdom lays nothing out. This pins the
// structure that geometry depends on — what is in the title's row, what is on the context line,
// and that a truncating chip still carries its whole sentence.

const PARENT = 'The original launch plan, before anyone asked what a two-person team would do with it'

const h = vi.hoisted(() => ({
  detail: {
    key: 'chat-7-x', title: 'Launch plan', running: false, queue: [] as unknown[],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    messages: [
      { role: 'user', content: 'Plan the launch week.', ts: '2026-09-26T09:00:00+00:00' },
      { role: 'assistant', content: 'Here is the plan.', ts: '2026-09-26T09:00:20+00:00' },
    ] as unknown[],
  } as Record<string, unknown>,
  chatSessionDetail: vi.fn(),
  usageTotals: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    usageTotals: h.usageTotals,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
  }
  // The page reads ~40 endpoints; anything not named above resolves to a shape-agnostic value.
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
  Element.prototype.scrollIntoView = vi.fn() as unknown as Element['scrollIntoView']
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
  h.navigate.mockReset()
  h.chatSessionDetail.mockReset().mockResolvedValue({ ...h.detail })
  h.usageTotals.mockReset().mockResolvedValue({
    session: 'chat-7-x',
    totals: { input_tokens: 41_000, output_tokens: 5_000, cost_usd: 0.0123, turns: 3, priced: true },
  })
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const open = (detail: Record<string, unknown> = {}) => {
  h.chatSessionDetail.mockResolvedValue({ ...h.detail, ...detail })
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-7-x" navigate={h.navigate} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

/** The header, once the chat has opened. */
async function header(): Promise<HTMLElement> {
  const banner = await screen.findByRole('banner')
  await waitFor(() => expect(within(banner).getByText('Launch plan')).toBeTruthy())
  return banner
}
const row = (banner: HTMLElement) => banner.querySelector<HTMLElement>('[data-header-left]')!
const contextLine = (banner: HTMLElement) => banner.querySelector<HTMLElement>('[data-header-below]')

describe('an app-started chat', () => {
  it('says "Started by <App>" in the header, with what that means as its tooltip', async () => {
    open({ created_by_app: 'probe-alpha', created_by_app_name: 'Probe Alpha', app_auto_approves: false })
    const banner = await header()
    const line = contextLine(banner)
    expect(line, 'the header has no context line for an app-started chat').not.toBeNull()
    const chip = within(line!).getByText('Started by Probe Alpha')
    // The same words, and the same tooltip, as the history row's (`StartedByApp`).
    expect(chip.closest('[title]')?.getAttribute('title'))
      .toBe("Probe Alpha started this chat. A turn in it runs with Probe Alpha's permissions.")
  })

  it('falls back to the app id when the server sent no name', async () => {
    open({ created_by_app: 'probe-alpha', app_auto_approves: false })
    const line = contextLine(await header())
    expect(within(line!).getByText('Started by probe-alpha')).toBeTruthy()
  })
})

describe('one of your chats', () => {
  it('names no app anywhere in the header', async () => {
    open()
    const banner = await header()
    expect(within(banner).queryByText(/^Started by /)).toBeNull()
  })

  it('keeps a single row when there is nothing to say about it', async () => {
    h.usageTotals.mockResolvedValue({ session: 'chat-7-x', totals: { input_tokens: 0, output_tokens: 0, cost_usd: 0, turns: 0, priced: true } })
    open()
    const banner = await header()
    await waitFor(() => expect(h.usageTotals).toHaveBeenCalled())
    expect(contextLine(banner), 'an empty context line still takes a strip of the header').toBeNull()
  })
})

describe('the title row holds only what names the chat', () => {
  it('puts the chips on the context line, not beside the title', async () => {
    open({ forked_from: 'dashboard:chat-1-x', forked_from_title: PARENT })
    const banner = await header()
    const line = await waitFor(() => {
      const l = contextLine(banner)
      expect(within(l!).getByText(/tokens$/)).toBeTruthy()
      return l!
    })
    // The branch chip truncates on the line, and its whole sentence is still its name.
    const branch = within(line).getByRole('button', { name: `Branched from "${PARENT}" — open the original` })
    expect(branch.querySelector('.truncate')?.textContent).toBe(`Branched from ${PARENT}`)
    // Nothing about the chat is left in the title's row: the way back, the title, regenerate.
    const inRow = within(row(banner)).getAllByRole('button').map((b) => b.getAttribute('aria-label') || b.getAttribute('title'))
    expect(inRow).toEqual(['Back to chat history', 'Rename chat', 'Regenerate title'])
  })

  it('makes "Copy chat link" a control of the cluster', async () => {
    open()
    const banner = await header()
    const copy = within(banner).getByRole('button', { name: 'Copy chat link' })
    expect(row(banner).contains(copy), 'copy-link is still wedged beside the title').toBe(false)
  })
})
