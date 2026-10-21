/**
 * Code, a table and a diff in a reply open beside the reading column, in a split that remembers its
 * size, and the address names what is open (#3820's approved update: "Code, tables, diffs,
 * artifacts, and review can use an adjustable split that remembers its size. Compact layouts stack
 * the work and artifact without clipping either one. The URL records reading or artifact mode and
 * the selected artifact.").
 *
 * Before, a block could only be read where it sat, at the reading width: a wide table or a long
 * diff scrolled sideways inside the 768 px column. Now each block's own Open beside puts it in the
 * chat's pane host, beside the turns. `?beside=<block>` is the split: absent, the thread reads in
 * its column alone; present, it names the block by what it is, so a reload, Back and a copied link
 * restore both, for a reply the page streamed as for one it loaded.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { runState, runningState } from '../../test/runState'
import type { HistMsg } from './chatTypes'

const SESSION = 'chat-beside'
const REPLY = [
  'Here is the script.',
  '',
  '```python',
  'print("hello")',
  '```',
  '',
  '| Day | Task |',
  '|---|---|',
  '| Mon | Write the brief |',
  '',
  '```diff',
  '-old line',
  '+new line',
  '```',
].join('\n')
const MESSAGES: HistMsg[] = [
  { role: 'user', content: 'Show me the script, the week and the change.', ts: '2026-10-20T09:00:00Z' },
  { role: 'assistant', content: REPLY, ts: '2026-10-20T09:00:04Z' },
]

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
  static last: FakeSocket | null = null
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { FakeSocket.last = this; setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

/** A window `width` px wide, as far as a media query and a laid-out box can tell. */
let boxWidth = 1280
const watchers = new Set<() => void>()
function atWidth(width: number): void {
  boxWidth = width
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

function pushFrame(type: string, data: Record<string, unknown>) {
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })
}

type Write = { patch: Record<string, string | null | undefined>; replace: boolean }
let writes: Write[] = []

/** The chat at `initial`, holding its address the way the router does: a patch merges, null drops. */
/** The router's own move, as Back makes it: the chat is handed another address in place. */
let route: (query: Record<string, string>) => void = () => {}
function Chat({ initial }: { initial: Record<string, string> }) {
  const [query, setQuery] = useState(initial)
  route = setQuery
  return (
    <ChatPageUnderTest sub={SESSION} navigate={() => {}} query={query}
      setQuery={(patch: Record<string, string | null | undefined>, opts?: { replace?: boolean }) => {
        writes.push({ patch, replace: !!opts?.replace })
        setQuery((prev) => {
          const next = { ...prev }
          for (const [k, v] of Object.entries(patch)) { if (v === null || v === undefined || v === '') delete next[k]; else next[k] = v }
          return next
        })
      }} />
  )
}
let ChatPageUnderTest: typeof import('../ChatPage').ChatPage

async function openChat(initial: Record<string, string> = {}, { waitFor = 'Here is the script.' }: { waitFor?: string | null } = {}) {
  ChatPageUnderTest = (await import('../ChatPage')).ChatPage
  const { AppearanceProvider } = await import('../../app/appearance')
  const view = render(<AppearanceProvider><Chat initial={initial} /></AppearanceProvider>)
  if (waitFor) await screen.findByText(waitFor)
  return view
}

