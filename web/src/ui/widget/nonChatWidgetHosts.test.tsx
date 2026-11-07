/** The gap this closes: a widget action raised OUTSIDE a chat used to be dropped on
 *  the floor. Both non-chat hosts are driven here — the
 *  artifact-library preview and the dashboard tile band — and each must land its
 *  `[UI]` turn in a chat session through the ONE `ne:launch-chat` path.
 *
 *  A second way into chat would be the dual path this repo forbids, so the assertion
 *  is deliberately narrow: `launchChat` is called, and the staged turn is exactly the
 *  text the widget produced.
 *
 *  And it lands only on the owner's press. A frame's own script can post an action itself,
 *  so the shell asks first, in the page's own DOM, naming the frame and showing the exact
 *  text, and stages nothing until the owner answers yes. */
import { render, act, waitFor, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { IframeHtmlPreview } from '../content/renderers'
import { PinnedTiles } from '../../pages/dashboard/PinnedTiles'
import { DialogHost } from '../dialog/DialogHost'
import { ReactWidgetFrame } from './ReactWidgetFrame'
import { ARM_MS } from '../dialog/DialogShell'
import { closeDialog, subscribeDialogs } from '../dialog/dialogStore'
import { Markdown } from '../Markdown'
import { registerBuiltinContentTypes } from '../content/registerBuiltins'
import { forgetPendingWidgetAction, takePendingWidgetAction, useWidgetActionLauncher } from './useWidgetActionBridge'
import { activateInFrame, pressFromFrame, pressIntoFrame } from '../../test/pressIntoFrame'
import { api } from '../../lib/api'

// The JSX compile runs in the code editor's language worker, which jsdom cannot host; the
// component passes through as written.
vi.mock('./reactJsx', () => ({ transformJsx: async (jsx: string) => ({ code: jsx }) }))

/** Each `ne:launch-chat` the shell raised, by its detail: the nonce its new chat drains by. */
const launched: { widgetAction?: string }[] = []
window.addEventListener('ne:launch-chat', (e) => { launched.push({ ...(e as CustomEvent).detail }) })
const lastNonce = () => launched[launched.length - 1]?.widgetAction

vi.mock('../../app/appSdk', () => ({
  // The bridge raises ne:launch-chat itself. A launch through the apps' helper is recorded as one
  // too, so any way a turn is staged without the owner's yes shows in `launched`.
  launchChat: () => { launched.push({}) },
  notify: () => {},
}))

const WIDGET_HTML = '<button data-action="refresh" data-payload=\'{"range":"30d"}\'>Refresh</button>'

vi.mock('../../lib/api', () => ({
  api: {
    artifactExists: vi.fn(async () => false),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
    dashboardViews: vi.fn(async () => [
      { id: 'overview', tiles: [{ ref: 'artifact:sales-view', order: 0, added_by: 'user' }] },
    ]),
    artifact: vi.fn(async () => ({ slug: 'sales-view', name: 'Sales', content: WIDGET_HTML })),
    resolveTile: vi.fn(async () => ({})),
  },
}))

beforeAll(() => {
  // A fresh URL per document, as a browser's is, so a new document is a new src.
  let blobs = 0
  URL.createObjectURL = () => `blob:non-chat-host-test-${++blobs}`
  URL.revokeObjectURL = () => {}
})

/** The dialogs open in the shell's dialog store. */
function openDialogs(): { id: number }[] {
  let seen: { id: number }[] = []
  subscribeDialogs((list) => { seen = list as typeof seen })()
  return seen
}

beforeEach(async () => {
  launched.length = 0
  forgetPendingWidgetAction()
  for (const d of openDialogs()) closeDialog(d.id, false)
  await act(async () => { await Promise.resolve() })
  localStorage.clear()
})

/** Wait out the ask's arming: its entrance (at most 600 ms), then ARM_MS. In two steps, since
 *  React renders what a timer set only when the `act` around it ends. */
async function waitForArming() {
  await act(async () => { await new Promise((r) => setTimeout(r, 650)) })
  await act(async () => { await new Promise((r) => setTimeout(r, ARM_MS + 100)) })
}

/** The app shell — the only place the non-chat fallback is registered. */
function Shell({ children }: { children: React.ReactNode }) {
  useWidgetActionLauncher()
  return <>{children}<DialogHost /></>
}

/** The shell's ask, as the owner sees it: its title, and the exact text it would send. Nothing is
 *  staged while it waits for the owner's answer. */
async function theAsk() {
  expect(launched, 'nothing staged before the owner answers').toEqual([])
  const dialog = await screen.findByRole('dialog')
  return { dialog, text: screen.getByTestId('widget-action-text').textContent }
}

/** Mount a preview in the shell, with its frame holding focus as a click in it would leave it. */
async function preview(title = 'Sales', content = WIDGET_HTML) {
  const view = render(<Shell><IframeHtmlPreview content={content} mode="dark" title={title} /></Shell>)
  await act(async () => {})
  const iframe = view.container.querySelector('iframe') as HTMLIFrameElement
  expect(iframe).not.toBeNull()
  act(() => { pressIntoFrame(iframe) })
  return { view, iframe, child: iframe.contentWindow }
}

/** A pointer press on the ask's button, as a browser's is: a pointerdown, then a click with
 *  `detail: 1` (a keyboard's click has `detail: 0`, and needs the button to hold focus). */
async function answer(name: RegExp) {
  const button = screen.getByRole('button', { name })
  await act(async () => { fireEvent.pointerDown(button); fireEvent.click(button, { detail: 1 }) })
}

function postFrom(child: Window | null, data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, source: child as MessageEventSource }))
  })
}

