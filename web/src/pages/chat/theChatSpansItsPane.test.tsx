// @module-tag tree-scan
/**
 * A chat spans its pane (#5865, `web/REDESIGN.md` §5 convention 1): the turns, the composer and its
 * fold, the history's placeholder, and the new chat's hero with its prompts take the pane's whole
 * width, inside its inner padding.
 *
 * Before, every one of them was held to `--reading-width`, a 768 px column centred in the pane
 * (#3820), so at 1440 a thread used about half the window and wrapped tables and code early. The
 * column and its token are gone, and nothing on these surfaces is narrower than the pane but what
 * is relative to it (a sent message's bubble, 80% of the column) or caps one chip's label.
 *
 * jsdom lays nothing out, so this reads what decides the width: each column's own inline width and
 * centring, at the desktop width and the compact ones, and the column sources' fixed caps.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { join } from 'node:path'
import { filesUnder, readSource } from '../../test/sourceTree'
import { runState } from '../../test/runState'
import type { HistMsg } from './chatTypes'

const DESKTOP = 1440
const COMPACT = [320, 390, 412] as const

const SESSION = 'chat-spans-its-pane'
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

/** A window `width` px wide, as far as a media query can tell. */
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

async function openChat(sub: string): Promise<void> {
  const { ChatPage } = await import('../ChatPage')
  const { AppearanceProvider } = await import('../../app/appearance')
  render(
    <AppearanceProvider>
      <ChatPage sub={sub} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

/** `el` takes its container's width: no width cap or fixed width of its own, not centred in a
 *  narrower column, and full width where it is a flex column whose items would shrink to fit. */
function expectSpans(el: HTMLElement, what: string): void {
  expect(el.style.maxWidth, `${what} has no width cap`).toBe('')
  expect(el.style.width, `${what} has no fixed width`).toBe('')
  expect(el.style.minWidth, `${what} has no minimum width`).toBe('')
  const classes = el.className.split(/\s+/)
  expect(classes.filter((c) => c.startsWith('max-w-')), `${what} has no max-width class`).toEqual([])
  expect(classes, `${what} is not centred in a narrower column`).not.toContain('mx-auto')
  expect(classes, `${what} takes the full width`).toContain('w-full')
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

describe('a thread spans the pane', () => {
  for (const width of [DESKTOP, ...COMPACT]) {
    it(`at ${width} px the turns, the composer and its fold span the pane`, async () => {
      atWidth(width)
      await openChat(SESSION)
      expectSpans(await turnColumn(), 'the turn column')
      const composer = document.querySelector<HTMLElement>('[data-tour="chat"]')
      expect(composer, 'the composer rendered').not.toBeNull()
      expectSpans(composer!, 'the composer')
      const region = document.querySelector<HTMLElement>('[data-composer-region]')
      expect(region, 'the composer fold rendered').not.toBeNull()
      expectSpans(region!, 'the composer fold')
    })
  }

  it('while the history loads, the placeholder turns span the pane too', async () => {
    atWidth(DESKTOP)
    hd.pending = true
    await openChat(SESSION)
    const placeholder = await screen.findByRole('status', { busy: true })
    await waitFor(() => expect(placeholder.className).toContain('flex'))
    expectSpans(placeholder, 'the placeholder column')
  })
})

describe('a new chat spans the pane', () => {
  it('its hero column, under the greeting, spans the pane', async () => {
    atWidth(DESKTOP)
    await openChat('')
    const composer = await waitFor(() => {
      const c = document.querySelector<HTMLElement>('[data-tour="chat"]')
      expect(c).not.toBeNull()
      return c!
    })
    expectSpans(composer, 'the new chat\'s composer')
    // The hero's column is the composer stage's nearest flex column above it.
    let column = composer.parentElement
    while (column && !column.className.split(/\s+/).includes('flex-col')) column = column.parentElement
    expect(column, 'the hero column').not.toBeNull()
    expectSpans(column!, 'the hero column')
  })
})

describe('no chat surface holds a fixed column width', () => {
  const SRC = join(process.cwd(), 'src')
  // The files that lay out the chat's columns: the page, its transcript, composer and hero.
  const COLUMNS = [
    'pages/ChatPage.tsx',
    'pages/chat/transcript/Transcript.tsx',
    'pages/chat/transcript/MessagesSkeleton.tsx',
    'pages/chat/transcript/RewindDivider.tsx',
    'pages/chat/transcript/UserEditor.tsx',
    'pages/chat/composer/ChatComposer.tsx',
    'pages/chat/composer/ComposerFold.tsx',
    'pages/chat/hero/ChatHero.tsx',
    'pages/chat/hero/ExplainCard.tsx',
  ]
  /** A cap in a length: `maxWidth: 720`, `maxWidth: '768px'`, `max-w-[452px]`, a `var(...)` width. */
  const FIXED = [
    /maxWidth:\s*\d/,
    /maxWidth:\s*['"`][^'"`]*(?:px|rem|em|ch|var\()/,
    /max-w-\[\d+(?:\.\d+)?(?:px|rem|em|ch)\]/,
    /max-w-(?:xs|sm|md|lg|xl|[2-7]xl|prose|screen-\w+)\b/,
  ]

  it('the column files cap nothing in a fixed length', () => {
    const found: string[] = []
    for (const rel of COLUMNS) {
      readSource(join(SRC, rel)).split('\n').forEach((line, i) => {
        if (FIXED.some((re) => re.test(line))) found.push(`${rel}:${i + 1}: ${line.trim()}`)
      })
    }
    expect(found).toEqual([])
  })

  it('the reading-width token is gone, from every stylesheet and every chat source', () => {
    const named = filesUnder(SRC, (name) => /\.(css|tsx?)$/.test(name) && !/\.test\.tsx?$/.test(name))
      .filter((file) => readSource(file).includes('reading-width'))
      .map((file) => file.slice(SRC.length + 1))
    expect(named).toEqual([])
  })
})
