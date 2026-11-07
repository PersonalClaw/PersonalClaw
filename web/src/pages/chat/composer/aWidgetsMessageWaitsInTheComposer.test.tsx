import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor, within } from '@testing-library/react'
import { EditorView } from '@codemirror/view'
import { runState, runningState } from '../../../test/runState'
import { publishWidgetAction } from '../../../ui/widget/actionTurn'
import { registerBuiltinContentTypes } from '../../../ui/content/registerBuiltins'
import { activateInFrame, pressFromFrame, pressIntoFrame } from '../../../test/pressIntoFrame'
import { DialogHost } from '../../../ui/dialog/DialogHost'
import { confirm } from '../../../ui/dialog'

// ── A widget's message waits in the composer for the owner, through the rendered chat ──────────
//
// A frame's action fills the chat's composer, unsent. For a moment after, its Send, Steer, Queue
// and Enter are held: they are off and say why, and only the hold's timer, the owner's edit or a
// press in the editor ends it. A press on Send, Steer or Queue is not one, so the click that
// follows it sends nothing, and where the fill turned Stop into Steer at the same spot, a press
// meant to stop the turn steers nothing either.
//
// Driven through ChatPage, as the owner meets it; each request is read where it leaves the page.

const SESSION = 'chat-widget-waits'
const ACTION = '[UI] refresh: {"range":"30d"}'

const h = vi.hoisted(() => ({
  calls: [] as Array<[string, unknown[]]>,
  detail: {} as Record<string, unknown>,
  /** What the composer handed the mic recorder for hands-free: its buffer and submit. */
  handsFree: null as null | { onBuffer?: (t: string) => void; onSubmit?: (t: string) => void },
}))

// The mic recorder as the composer drives it, with hands-free's own two callbacks kept so a case
// can speak into the draft as dictation does.
vi.mock('../../../ui/composer/useMicRecorder', async (orig) => {
  const real = await orig<typeof import('../../../ui/composer/useMicRecorder')>()
  return {
    ...real,
    useMicRecorder: (...args: Parameters<typeof real.useMicRecorder>) => {
      h.handsFree = (args[3] as typeof h.handsFree) ?? null
      return real.useMicRecorder(...args)
    },
  }
})

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
    sendChat: () => Promise.resolve({ disposition: 'started', message_id: 'msg_01jbxq3c6m8n9p2r4s5t6v7w8x', accepted_at: '2026-11-01T09:01:00+00:00' }),
    cancelQueued: () => Promise.resolve({ ok: true }),
    // One saved prompt with a variable, so the palette's fill-in (and its Send) opens.
    prompts: () => Promise.resolve([{ name: 'brief', title: 'Brief', kind: 'user', variables: [{ name: 'topic' }] }]),
    prompt: () => Promise.resolve({ name: 'brief', title: 'Brief', kind: 'user', merged_variables: [{ name: 'topic' }] }),
    renderPrompt: () => Promise.resolve({ rendered: 'Summarize it in three lines.' }),
    optimizePrompt: () => Promise.resolve({ changed: true, optimized: 'A tidier ask, please' }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy({} as Record<string, unknown>, {
    get(_t, p) {
      if (typeof p !== 'string') return undefined
      return (...args: unknown[]) => {
        h.calls.push([p, args])
        return p in answers ? answers[p](...args) : Promise.resolve([])
      }
    },
  })
  return { ...real, api }
})