const action = { type: 'widget-action', action: 'refresh', payload: { range: '30d' } }

describe('artifact-library preview', () => {
  it('🔴 asks the owner first, focused on "Don’t send", and lands the turn only on their yes', async () => {
    // Red before: the post alone opened a chat and staged the turn to send, without the owner's yes.
    const { child } = await preview()
    postFrom(child, action)
    expect(launched).toEqual([])
    const ask = await theAsk()
    expect(ask.dialog.getAttribute('aria-label')).toBe('A widget titled “Sales” asks to send this')
    expect(ask.text).toBe('[UI] refresh: {"range":"30d"}')
    expect(screen.getByTestId('widget-action-size').textContent).toBe('29 characters · 1 line')
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /^Don’t send$/ }))
    // A screen reader hears what it would send; a keyboard reaches the box.
    const describedBy = ask.dialog.getAttribute('aria-describedby')
    expect(describedBy && document.getElementById(describedBy)?.textContent).toContain('[UI] refresh')
    expect(screen.getByTestId('widget-action-text').tabIndex).toBe(0)
    expect(screen.getByRole('region', { name: 'The message it would send' })).toBeTruthy()
    // A second post while the ask is open is not a second ask.
    postFrom(child, action)
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    await waitForArming()
    await answer(/^Send in a new chat$/)
    expect(launched).toHaveLength(1)
    expect(takePendingWidgetAction(lastNonce())?.text).toBe('[UI] refresh: {"range":"30d"}')
  })

  it('🔴 takes no Enter, no early click and no press begun before it armed as a yes', async () => {
    // Focus starts on "Don't send", and no Enter from the window, early click or press begun
    // before it armed answers yes.
    const { child } = await preview()
    postFrom(child, action)
    await theAsk()
    fireEvent.keyDown(window, { key: 'Enter' })
    fireEvent.keyDown(document.activeElement as Element, { key: 'Enter' })
    await answer(/^Send in a new chat$/)
    expect(screen.queryByRole('dialog'), 'still asking').not.toBeNull()
    expect(launched).toEqual([])
    const send = screen.getByRole('button', { name: /^Send in a new chat$/ })
    expect(send.getAttribute('aria-disabled')).toBe('true')
    // A press that begins before arming and ends after it is not a yes.
    fireEvent.pointerDown(send)
    await waitForArming()
    await act(async () => { fireEvent.click(send, { detail: 1 }) })
    expect(launched).toEqual([])
    // Away and back re-arms it.
    act(() => { window.dispatchEvent(new Event('blur')); window.dispatchEvent(new Event('focus')) })
    await answer(/^Send in a new chat$/)
    expect(launched, 'refocused a moment ago').toEqual([])
    await act(async () => { await new Promise((r) => setTimeout(r, ARM_MS + 100)) })
    await answer(/^Send in a new chat$/)
    expect(launched).toHaveLength(1)
  })

  it('🔴 asks nothing for a post made while the owner types in the page', async () => {
    const { iframe, child } = await preview()
    const field = document.createElement('input')
    document.body.appendChild(field)
    act(() => { field.focus() })
    postFrom(child, action)
    await act(async () => {})
    expect(openDialogs()).toEqual([])
    act(() => { pressIntoFrame(iframe) })
    postFrom(child, action)
    expect((await theAsk()).text).toBe('[UI] refresh: {"range":"30d"}')
    field.remove()
  })

  it('🔴 keeps a "Don’t send" for that frame while it is mounted, and another frame still asks', async () => {
    const view = render(
      <Shell>
        <IframeHtmlPreview content={WIDGET_HTML} mode="dark" title="Sales" />
        <IframeHtmlPreview content={'<button data-action="note">Note</button>'} mode="dark" title="Notes" />
      </Shell>
    )
    await act(async () => {})
    const [sales, notes] = Array.from(view.container.querySelectorAll('iframe'))
    act(() => { pressIntoFrame(sales) })
    postFrom(sales.contentWindow, action)
    await theAsk()
    await answer(/^Don’t send$/)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    // The frame reloading itself is the same mount.
    act(() => { sales.dispatchEvent(new Event('load')) })
    act(() => { pressIntoFrame(sales) })
    postFrom(sales.contentWindow, action)
    await act(async () => {})
    expect(openDialogs()).toEqual([])
    act(() => { pressIntoFrame(notes) })
    postFrom(notes.contentWindow, { type: 'widget-action', action: 'note' })
    expect((await theAsk()).text).toBe('[UI] note')
    expect(launched).toEqual([])
  })

  it('🔴 asks for a frame pressed straight after another, which the page sees no blur for', async () => {
    const view = render(
      <Shell>
        <IframeHtmlPreview content={WIDGET_HTML} mode="dark" title="Sales" />
        <IframeHtmlPreview content={'<button data-action="note">Note</button>'} mode="dark" title="Notes" />
      </Shell>
    )
    await act(async () => {})
    const [sales, notes] = Array.from(view.container.querySelectorAll('iframe'))
    act(() => { pressIntoFrame(sales) })
    act(() => { pressFromFrame(sales, notes) })
    postFrom(notes.contentWindow, { type: 'widget-action', action: 'note' })
    expect((await theAsk()).text).toBe('[UI] note')
  })

  it('🔴 asks for a frame an assistive technology activated, with no Tab and no pointer', async () => {
    const { iframe, child } = await preview()
    const field = document.createElement('input')
    document.body.appendChild(field)
    act(() => { iframe.dispatchEvent(new MouseEvent('mouseout', { bubbles: true })); field.focus(); window.dispatchEvent(new Event('focus')) })
    act(() => { activateInFrame(iframe) })
    postFrom(child, action)
    expect((await theAsk()).text).toBe('[UI] refresh: {"range":"30d"}')
    field.remove()
  })

  it('🔴 leaves focus in the frame once the owner says no, never on the page\'s body', async () => {
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => {})
    try {
      const { iframe, child } = await preview()
      postFrom(child, action)
      await theAsk()
      await answer(/^Don’t send$/)
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
      await act(async () => { await new Promise((r) => setTimeout(r, 50)) })
      expect(document.activeElement).toBe(iframe)
      expect(debug).not.toHaveBeenCalled()
    } finally {
      debug.mockRestore()
    }
  })

  it('🔴 keeps a "Don’t send" through a new document in the same frame, and asks again after a remount', async () => {
    const view = render(<Shell><IframeHtmlPreview content={WIDGET_HTML} mode="dark" title="Sales" /></Shell>)
    await act(async () => {})
    const first = view.container.querySelector('iframe') as HTMLIFrameElement
    const src = first.getAttribute('src')
    act(() => { pressIntoFrame(first) })
    postFrom(first.contentWindow, action)
    await theAsk()
    await answer(/^Don’t send$/)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    // New content in the same preview: the same iframe, with a new document.
    view.rerender(<Shell><IframeHtmlPreview content={`${WIDGET_HTML}<p>v2</p>`} mode="dark" title="Sales" /></Shell>)
    await act(async () => {})
    const same = view.container.querySelector('iframe') as HTMLIFrameElement
    expect(same, 'premise: the same element').toBe(first)
    expect(same.getAttribute('src'), 'premise: a new document').not.toBe(src)
    act(() => { pressIntoFrame(same) })
    postFrom(same.contentWindow, action)
    await act(async () => {})
    expect(openDialogs()).toEqual([])
    // A remount is a new frame, and asks.
    view.unmount()
    const again = render(<Shell><IframeHtmlPreview content={WIDGET_HTML} mode="dark" title="Sales" /></Shell>)
    await act(async () => {})
    const fresh = again.container.querySelector('iframe') as HTMLIFrameElement
    act(() => { pressIntoFrame(fresh) })
    postFrom(fresh.contentWindow, action)
    expect((await theAsk()).text).toBe('[UI] refresh: {"range":"30d"}')
  })

  it('shows every line of a long message, says how much there is, and cues what is below', async () => {
    const tall = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight')
    const shown = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight')
    const isBox = (el: HTMLElement) => el.dataset?.testid === 'widget-action-text'
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, get() { return isBox(this) ? 2000 : 0 } })
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get() { return isBox(this) ? 240 : 0 } })
    try {
      const { child } = await preview()
      const lines = Array.from({ length: 40 }, (_, n) => `line ${n}`).join('\n')
      postFrom(child, { type: 'widget-action', action: 'note', payload: { text: lines } })
      const ask = await theAsk()
      expect(ask.text).toContain('line 39')
      expect(screen.getByTestId('widget-action-more').textContent).toBe('More below')
      // The sheet scrolls on a short screen, and the box is capped within it.
      expect(ask.dialog.className).toContain('max-h-[calc(100dvh-2*var(--spacing-2xl))]')
      expect(ask.dialog.className).toContain('overflow-y-auto')
      expect(screen.getByTestId('widget-action-text').className).toContain('max-h-[min(15rem,35dvh)]')
    } finally {
      if (tall) Object.defineProperty(HTMLElement.prototype, 'scrollHeight', tall)
      if (shown) Object.defineProperty(HTMLElement.prototype, 'clientHeight', shown)
    }
  })

  it('sends nothing when the owner says no', async () => {
    const { child } = await preview()
    postFrom(child, action)
    await theAsk()
    await answer(/^Don’t send$/)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(launched).toEqual([])
    expect(takePendingWidgetAction(lastNonce())).toBeNull()
  })

  it('still refuses a forged action from another window', async () => {
    await preview()
    const foreign = document.createElement('iframe')
    document.body.appendChild(foreign)
    postFrom(foreign.contentWindow, action)
    await act(async () => {})
    expect(launched).toEqual([])
    expect(openDialogs()).toEqual([])
  })
})