const openBeside = (name: string) => screen.getByRole('button', { name })
const besidePane = (name: string) => screen.getByRole('region', { name })
const layout = () => document.querySelector<HTMLElement>('[data-pane-layout]')
/** The ref the last Open beside wrote. */
const lastBeside = () => {
  const w = [...writes].reverse().find((x) => 'beside' in x.patch)
  expect(w, 'an Open beside wrote the address').toBeDefined()
  return w!
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
  watchers.clear()
  vi.stubGlobal('ResizeObserver', class {
    fire: () => void
    constructor(callback: ResizeObserverCallback) { this.fire = () => callback([], this as unknown as ResizeObserver) }
    observe() { watchers.add(this.fire) }
    unobserve() { watchers.delete(this.fire) }
    disconnect() { watchers.delete(this.fire) }
  })
  // Only the chat's pane host is laid out: it is the box whose room the split reads. Every other box
  // keeps jsdom's empty one, so nothing else (the composer's editor) starts measuring text.
  const box = HTMLElement.prototype.getBoundingClientRect
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.getAttribute('data-pane-host') !== 'chat') return box.call(this)
    return { width: boxWidth, height: 800, top: 0, left: 0, right: boxWidth, bottom: 800, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
  })
  localStorage.clear()
  sessionStorage.clear()
  writes = []
  atWidth(1280)
  hd.detail = {
    key: SESSION, title: SESSION, messages: MESSAGES, run_state: runState({ session: SESSION }), queue: [],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('a block in a reply opens beside the reading column', () => {
  it('offers Open beside on the code, the table and the diff, and the thread reads alone until one is used', async () => {
    await openChat()
    expect(openBeside('Open python code beside')).toBeInTheDocument()
    expect(openBeside('Open table beside')).toBeInTheDocument()
    expect(openBeside('Open diff beside')).toBeInTheDocument()
    expect(layout(), 'reading mode: no split').toBeNull()
  })

  it('opens the code beside the turns, which keep the reading width, and names it in the address', async () => {
    await openChat()
    fireEvent.click(openBeside('Open python code beside'))
    const w = lastBeside()
    expect(w.replace, 'opening is a step Back can undo').toBe(false)
    expect(w.patch.beside).toMatch(/\S/)
    const pane = besidePane('python code')
    expect(pane).toHaveTextContent('print("hello")')
    expect(layout()).toHaveAttribute('data-pane-layout', 'beside')
    const turns = document.querySelectorAll<HTMLElement>('[data-transcript-turn]')
    expect(turns[0].parentElement!.style.maxWidth, 'the turns still read at the reading width').toBe('var(--reading-width)')
    expect(pane.contains(turns[0]), 'the turns are beside the pane, not in it').toBe(false)
    expect(within(pane).queryByRole('button', { name: /beside$/ }), 'the pane does not offer itself again').toBeNull()
  })

  it('opens the table and the diff the same way, each under its own name', async () => {
    await openChat()
    fireEvent.click(openBeside('Open table beside'))
    expect(besidePane('Table')).toHaveTextContent('Write the brief')
    fireEvent.click(openBeside('Open diff beside'))
    expect(besidePane('Diff')).toHaveTextContent('+new line')
    expect(screen.queryByRole('region', { name: 'Table' }), 'one block at a time').toBeNull()
  })

  it('opens the same block from a copied address or a reload, before anything is clicked', async () => {
    const first = await openChat()
    fireEvent.click(openBeside('Open diff beside'))
    const ref = lastBeside().patch.beside!
    first.unmount()
    writes = []
    await openChat({ beside: ref })
    expect(await screen.findByRole('region', { name: 'Diff' })).toHaveTextContent('-old line')
    expect(writes.filter((x) => 'beside' in x.patch), 'restoring writes nothing').toEqual([])
  })

  it('closes from its own Close, which gives the focus back to its Open beside', async () => {
    await openChat()
    const opener = openBeside('Open python code beside')
    opener.focus()
    fireEvent.click(opener)
    expect(besidePane('python code')).toBeInTheDocument()
    const close = screen.getByRole('button', { name: 'Close code' })
    close.focus()
    fireEvent.click(close)
    expect(screen.queryByRole('region', { name: 'python code' })).toBeNull()
    expect(lastBeside().patch.beside).toBeNull()
    expect(document.activeElement, 'the block was not drawn again under the focus').toBe(opener)
  })

  it('closes on Back: the address without it, in the same page', async () => {
    await openChat()
    fireEvent.click(openBeside('Open python code beside'))
    expect(besidePane('python code')).toBeInTheDocument()
    act(() => route({}))
    expect(screen.queryByRole('region', { name: 'python code' })).toBeNull()
    expect(layout(), 'reading mode again').toBeNull()
  })

  it('says so when the address names a block this chat does not have', async () => {
    await openChat({ beside: 'code-0' })
    expect(await screen.findByRole('region', { name: 'Not in this chat' })).toHaveTextContent('That block is not in this chat.')
  })
})

describe('only the answer offers its blocks beside the reading', () => {
  it("the narration folded into the turn's work offers none, since folding the work unmounts it", async () => {
    hd.detail = {
      ...(hd.detail as Record<string, unknown>),
      messages: [
        MESSAGES[0],
        { role: 'assistant', content: 'First I list the folder:\n\n```sh\nls notes\n```', ts: '2026-10-20T09:00:01Z' },
        { role: 'tool', content: 'list_dir', cls: 'msg msg-tool', meta: { tool_call_id: 'tc1', purpose: '', input: '{}', kind: 'read', detail: 'notes', done: true, output: 'a.md' } },
        MESSAGES[1],
      ],
    }
    await openChat()
    fireEvent.click(await screen.findByRole('button', { name: /^Worked/ }))
    expect(await screen.findByRole('group', { name: 'sh code' }), 'the narration is open').toHaveTextContent('ls notes')
    expect(screen.queryByRole('button', { name: 'Open sh code beside' }), 'the folded work offers none').toBeNull()
    expect(openBeside('Open python code beside'), 'the answer still does').toBeInTheDocument()
  })
})

describe('a reply the page streamed names its blocks as the reloaded reply does', () => {
  it('a block opened right after the reply finished opens again from a reload or a copied link', async () => {
    const asked = MESSAGES[0]
    hd.detail = { ...(hd.detail as Record<string, unknown>), messages: [asked], run_state: runningState({ session: SESSION }) }
    await openChat({}, { waitFor: null })
    await screen.findByRole('button', { name: 'Stop' })
    // What the gateway sends for the turn: what fed it, a call, then the answer, then its end.
    pushFrame('activity_event', { kind: 'context', text: 'Injected 1,204 chars of context' })
    pushFrame('tool_call', { tool_call_id: 'tc1', tool: 'read_file', kind: 'read', purpose: '', input_preview: '{}', detail: 'notes.md' })
    pushFrame('tool_result', { tool_call_id: 'tc1', output: 'ok' })
    pushFrame('chat_chunk', { content: REPLY, seq: 1 })
    hd.detail = {
      ...(hd.detail as Record<string, unknown>), run_state: runState({ session: SESSION }),
      messages: [
        asked,
        { role: 'tool', content: 'read_file', cls: 'msg msg-tool', meta: { tool_call_id: 'tc1', purpose: '', input: '{}', kind: 'read', detail: 'notes.md', done: true, output: 'ok' } },
        { ...MESSAGES[1], meta: { context_fed: { kind: 'context', text: 'Injected 1,204 chars of context' } } },
      ],
    }
    pushFrame('chat_done', { outcome: 'complete' })
    await screen.findByText('Here is the script.')
    fireEvent.click(await screen.findByRole('button', { name: 'Open python code beside' }))
    const live = lastBeside().patch.beside!
    cleanup()
    writes = []
    await openChat({ beside: live })
    expect(await screen.findByRole('region', { name: 'python code' })).toHaveTextContent('print("hello")')
    fireEvent.click(openBeside('Open python code beside'))
    expect(lastBeside().patch.beside, 'the reloaded reply names the block as the live one did').toBe(live)
  })
})

describe('the split remembers its size', () => {
  it('a drag changes the width, and a reload opens it at that width', async () => {
    const first = await openChat()
    fireEvent.click(openBeside('Open python code beside'))
    const h = screen.getByRole('separator', { name: 'Resize code — arrow keys to resize' })
    expect(h).toHaveAttribute('aria-valuenow', '480')
    fireEvent.pointerDown(h, { clientX: 800, pointerId: 1 })
    fireEvent.pointerMove(h, { clientX: 700, pointerId: 1 })
    fireEvent.pointerUp(h, { clientX: 700, pointerId: 1 })
    expect(h).toHaveAttribute('aria-valuenow', '580')
    const ref = lastBeside().patch.beside!
    expect(writes.some((x) => Object.values(x.patch).some((v) => v === '580')), 'the width stays out of the address').toBe(false)
    first.unmount()
    await openChat({ beside: ref })
    const again = await screen.findByRole('separator', { name: 'Resize code — arrow keys to resize' })
    expect(again).toHaveAttribute('aria-valuenow', '580')
    expect(besidePane('python code').style.width).toBe('580px')
  })
})

describe('a compact window stacks the block under the thread', () => {
  for (const width of [320, 390, 412]) {
    it(`at ${width} px the thread and the block stack, neither clipped`, async () => {
      atWidth(width)
      await openChat()
      fireEvent.click(openBeside('Open python code beside'))
      const pane = besidePane('python code')
      expect(layout()).toHaveAttribute('data-pane-layout', 'stacked')
      expect(pane.style.width, 'the block takes the window width').toBe('')
      expect(screen.queryByRole('separator', { name: /^Resize code/ }), 'no width to choose').toBeNull()
      const turns = document.querySelectorAll<HTMLElement>('[data-transcript-turn]')
      expect(turns[0].compareDocumentPosition(pane) & Node.DOCUMENT_POSITION_FOLLOWING, 'the block is under the thread').toBeTruthy()
      expect(pane).toBeVisible()
      expect(turns[1]).toBeVisible()
      expect(screen.getByRole('group', { name: 'python code, beside the conversation' }).style.paddingBottom,
        "its end clears reading mode's folded composer, which floats over the column's foot").toContain('var(--composer-fold-room')
    })
  }

  it('is taken out of reach with the thread while the phone session map covers both', async () => {
    atWidth(390)
    await openChat()
    fireEvent.click(openBeside('Open python code beside'))
    const ref = lastBeside().patch.beside!
    act(() => route({ beside: ref, map: '1' }))
    expect(besidePane('python code').closest('[inert]'), 'the block beside the thread is inert under the drawer').not.toBeNull()
    expect(document.querySelector('[data-transcript-turn]')!.closest('[inert]')).not.toBeNull()
  })

  it('goes back beside the thread when the window widens', async () => {
    atWidth(390)
    await openChat()
    fireEvent.click(openBeside('Open python code beside'))
    expect(layout()).toHaveAttribute('data-pane-layout', 'stacked')
    act(() => { atWidth(1280); for (const fire of watchers) fire() })
    expect(layout()).toHaveAttribute('data-pane-layout', 'beside')
    expect(besidePane('python code')).toHaveTextContent('print("hello")')
  })
})
