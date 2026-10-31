// @module-tag tree-scan
/**
 * A chat's turns sit in one centred reading column whose width is the `--reading-width` token
 * (#3820: "Reading stays around 720 to 780 px").
 *
 * Before, the turn column took `--content-width`, the page-width preset, and the shipped preset was
 * `full`, so at 1280 the turns ran the width of the window. The preset is gone (#5718): every page's
 * main section spans the pane, and the chat's turns read at their own width.
 *
 * jsdom lays nothing out, so this resolves what the browser would: the column's `max-width` is
 * followed through the stylesheet's declarations, with the custom properties the appearance store
 * writes on `<html>` laid over them (an inline declaration wins), at each width the issue names
 * (1280, and the compact 320, 390 and 412), and with each preset an older version may have saved.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { join } from 'node:path'
import { filesUnder, readSource } from '../../test/sourceTree'
import { runState } from '../../test/runState'
import { cascade, loadTokenSheets, resolveValue, topLevelBlocks, declarations } from '../../design/tokenSheet'
import type { HistMsg } from './chatTypes'

const TOKEN = '--reading-width'
/** The approved band (#3820's approved update) and the issue's proposed default inside it. */
const BAND = { min: 720, max: 780 }
const DEFAULT = '768px'
/** The widths #3820 names: its column test's desktop, and the compact widths it stacks at. */
const DESKTOP = 1280
const COMPACT = [320, 390, 412] as const

const SESSION = 'chat-reading-width'
const MESSAGES: HistMsg[] = [
  { role: 'user', content: 'Plan the launch week.', ts: '2026-10-20T09:00:00Z' },
  { role: 'assistant', content: 'Here is the week, day by day.', ts: '2026-10-20T09:00:04Z' },
]

const hd = vi.hoisted(() => ({ detail: null as unknown, pending: false }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: () => (hd.pending ? new Promise(() => {}) : Promise.resolve(hd.detail)),
    useCaseSettings: () => Promise.resolve({}),
    personalclawConfig: () => Promise.resolve({ voice: {} }),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
    themes: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
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

/** A window `width` px wide, as far as a media query can tell: `(max-width: 768px)` matches at
 *  390 and not at 1280. Every other query (reduced motion, colour scheme) matches nothing. */
function atWidth(width: number): void {
  const matches = (query: string) => {
    const max = /max-width:\s*([\d.]+)px/.exec(query)
    const min = /min-width:\s*([\d.]+)px/.exec(query)
    if (!max && !min) return false
    return (!max || width <= Number(max[1])) && (!min || width >= Number(min[1]))
  }
  vi.stubGlobal('innerWidth', width)
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: matches(query), media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    dispatchEvent: () => false,
  }))
}

/** What `value` resolves to in the browser: the sheet's declarations, with each custom property
 *  the page wrote inline on `<html>` laid over its declared value. */
function resolved(value: string): string {
  const map = cascade(loadTokenSheets(), 'dark')
  const inline = document.documentElement.style
  for (const name of [...map.keys()]) {
    const v = inline.getPropertyValue(name).trim()
    if (v) map.set(name, v)
  }
  return resolveValue(map, value)
}

/** Opens the chat; `saved` is a content-width preset an older version stored, which nothing reads now. */
async function openChat(saved?: string): Promise<void> {
  if (saved) localStorage.setItem('appearance', JSON.stringify({ widthPreset: saved }))
  const { ChatPage } = await import('../ChatPage')
  const { AppearanceProvider } = await import('../../app/appearance')
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

/** The box the turns sit in: the parent of every rendered turn. */
async function turnColumn(): Promise<HTMLElement> {
  await screen.findByText('Here is the week, day by day.')
  const turns = [...document.querySelectorAll<HTMLElement>('[data-transcript-turn]')]
  expect(turns.length, 'both turns rendered').toBe(2)
  const column = turns[0].parentElement!
  for (const t of turns) expect(t.parentElement, 'every turn sits in the one column').toBe(column)
  return column
}

/** `column`'s cap resolves to the reading width, and is read from the token itself, so another
 *  width that happens to equal it cannot pass for it. Returns the cap. */
function expectReadingWidth(column: HTMLElement): string {
  const cap = resolved(column.style.maxWidth)
  expect(cap, `the column's max-width (${column.style.maxWidth}) resolves to the reading width`).toBe(DEFAULT)
  expect(column.style.maxWidth, 'and takes it from the token').toBe(`var(${TOKEN})`)
  return cap
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
  localStorage.clear()
  sessionStorage.clear()
  document.documentElement.removeAttribute('style')
  hd.pending = false
  hd.detail = {
    key: SESSION, title: SESSION, messages: MESSAGES, run_state: runState({ session: SESSION }), queue: [],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the reading width is one token', () => {
  const SRC = join(process.cwd(), 'src')
  const sheets = filesUnder(SRC, (name) => name.endsWith('.css'))

  it(`is declared once, in tokens.css, at ${DEFAULT}, inside the approved ${BAND.min} to ${BAND.max} px`, () => {
    const found: string[] = []
    for (const file of sheets) {
      for (const { selector, body } of topLevelBlocks(readSource(file))) {
        for (const d of declarations(body)) {
          if (d.name === TOKEN) found.push(`${file.slice(SRC.length + 1)} ${selector}: ${d.value}`)
        }
      }
    }
    expect(found, `${TOKEN} is declared in exactly one place`).toEqual([`design/tokens.css @theme: ${DEFAULT}`])
    const px = Number(/^(\d+)px$/.exec(DEFAULT)![1])
    expect(px).toBeGreaterThanOrEqual(BAND.min)
    expect(px).toBeLessThanOrEqual(BAND.max)
  })

})

describe('the turn column is the reading width', () => {
  it(`at ${DESKTOP} px the column is ${DEFAULT}, not the window`, async () => {
    atWidth(DESKTOP)
    await openChat()
    const column = await turnColumn()
    expect(document.documentElement.style.getPropertyValue('--content-width'), 'no page-width preset is written').toBe('')
    expect(document.documentElement.style.getPropertyValue(TOKEN), 'the appearance store leaves the reading width alone').toBe('')
    const cap = expectReadingWidth(column)
    expect(Number.parseFloat(cap), 'and binds: it is narrower than the window').toBeLessThan(DESKTOP)
  })

  for (const saved of ['narrow', 'default', 'wide', 'full'] as const) {
    it(`at ${DESKTOP} px with a ${saved} preset an older version saved, the column is still ${DEFAULT}`, async () => {
      atWidth(DESKTOP)
      await openChat(saved)
      expectReadingWidth(await turnColumn())
      expect(document.documentElement.style.getPropertyValue('--content-width'), 'and the saved preset is not read').toBe('')
    })
  }

  for (const width of COMPACT) {
    it(`at ${width} px the column reads the same token, wider than the screen, so it takes the screen's width`, async () => {
      atWidth(width)
      await openChat()
      const column = await turnColumn()
      const cap = expectReadingWidth(column)
      expect(Number.parseFloat(cap)).toBeGreaterThan(width)
      expect(column.style.width, 'no fixed width that could outgrow a phone').toBe('')
      expect(column.style.minWidth).toBe('')
    })
  }

  it(`while the history loads, the placeholder turns sit in the same ${DEFAULT} column`, async () => {
    atWidth(DESKTOP)
    hd.pending = true
    await openChat()
    const placeholder = await screen.findByRole('status', { busy: true })
    await waitFor(() => expect(placeholder.style.maxWidth).not.toBe(''))
    expectReadingWidth(placeholder)
  })
})