describe('dashboard tile band', () => {
  it('🔴 asks the owner first, and lands the [UI] turn via ne:launch-chat only on their yes', async () => {
    const view = render(<Shell><PinnedTiles /></Shell>)
    await act(async () => {})
    const iframe = view.container.querySelector('[data-testid="pinned-tiles"] iframe') as HTMLIFrameElement | null
    expect(iframe, 'the tile band rendered no widget frame').not.toBeNull()
    act(() => { pressIntoFrame(iframe) })
    postFrom((iframe as HTMLIFrameElement).contentWindow, action)
    expect(launched).toEqual([])
    const ask = await theAsk()
    expect(ask.text).toBe('[UI] refresh: {"range":"30d"}')
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /^Don’t send$/ }))
    await waitForArming()
    await answer(/^Send in a new chat$/)
    expect(launched).toHaveLength(1)
    expect(takePendingWidgetAction(lastNonce())?.text).toBe('[UI] refresh: {"range":"30d"}')
  })
  it('🔴 asks for a tile pressed straight after a click in another tile', async () => {
    vi.mocked(api.dashboardViews).mockResolvedValueOnce([
      { id: 'overview', tiles: [{ ref: 'artifact:sales-view', order: 0, added_by: 'user' }, { ref: 'artifact:other-view', order: 1, added_by: 'user' }] },
    ] as never)
    const view = render(<Shell><PinnedTiles /></Shell>)
    await act(async () => {})
    const tiles = await waitFor(() => {
      const found = Array.from(view.container.querySelectorAll('[data-testid="pinned-tiles"] iframe')) as HTMLIFrameElement[]
      if (found.length < 2) throw new Error('the tile band rendered fewer than two frames')
      return found
    })
    act(() => { pressIntoFrame(tiles[0]) })
    act(() => { pressFromFrame(tiles[0], tiles[1]) })
    postFrom(tiles[1].contentWindow, action)
    expect((await theAsk()).text).toBe('[UI] refresh: {"range":"30d"}')
    expect(launched).toEqual([])
  })
})