class FakeSocket {
  static open: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { FakeSocket.open.push(this); setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

let ChatPage: typeof import('../../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../../app/appearance')['AppearanceProvider']

const chat = (over: Record<string, unknown> = {}) => ({
  key: SESSION, title: 'Sales this month', run_state: runState({ session: SESSION }), queue: [],
  task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  messages: [
    { role: 'user', content: 'Show me the sales view.', ts: '2026-11-01T09:00:00+00:00' },
    { role: 'assistant', content: 'Here it is.', ts: '2026-11-01T09:00:05+00:00' },
  ],
  ...over,
})

const sends = () => h.calls.filter(([name]) => name === 'sendChat').map(([, args]) => args)
const settle = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)) })
let hostSeq = 900

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
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:composer-waits'
    URL.revokeObjectURL = () => {}
  }
  sessionStorage.clear()
  h.calls.length = 0
  h.detail = chat()
  ;({ ChatPage } = await import('../../ChatPage'))
  ;({ AppearanceProvider } = await import('../../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function open(text = /Here it is/) {
  render(<AppearanceProvider><ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
  await screen.findByText(text)
  await settle(60)
  // The owner is not in the composer, so a widget's message fills it rather than waiting.
  act(() => { (document.activeElement as HTMLElement | null)?.blur?.() })
}

/** A frame's action, from a frame the owner gave its focus unless *earned* says otherwise. */
function offer(frame = 'Sales', earned = true) {
  act(() => { publishWidgetAction(ACTION, { frame, host: `frame-${++hostSeq}`, ...(earned ? { earned } : {}) }) })
}

/** A browser's press on *el*: its pointerdown (which the composer's capture sees), then its click. */
function press(el: Element) {
  fireEvent.pointerDown(el)
  fireEvent.click(el, { detail: 1 })
}

const composerText = () => document.querySelector('[data-composer-region] .cm-content')?.textContent ?? ''

/** The frame's message, landed in the composer: nothing sent before the owner presses. */
async function landed(text: string) {
  await settle()
  expect(sends(), 'nothing sent before the owner presses').toEqual([])
  await waitFor(() => expect(composerText()).toContain(text))
}

/** The frame's message, offered as a chip naming *frame*: nothing sent, nothing put in the composer. */
async function chipped(frame: string) {
  await settle()
  expect(sends(), 'nothing sent before the owner presses').toEqual([])
  await waitFor(() => expect(screen.getByTestId('widget-suggestion').textContent).toContain(frame))
}

describe('a widget\'s message in the chat composer', () => {
  it('🔴 is not sent by a press on Send in the moment after it lands', async () => {
    // The press's own pointerdown doesn't end the hold, so its click sends nothing.
    await open()
    offer()
    await landed('[UI] refresh')
    const send = screen.getByRole('button', { name: 'Send message' })
    press(send)
    await settle()
    expect(sends(), 'the hold is still on').toEqual([])
    // It says why it is held, where the owner looks.
    expect(send.getAttribute('title')).toBe('Send message — Read the message the widget put here first')
    expect(screen.getByTestId('widget-draft-from').textContent).toContain('From “Sales”')
  })

  it('🔴 is not steered into a running turn by a press on Steer in the moment after it lands', async () => {
    // The fill turns Stop into Steer at the same spot: a press there steers nothing.
    h.detail = chat({ run_state: runningState({ session: SESSION, steerable: true }) })
    await open()
    offer()
    await landed('[UI] refresh')
    const steer = screen.getByRole('button', { name: /^Steer/ })
    press(steer)
    await settle()
    expect(sends()).toEqual([])
    expect(steer.getAttribute('title')).toContain('Read the message the widget put here first')
  })

  it('sends it, marked, once the owner has pressed in the editor', async () => {
    await open()
    offer()
    await landed('[UI] refresh')
    const editor = document.querySelector('[data-composer-region] .cm-content') as HTMLElement
    fireEvent.pointerDown(editor)
    await settle()
    press(screen.getByRole('button', { name: 'Send message' }))
    await settle()
    expect(sends()).toHaveLength(1)
    expect(sends()[0][0]).toBe(ACTION)
    expect(sends()[0][2]).toEqual({ widget_action: true })
  })

  it('sends it, marked, once the hold has run out', async () => {
    await open()
    offer()
    await landed('[UI] refresh')
    await settle(1100)
    press(screen.getByRole('button', { name: 'Send message' }))
    await settle()
    expect(sends()).toHaveLength(1)
    expect(sends()[0][2]).toEqual({ widget_action: true })
  })

  it('🔴 comes back marked from a queued message\'s Edit', async () => {
    // The gateway says a queued message came from a widget (`widget_action` on the queue item):
    // its Edit gives it back to the draft still marked, after a reload as well as live.
    h.detail = chat({
      run_state: runningState({ session: SESSION }),
      queue: [{ id: 'q-1', content: ACTION, widget_action: true }],
    })
    await open()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit queued message' }))
    await settle()
    expect(composerText()).toContain('[UI] refresh')
    expect(screen.getByTestId('widget-draft-from')).toBeTruthy()
    expect(h.calls.some(([name, args]) => name === 'cancelQueued' && args[1] === 'q-1')).toBe(true)
  })

  it.each([[true], [false]])('marks the palette\'s combined send only while the draft holds a widget\'s message (%s)', async (withWidget) => {
    await open()
    if (withWidget) {
      offer()
      await landed('[UI] refresh')
    }
    fireEvent.click(screen.getByRole('button', { name: 'Add to message' }))
    fireEvent.click(await screen.findByRole('button', { name: /Saved prompts/ }))
    // The saved prompt's row (the menu also offers to brief an agent).
    fireEvent.click(await screen.findByRole('button', { name: /^Brief\s*1 var$/ }))
    await settle(60)
    const fill = await screen.findByRole('dialog', { name: 'Brief' })
    fireEvent.click(within(fill).getByRole('button', { name: /^\s*Send\s*$/ }))
    await settle(60)
    expect(sends()).toHaveLength(1)
    const [text, , meta] = sends()[0] as [string, string, Record<string, unknown> | undefined]
    expect(text).toContain('Summarize it in three lines.')
    expect(meta?.widget_action === true).toBe(withWidget)
  })

  it('sends the palette\'s combined send unmarked once the × has taken out all of a widget\'s message', async () => {
    await open()
    offer()
    await landed('[UI] refresh')
    fireEvent.click(screen.getByRole('button', { name: /^Take out the message/ }))
    await waitFor(() => expect(composerText()).not.toContain('[UI] refresh'))
    expect(screen.queryByTestId('widget-draft-from')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Add to message' }))
    fireEvent.click(await screen.findByRole('button', { name: /Saved prompts/ }))
    fireEvent.click(await screen.findByRole('button', { name: /^Brief\s*1 var$/ }))
    await settle(60)
    const fill = await screen.findByRole('dialog', { name: 'Brief' })
    fireEvent.click(within(fill).getByRole('button', { name: /^\s*Send\s*$/ }))
    await settle(60)
    expect(sends()).toHaveLength(1)
    const [, , meta] = sends()[0] as [string, string, Record<string, unknown> | undefined]
    expect(meta?.widget_action).toBeUndefined()
  })

  it('says on the notice line that a frame the owner turned down stays turned down', async () => {
    await open()
    const frameHost = `frame-${++hostSeq}`
    act(() => { publishWidgetAction(ACTION, { frame: 'Sales', host: frameHost }) })
    await chipped('Sales')
    fireEvent.click(screen.getByRole('button', { name: /^Dismiss/ }))
    // The owner presses into the frame again: its post is theirs, and goes nowhere.
    act(() => { publishWidgetAction(ACTION, { frame: 'Sales', host: frameHost, earned: true }) })
    expect((await screen.findByText('“Sales” is turned down while this chat is open.')).closest('[role="status"]')).toBeTruthy()
    expect(composerText()).not.toContain('[UI] refresh')
    expect(sends()).toEqual([])
  })

  it('drops Revert and the follow-ups when hands-free speaks into an optimized draft, and sends the spoken words as the owner\'s', async () => {
    await open()
    const editor = EditorView.findFromDOM(document.querySelector('[data-composer-region] .cm-editor') as HTMLElement)
    act(() => { editor?.dispatch({ changes: { from: 0, insert: 'tidy this' } }) })
    fireEvent.click(screen.getByRole('button', { name: 'Optimize prompt (⌘↵)' }))
    await waitFor(() => expect(composerText()).toContain('A tidier ask, please'))
    expect(screen.getByRole('button', { name: /revert to original/ })).toBeTruthy()
    frame('chat_followups', { items: ['Show a code example'] })
    expect(await screen.findByRole('button', { name: 'Show a code example' })).toBeTruthy()
    expect(h.handsFree?.onBuffer, 'premise: hands-free is wired').toBeTruthy()
    act(() => { h.handsFree?.onBuffer?.('check the logs') })
    await waitFor(() => expect(composerText()).toContain('check the logs'))
    expect(screen.queryByRole('button', { name: /revert to original/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Show a code example' })).toBeNull()
    act(() => { h.handsFree?.onSubmit?.('check the logs') })
    await settle()
    expect(sends()).toHaveLength(1)
    const [text, , meta] = sends()[0] as [string, string, Record<string, unknown> | undefined]
    expect(text).toBe('check the logs')
    expect(meta).toEqual({})
    expect(await screen.findByText('check the logs', { selector: '[data-transcript-turn="user"] *' })).toBeTruthy()
  })

  it('🔴 is not queued by a press on Queue in the moment after it lands', async () => {
    h.detail = chat({ run_state: runningState({ session: SESSION, steerable: false }) })
    await open()
    offer()
    await landed('[UI] refresh')
    const queue = screen.getByRole('button', { name: /^Queue/ })
    press(queue)
    await settle()
    expect(sends()).toEqual([])
    expect(queue.getAttribute('title')).toContain('Read the message the widget put here first')
  })

  it('offers a frame\'s message as a chip when the owner didn\'t give the frame its focus, and sends nothing', async () => {
    await open()
    offer('Sales', false)
    await chipped('Sales')
    expect(composerText()).not.toContain('[UI] refresh')
    await settle(1100)
    expect(sends()).toEqual([])
  })

  /** The chat with two widget frames in its transcript, Sales and then Notes. */
  async function twoFrames() {
    registerBuiltinContentTypes()
    h.detail = chat({
      messages: [
        { role: 'user', content: 'Show me both views.', ts: '2026-11-01T09:00:00+00:00' },
        { role: 'assistant', content: 'Here they are.\n\n<widget title="Sales"><button data-action="refresh">Refresh</button></widget>\n\n<widget title="Notes"><button data-action="note">Note</button></widget>', ts: '2026-11-01T09:00:05+00:00' },
      ],
    })
    await open(/Here they are/)
    return waitFor(() => {
      const sales = document.querySelector('iframe[title="Sales"]') as HTMLIFrameElement | null
      const notes = document.querySelector('iframe[title="Notes"]') as HTMLIFrameElement | null
      if (!sales || !notes) throw new Error('the transcript rendered fewer than two widget frames')
      return { sales, notes }
    })
  }
  const postFrom = (frame: HTMLIFrameElement, action: string) => act(() => {
    window.dispatchEvent(new MessageEvent('message', { data: { type: 'widget-action', action }, source: frame.contentWindow as MessageEventSource }))
  })

  it('🔴 offers the second frame\'s message as a chip when the owner presses one frame and then the other', async () => {
    const { sales, notes } = await twoFrames()
    act(() => { pressIntoFrame(sales) })
    postFrom(sales, 'refresh')
    await landed('[UI] refresh')
    act(() => { pressFromFrame(sales, notes) })
    postFrom(notes, 'note')
    await chipped('Notes')
    expect(composerText()).not.toContain('[UI] note')
    await settle(1100)
    expect(sends()).toEqual([])
  })

  it('🔴 offers as a chip the message of a frame an assistive technology activated', async () => {
    const { sales } = await twoFrames()
    act(() => { activateInFrame(sales) })
    postFrom(sales, 'refresh')
    await chipped('Sales')
    expect(composerText()).not.toContain('[UI] refresh')
    expect(sends()).toEqual([])
  })

  it('🔴 offers as a chip the message of a frame that took focus under a resting pointer, or after a Tab the page kept', async () => {
    const { sales, notes } = await twoFrames()
    // A frame loads under the pointer where it rests, and takes focus itself.
    act(() => { sales.dispatchEvent(new Event('load')) })
    act(() => { sales.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })) })
    act(() => { sales.focus(); window.dispatchEvent(new Event('blur')) })
    postFrom(sales, 'refresh')
    await chipped('Sales')
    expect(composerText()).not.toContain('[UI] refresh')
    act(() => { fireEvent.click(screen.getByRole('button', { name: /^Dismiss/ })) })
    // The composer's own Tab (a mention menu's pick, which it keeps), then a frame takes focus.
    const editor = document.querySelector('[data-composer-region] .cm-content') as HTMLElement
    act(() => { sales.dispatchEvent(new MouseEvent('mouseout', { bubbles: true })); editor.focus(); window.dispatchEvent(new Event('focus')) })
    await settle(2100)
    act(() => { editor.blur() })
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    tab.preventDefault()
    act(() => { editor.dispatchEvent(tab) })
    act(() => { notes.focus(); window.dispatchEvent(new Event('blur')) })
    postFrom(notes, 'note')
    await chipped('Notes')
    expect(composerText()).not.toContain('[UI] note')
    expect(sends()).toEqual([])
  })

  it.each([
    ['fills the composer from a frame the pointer came to rest over a moment after it loaded', false],
    ['🔴 offers as a chip the message of a frame the transcript\'s growing just moved under the pointer', true],
  ])('%s', async (_name, grew) => {
    // The transcript's column is watched for its size: what grows above a frame moves it.
    const watched: Array<{ target: Element; resized: ResizeObserverCallback; self: ResizeObserver }> = []
    vi.stubGlobal('ResizeObserver', class {
      constructor(private resized: ResizeObserverCallback) {}
      observe(target: Element): void { watched.push({ target, resized: this.resized, self: this as unknown as ResizeObserver }) }
      unobserve(): void {}
      disconnect(): void {}
    })
    const { sales } = await twoFrames()
    act(() => { sales.dispatchEvent(new Event('load')) })
    await settle(700)
    if (grew) {
      // Only what watches the scroller's in-flow content: the scroller's own box never changes size.
      const scroller = document.querySelector('[data-transcript-scroll]') as HTMLElement
      const around = watched.filter((w) => w.target !== scroller && scroller.contains(w.target) && w.target.contains(sales))
      expect(around.length, 'premise: something around the frame watches its size').toBeGreaterThan(0)
      act(() => { for (const w of around) w.resized([{ target: w.target, contentRect: w.target.getBoundingClientRect() } as ResizeObserverEntry], w.self) })
    }
    act(() => { sales.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })) })
    act(() => { sales.focus(); window.dispatchEvent(new Event('blur')) })
    postFrom(sales, 'refresh')
    if (grew) {
      await chipped('Sales')
      expect(composerText()).not.toContain('[UI] refresh')
    } else {
      await landed('[UI] refresh')
    }
    expect(sends()).toEqual([])
  })

  it('🔴 offers as a chip the message of a frame a dialog just closed over', async () => {
    render(<DialogHost />)
    const { sales } = await twoFrames()
    act(() => { sales.dispatchEvent(new Event('load')) })
    await settle(700)
    // A dialog over the frames goes away; the pointer rests where it was.
    let asked: Promise<boolean> = Promise.resolve(false)
    act(() => { asked = confirm({ title: 'Something else', body: 'b', confirmLabel: 'OK', cancelLabel: 'Cancel' }) })
    await screen.findByRole('dialog', { name: 'Something else' })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await asked
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Something else' })).toBeNull())
    act(() => { sales.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })) })
    act(() => { sales.focus(); window.dispatchEvent(new Event('blur')) })
    postFrom(sales, 'refresh')
    await chipped('Sales')
    expect(composerText()).not.toContain('[UI] refresh')
  })

  it('🔴 offers as a chip the message of a frame the prompt palette just closed over', async () => {
    const { sales } = await twoFrames()
    act(() => { sales.dispatchEvent(new Event('load')) })
    await settle(700)
    // The palette (a ui/Modal) over the frames stays a while, then goes; the pointer rests where it was.
    fireEvent.click(screen.getByRole('button', { name: 'Add to message' }))
    fireEvent.click(await screen.findByRole('button', { name: /Saved prompts/ }))
    const palette = await screen.findByRole('dialog', { name: 'Insert a prompt' })
    await settle(700)
    fireEvent.click(within(palette).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Insert a prompt' })).toBeNull())
    act(() => { sales.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })) })
    act(() => { sales.focus(); window.dispatchEvent(new Event('blur')) })
    postFrom(sales, 'refresh')
    await chipped('Sales')
    expect(composerText()).not.toContain('[UI] refresh')
  })

  it('🔴 offers as a chip the message of a frame the find bar just moved under the pointer', async () => {
    // The transcript watches its content for its size, the find bar's as well as the column's.
    const watched: Array<{ target: Element; resized: ResizeObserverCallback; self: ResizeObserver }> = []
    vi.stubGlobal('ResizeObserver', class {
      constructor(private resized: ResizeObserverCallback) {}
      observe(target: Element): void { watched.push({ target, resized: this.resized, self: this as unknown as ResizeObserver }) }
      unobserve(): void {}
      disconnect(): void {}
    })
    registerBuiltinContentTypes()
    h.detail = chat({
      messages: [
        { role: 'user', content: 'Show me the sales view.', ts: '2026-11-01T09:00:00+00:00' },
        { role: 'assistant', content: 'Here it is.\n\n<widget title="Sales"><button data-action="refresh">Refresh</button></widget>', ts: '2026-11-01T09:00:05+00:00' },
      ],
    })
    render(<AppearanceProvider><ChatPage sub={SESSION} navigate={() => {}} query={{ find: 'Here' }} setQuery={() => {}} /></AppearanceProvider>)
    const bar = await screen.findByRole('search')
    const sales = await waitFor(() => {
      const f = document.querySelector('iframe[title="Sales"]') as HTMLIFrameElement | null
      if (!f) throw new Error('no widget frame')
      return f
    })
    await settle(60)
    act(() => { (document.activeElement as HTMLElement | null)?.blur?.() })
    act(() => { sales.dispatchEvent(new Event('load')) })
    await settle(700)
    // The bar opened above the frames: what watches it sees it, and the frames moved.
    // Only what watches the scroller's in-flow content: the scroller's own box never changes size.
    const scroller = document.querySelector('[data-transcript-scroll]') as HTMLElement
    const around = watched.filter((w) => w.target !== scroller && scroller.contains(w.target) && w.target.contains(bar) && w.target.contains(sales))
    expect(around.length, 'premise: something watches the bar and the frames').toBeGreaterThan(0)
    act(() => { for (const w of around) w.resized([{ target: w.target, contentRect: w.target.getBoundingClientRect() } as ResizeObserverEntry], w.self) })
    act(() => { sales.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })) })
    act(() => { sales.focus(); window.dispatchEvent(new Event('blur')) })
    postFrom(sales, 'refresh')
    await chipped('Sales')
    expect(composerText()).not.toContain('[UI] refresh')
  })

  it('fills the composer from a widget frame\'s own post in the transcript, and sends nothing', async () => {
    registerBuiltinContentTypes()
    h.detail = chat({
      messages: [
        { role: 'user', content: 'Show me the sales view.', ts: '2026-11-01T09:00:00+00:00' },
        { role: 'assistant', content: 'Here it is.\n\n<widget title="Sales"><button data-action="refresh">Refresh</button></widget>', ts: '2026-11-01T09:00:05+00:00' },
      ],
    })
    await open()
    const iframe = await waitFor(() => {
      const f = document.querySelector('iframe[title="Sales"]') as HTMLIFrameElement | null
      if (!f) throw new Error('the transcript rendered no widget frame')
      return f
    })
    act(() => { pressIntoFrame(iframe) })
    act(() => {
      window.dispatchEvent(new MessageEvent('message', { data: { type: 'widget-action', action: 'refresh' }, source: iframe.contentWindow as MessageEventSource }))
    })
    await landed('[UI] refresh')
    await settle(1100)
    expect(sends()).toEqual([])
  })

  /** A frame the gateway sends this chat over its socket. */
  const frame = (type: string, data: Record<string, unknown>) => act(() => {
    for (const sock of FakeSocket.open) sock.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) })
  })
  /** Select the text of *el* and press the selection's Quote. */
  function quote(el: HTMLElement) {
    Range.prototype.getBoundingClientRect ??= () => ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) })
    const range = document.createRange()
    range.selectNodeContents(el)
    act(() => { const sel = window.getSelection(); sel?.removeAllRanges(); sel?.addRange(range) })
    act(() => { fireEvent.mouseUp(document) })
    fireEvent.mouseDown(screen.getByRole('button', { name: /^Quote/ }))
  }
  const WIDGETS = '[UI] open: {"href":"https://x.example/a"}'
  /** Send the draft as the owner would, once its hold is over, and read what went. */
  async function sendTheDraft() {
    fireEvent.pointerDown(document.querySelector('[data-composer-region] .cm-content') as HTMLElement)
    await settle(1100)
    press(screen.getByRole('button', { name: 'Send message' }))
    await settle()
    return sends().at(-1) as [string, string, Record<string, unknown> | undefined]
  }

  it('🔴 marks the palette\'s combined send of a picked follow-up whose words are the widget\'s rewrite', async () => {
    // An Optimize rewrites the widget's words; a follow-up whose words are exactly the rewrite
    // replaces the draft with those same words, which are still the widget's.
    await open()
    offer()
    await landed('[UI] refresh')
    fireEvent.click(screen.getByRole('button', { name: 'Optimize prompt (⌘↵)' }))
    await waitFor(() => expect(composerText()).toContain('A tidier ask, please'))
    frame('chat_followups', { items: ['A tidier ask, please'] })
    fireEvent.click(await screen.findByRole('button', { name: 'A tidier ask, please' }))
    await waitFor(() => expect(screen.queryByTestId('widget-draft-from'), 'premise: the pick took the chip').toBeNull())
    expect(composerText()).toBe('A tidier ask, please')
    fireEvent.click(screen.getByRole('button', { name: 'Add to message' }))
    fireEvent.click(await screen.findByRole('button', { name: /Saved prompts/ }))
    fireEvent.click(await screen.findByRole('button', { name: /^Brief\s*1 var$/ }))
    await settle(60)
    const fill = await screen.findByRole('dialog', { name: 'Brief' })
    fireEvent.click(within(fill).getByRole('button', { name: /^\s*Send\s*$/ }))
    await settle(60)
    expect(sends()).toHaveLength(1)
    const [text, , meta] = sends()[0] as [string, string, Record<string, unknown> | undefined]
    expect(text).toContain('A tidier ask, please')
    expect(meta?.widget_action).toBe(true)
  })

  it('🔴 quotes as a widget\'s the turn a slash command writes from a widget\'s draft', async () => {
    await open()
    offer()
    await landed('[UI] refresh')
    // The owner types /goal in front of it: a started chat answers that here, and sends nothing.
    const editor = EditorView.findFromDOM(document.querySelector('[data-composer-region] .cm-editor') as HTMLElement)
    act(() => { editor?.dispatch({ changes: { from: 0, insert: '/goal ' } }) })
    await waitFor(() => expect(composerText()).toContain('/goal [UI] refresh'))
    fireEvent.pointerDown(document.querySelector('[data-composer-region] .cm-content') as HTMLElement)
    await settle(1100)
    press(screen.getByRole('button', { name: 'Send message' }))
    await settle()
    expect(sends()).toEqual([])
    const bubble = await waitFor(() => {
      const el = document.querySelector('[data-from-widget]')
      if (!el?.textContent?.includes('/goal')) throw new Error('no widget-marked turn holds the command')
      return el as HTMLElement
    })
    quote(bubble)
    await waitFor(() => expect(composerText()).toContain('A widget said:'))
    expect(screen.getByTestId('widget-draft-from').textContent).toContain('Holds a widget’s words')
  })

  it('🔴 quotes a widget\'s message sent live as a widget\'s, where it shares its place in the map with an earlier turn', async () => {
    // A tool-using history merges assistant rows, so its last turn's place runs ahead of the list,
    // and the next turn the page adds live lands on it.
    FakeSocket.open.length = 0
    h.detail = chat({
      messages: [
        { role: 'user', content: 'run the build', ts: '2026-11-01T09:00:00+00:00' },
        { role: 'assistant', content: 'starting', ts: '2026-11-01T09:00:01+00:00' },
        { role: 'assistant', content: 'done, 0 errors', ts: '2026-11-01T09:00:02+00:00' },
        { role: 'user', content: 'now the tests', ts: '2026-11-01T09:00:03+00:00' },
        { role: 'assistant', content: 'all green', ts: '2026-11-01T09:00:04+00:00' },
      ],
    })
    await open(/all green/)
    frame('chat_user_message', { content: WIDGETS, ts: '2026-11-01T09:01:00+00:00', message_id: 'msg_01jbxq3c6m8n9p2r4s5t6v7w8z', widget_action: true })
    frame('chat_done', { outcome: 'complete' })
    const bubble = await screen.findByText(WIDGETS)
    quote(bubble)
    await waitFor(() => expect(composerText()).toContain('A widget said:'))
    expect(screen.getByTestId('widget-draft-from').textContent).toContain('Holds a widget’s words')
    const [, , meta] = await sendTheDraft()
    expect(meta?.widget_action).toBe(true)
  })

  it('🔴 quotes an earlier widget\'s message as a widget\'s, where a turn the page added live shares its place', async () => {
    FakeSocket.open.length = 0
    h.detail = chat({
      messages: [
        { role: 'user', content: 'run the build', ts: '2026-11-01T09:00:00+00:00' },
        { role: 'assistant', content: 'starting', ts: '2026-11-01T09:00:01+00:00' },
        { role: 'assistant', content: 'still going', ts: '2026-11-01T09:00:02+00:00' },
        { role: 'assistant', content: 'done, 0 errors', ts: '2026-11-01T09:00:03+00:00' },
        { role: 'user', content: WIDGETS, ts: '2026-11-01T09:00:04+00:00', meta: { widget_action: true } },
      ],
    })
    await open(/done, 0 errors/)
    frame('chat_user_message', { content: 'and the tests?', ts: '2026-11-01T09:01:00+00:00', message_id: 'msg_01jbxq3c6m8n9p2r4s5t6v7w8y' })
    frame('chat_chunk', { content: 'Running them now.' })
    await screen.findByText(/Running them now/)
    quote(screen.getByText(WIDGETS))
    await waitFor(() => expect(composerText()).toContain('A widget said:'))
    expect(screen.getByTestId('widget-draft-from').textContent).toContain('Holds a widget’s words')
  })

  it.each([
    ['its mark', { widget_action: true }],
    ['a genui control\'s label', { ui_label: 'Show the view' }],
  ])('🔴 names a widget\'s message in a rewound tail as a widget\'s, by %s, and quotes it marked', async (_by, meta) => {
    FakeSocket.open.length = 0
    h.detail = chat({
      messages: [
        { role: 'user', content: 'Show me the sales view, again.', ts: '2026-11-01T09:00:00+00:00', rewound: [{ messages: [
          { role: 'user', content: 'Show me the sales view.' },
          { role: 'assistant', content: 'Here it is.' },
          { role: 'user', content: WIDGETS, meta },
        ] }] },
        { role: 'assistant', content: 'Here it is, again.', ts: '2026-11-01T09:00:05+00:00' },
      ],
    })
    await open(/Here it is, again/)
    fireEvent.click(screen.getByRole('button', { name: /^View/ }))
    const row = await screen.findByText(WIDGETS)
    expect(row.parentElement?.textContent?.startsWith('You'), 'the widget\'s message labelled as the owner\'s').toBe(false)
    expect(row.parentElement?.textContent?.startsWith('A widget')).toBe(true)
    quote(row)
    await waitFor(() => expect(composerText()).toContain('A widget said:'))
    expect(screen.getByTestId('widget-draft-from').textContent).toContain('Holds a widget’s words')
  })
})