describe('a peeked chat\'s widget (SessionPeek renders <Markdown widgets>, and claims no bridge)', () => {
  it('reaches the shell\'s ask, as every other non-chat host does', async () => {
    // What renders a `<widget>` block in Markdown, as the app registers it at start.
    registerBuiltinContentTypes()
    const view = render(<Shell><Markdown widgets>{`Here:\n\n<widget title="Peek">${WIDGET_HTML}</widget>`}</Markdown></Shell>)
    const iframe = await waitFor(() => {
      const f = view.container.querySelector('iframe')
      if (!f) throw new Error('the peek rendered no widget frame')
      return f
    })
    act(() => { pressIntoFrame(iframe) })
    postFrom(iframe.contentWindow, action)
    const ask = await theAsk()
    expect(ask.dialog.getAttribute('aria-label')).toBe('A widget titled “Peek” asks to send this')
    expect(launched).toEqual([])
  })
})

describe('react widget host', () => {
  it('does not forward actions — its child document has no human-gesture gate', async () => {
    const view = render(<Shell><ReactWidgetFrame jsx="const App = () => null" title="R" /></Shell>)
    // A frame must exist, or a post "from" it proves nothing: a null source is refused anyway.
    const iframe = await waitFor(() => {
      const f = view.container.querySelector('iframe')
      if (!f) throw new Error('the react host rendered no widget frame')
      return f
    })
    const child = iframe.contentWindow
    expect(child).not.toBeNull()
    postFrom(child, action)
    expect(launched).toEqual([])
    expect(openDialogs()).toEqual([])
  })
})
