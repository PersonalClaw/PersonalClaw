/** The child→parent widget wire contract, asserted through the real hosts.
 *
 *  A widget runs in a sandboxed `allow-scripts` iframe off a blob (null) origin, so
 *  the ONLY thing it can reach in the host is `parent.postMessage`. That makes this
 *  file the test of a trust boundary, not of a helper: everything a widget can say
 *  arrives here as untrusted input and must be refused unless it matches the
 *  contract — `widget-height` / `widget-action` / `widget-error`, from THIS frame's
 *  own contentWindow.
 *
 *  jsdom caveat that shapes every fixture below: jsdom's own `postMessage` does not
 *  populate `event.source`, so a real child→parent post can't be replayed. Fixtures
 *  therefore construct the MessageEvent directly WITH `source`, which is also
 *  exactly how a same-page attacker would try to forge one. The child's click gate
 *  (`e.isTrusted`, in HOST_SCRIPT, asserted in widgetHostScript.test.ts) only stops its
 *  listener forwarding an untrusted click; the parent never sends what a frame posts.
 *
 *  An action is dropped unless its frame holds focus when it arrives (a real click in a frame
 *  focuses its iframe), so each fixture that expects one focuses the frame first. Whether the
 *  owner gave the frame that focus (`earned`) only picks where a chat puts it. */
import { render, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll, afterEach } from 'vitest'
import { useRef, useState } from 'react'
import { WidgetFrame } from './WidgetFrame'
import { coolWidgetHost, declineWidgetHost, forgetWidgetHost, useWidgetWire, widgetFramesMayHaveMoved } from './useWidgetActionBridge'
import { ReactWidgetFrame } from './ReactWidgetFrame'
import { api } from '../../lib/api'
import { activateInFrame, pressFromFrame, pressIntoFrame } from '../../test/pressIntoFrame'

// The JSX compile runs in the code editor's language worker, which jsdom cannot host. These
// tests assert the wire, so the component passes through as written.
vi.mock('./reactJsx', () => ({ transformJsx: async (jsx: string) => ({ code: jsx }) }))

vi.mock('../../lib/api', () => ({
  api: {
    artifactExists: vi.fn(async () => false),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
  },
}))

// jsdom ships no blob-URL factory; both frames render their iframe off one. The
// stub only has to be stable — these tests assert the WIRE, never the document.
beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:widget-wire-test'
    URL.revokeObjectURL = () => {}
  }
})

const published: string[] = []
const details: Record<string, unknown>[] = []
const record = (e: Event) => {
  published.push(String((e as CustomEvent).detail?.text))
  details.push({ ...(e as CustomEvent).detail })
}
beforeAll(() => window.addEventListener('ne:widget-action', record as EventListener))
afterEach(() => {
  published.length = 0
  details.length = 0
  delete (navigator as { userActivation?: unknown }).userActivation
})

/** The page's transient user activation, as `navigator.userActivation` reads it. */
function activation(isActive: boolean): void {
  Object.defineProperty(navigator, 'userActivation', { value: { isActive }, configurable: true })
}

/** Deliver `data` to the host as if it came from `source`. */
function post(data: unknown, source: Window | null): void {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, source: source as MessageEventSource }))
  })
}

/** A window that is not this frame's child — the forgery fixture. */
function foreignWindow(): Window | null {
  const el = document.createElement('iframe')
  document.body.appendChild(el)
  return el.contentWindow
}

describe('widget wire — WidgetFrame (the action producer)', () => {
  // Distinct html per test: WidgetFrame's height cache is module-level and keyed by
  // content hash, so a shared body would leak one test's height into the next.
  async function mount(html: string) {
    const view = render(<WidgetFrame html={html} title="W" />)
    await act(async () => {}) // settle the artifactExists probe
    const iframe = view.container.querySelector('iframe')
    if (!iframe) throw new Error('WidgetFrame rendered no iframe — the fixture never reached the wire')
    // What a real click inside the frame does in the parent.
    act(() => { pressIntoFrame(iframe) })
    expect(document.activeElement, 'premise: the frame holds focus').toBe(iframe)
    return { view, iframe, child: iframe.contentWindow }
  }

  it('turns a widget-action from THIS frame into an [UI] turn carrying the payload', async () => {
    const { child } = await mount('<button data-action="refresh">a</button>')
    post({ type: 'widget-action', action: 'refresh', payload: { a: 1 } }, child)
    expect(published).toEqual(['[UI] refresh: {"a":1}'])
  })

  it('🔴 hands a frame\'s action on as the frame\'s, never as a press in the page, whatever it claims', async () => {
    // Red before: it went on as a bare turn, which the chat sent at once. A frame's own script can
    // post this itself; only the owner's press may send it (the consumers).
    // Nothing the frame posts, at the top level or in its payload, can claim a press, a label, a
    // name, an artifact, or that the owner gave it its focus.
    const { iframe, child } = await mount('<button data-action="refresh">h</button>')
    // The pointer leaves, and the frame takes focus itself: nothing of the owner's earns it.
    leaveFrame(iframe)
    takeFocus(iframe)
    const spoof = { pressed: true, label: 'Approve', frame: 'Your bank', slug: 'other-view', host: 'frame-0', earned: true }
    post({ type: 'widget-action', action: 'refresh', payload: { a: 1, ...spoof }, ...spoof }, child)
    expect(details).toHaveLength(1)
    expect(details[0].earned).toBeUndefined()
    expect(details[0].pressed).toBeUndefined()
    expect(details[0].label).toBeUndefined()
    expect(details[0].slug).toBeUndefined()
    expect(details[0].frame).toBe('W')
    expect(details[0].host).toMatch(/^frame-\d+$/)
    expect(details[0].host).not.toBe('frame-0')
  })

  it('🔴 drops an action posted while the owner works elsewhere in the page', async () => {
    // Red before: any post from the frame went on. A real click in the frame gives it focus.
    const { iframe, child } = await mount('<button data-action="refresh">k</button>')
    const field = document.createElement('input')
    document.body.appendChild(field)
    act(() => { field.focus() })
    post({ type: 'widget-action', action: 'refresh' }, child)
    expect(published).toEqual([])
    act(() => { pressIntoFrame(iframe) })
    post({ type: 'widget-action', action: 'refresh' }, child)
    expect(published).toEqual(['[UI] refresh'])
    field.remove()
  })

  it('🔴 drops no action of a frame that holds focus, and marks it earned only when the owner gave the frame that focus', async () => {
    // Whether the owner gave the frame its focus decides only where a chat puts the action: into
    // an idle composer, or offered as a chip. Nothing is dropped for it, and the page leaves focus
    // where it is.
    const { iframe, child } = await mount('<button data-action="refresh">t</button>')
    const field = document.createElement('input')
    const next = document.createElement('button')
    document.body.append(field, next)
    // In the past, so no time this case sets is later than the next case's clock.
    const T0 = Date.now() - 100_000
    const now = vi.spyOn(Date, 'now')
    const at = (ms: number) => now.mockReturnValue(T0 + ms)
    const typeIn = (key: string) => act(() => { field.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })) })
    const arrive = (moved: boolean) => act(() => {
      if (moved) document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }))
      iframe.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
    })
    const leave = () => act(() => {
      iframe.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }))
      field.focus()
      window.dispatchEvent(new Event('focus'))
    })
    const steal = () => act(() => { iframe.focus(); window.dispatchEvent(new Event('blur')) })
    const earned = () => details.map((d) => d.earned === true)
    try {
      // The owner types with the pointer elsewhere, and the frame takes focus itself.
      leave(); at(0); typeIn('m'); at(300); steal()
      post({ type: 'widget-action', action: 'a' }, child)
      expect(published).toEqual(['[UI] a'])
      expect(earned()).toEqual([false])
      expect(document.activeElement, 'focus stays where it is').toBe(iframe)
      // The pointer rests over the frame while the owner types.
      leave(); at(1000); arrive(true); at(1200); typeIn('n'); at(1500); steal()
      post({ type: 'widget-action', action: 'b' }, child)
      expect(earned()).toEqual([false, false])
      // The owner types, then moves onto the frame 200 ms later and presses in it 200 ms after that.
      leave(); at(3000); typeIn('o'); at(3200); arrive(true); at(3400); steal()
      post({ type: 'widget-action', action: 'c' }, child)
      expect(earned()).toEqual([false, false, true])
      // An un-prevented Tab from the field, mid-word, with the pointer elsewhere.
      leave(); at(5000); typeIn('x'); at(5050); typeIn('Tab'); at(5100); steal()
      post({ type: 'widget-action', action: 'd' }, child)
      expect(earned()).toEqual([false, false, true, true])
      // A Tab the page prevented (a mention menu's pick), then the frame takes focus.
      leave(); at(7000)
      field.addEventListener('keydown', (e) => e.preventDefault(), { once: true })
      typeIn('Tab'); at(7050); steal()
      post({ type: 'widget-action', action: 'e' }, child)
      // A Tab that landed on something in the page, then the frame takes focus.
      leave(); at(9000); typeIn('Tab'); act(() => { next.focus() }); at(9050); steal()
      post({ type: 'widget-action', action: 'f' }, child)
      expect(earned()).toEqual([false, false, true, true, false, false])
      // The frame comes to rest under a resting pointer (a load), with no pointer move the page saw.
      leave(); at(11_000); act(() => { iframe.dispatchEvent(new Event('load')) }); at(11_200); arrive(false); at(11_300); steal()
      post({ type: 'widget-action', action: 'g' }, child)
      // The same arrival well after it came to rest (a tap, or the pointer coming in from outside the window).
      leave(); at(12_000); arrive(false); at(12_100); steal()
      post({ type: 'widget-action', action: 'h' }, child)
      expect(earned().slice(6)).toEqual([false, true])
      // A scroll the owner didn't make (the transcript's own follow) moves the frame under the pointer.
      leave(); at(14_000); act(() => { document.dispatchEvent(new Event('scroll')) }); at(14_100); arrive(false); at(14_200); steal()
      post({ type: 'widget-action', action: 'i' }, child)
      // One that follows the owner's wheel is theirs.
      leave(); at(16_000); act(() => { document.dispatchEvent(new WheelEvent('wheel')) }); at(16_010)
      act(() => { document.dispatchEvent(new Event('scroll')) }); at(16_100); arrive(false); at(16_200); steal()
      post({ type: 'widget-action', action: 'j' }, child)
      expect(earned().slice(8)).toEqual([false, true])
      // A composition or a `beforeinput` (an IME, dictation, an Android keyboard) is typing too.
      leave(); at(18_000); arrive(true); at(18_500)
      act(() => { field.dispatchEvent(new Event('beforeinput', { bubbles: true })) }); at(19_000); steal()
      post({ type: 'widget-action', action: 'k' }, child)
      leave(); at(20_000); arrive(true); at(20_500)
      act(() => { field.dispatchEvent(new CompositionEvent('compositionupdate', { bubbles: true })) }); at(21_000); steal()
      post({ type: 'widget-action', action: 'l' }, child)
      expect(earned().slice(10)).toEqual([false, false])
      expect(published).toHaveLength(12)
    } finally {
      now.mockRestore()
      field.remove()
      next.remove()
    }
  })

  it('🔴 hands on the action of a frame pressed straight after another, which the page sees no blur for', async () => {
    // Clicking from one widget into the next moves focus between child frames, and the page's
    // window gets no blur or focus for it.
    const view = render(<><WidgetFrame html={'<button data-action="one">p</button>'} title="A" /><WidgetFrame html={'<button data-action="two">q</button>'} title="B" /></>)
    await act(async () => {})
    const [a, b] = Array.from(view.container.querySelectorAll('iframe'))
    act(() => { pressIntoFrame(a) })
    post({ type: 'widget-action', action: 'one' }, a.contentWindow)
    act(() => { pressFromFrame(a, b) })
    post({ type: 'widget-action', action: 'two' }, b.contentWindow)
    expect(published).toEqual(['[UI] one', '[UI] two'])
    expect(details.map((d) => [d.frame, d.earned === true])).toEqual([['A', true], ['B', true]])
    // The first frame's post no longer counts once focus left it.
    post({ type: 'widget-action', action: 'one' }, a.contentWindow)
    expect(published).toHaveLength(2)
  })

  /** A clock for one case, from the real time now, so every time an earlier case set is before
   *  it. `done` puts the page's last move back at that time, so the next case's real time is after
   *  every time this one set (its cases neither type nor scroll). */
  function clock() {
    const T0 = Date.now()
    const now = vi.spyOn(Date, 'now')
    return {
      at: (ms: number) => now.mockReturnValue(T0 + ms),
      done: () => { now.mockReturnValue(T0); document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true })); now.mockRestore() },
    }
  }
  /** The pointer arrives over *frame*, with the page's own move just before it or none. */
  const arriveOver = (frame: HTMLIFrameElement, moved: boolean) => act(() => {
    if (moved) document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }))
    frame.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
  })
  const leaveFrame = (frame: HTMLIFrameElement) => act(() => {
    frame.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }))
    ;(document.activeElement as HTMLElement | null)?.blur?.()
    window.dispatchEvent(new Event('focus'))
  })
  const takeFocus = (frame: HTMLIFrameElement) => act(() => { frame.focus(); window.dispatchEvent(new Event('blur')) })
  const lastEarned = () => details.at(-1)?.earned === true

  it('reads a frame\'s focus at the post: a pointer that left before it doesn\'t count, one that arrived before it does', async () => {
    const { iframe, child } = await mount('<button data-action="refresh">r</button>')
    // Pressed into, then the pointer leaves before the frame posts.
    leaveFrame(iframe)
    takeFocus(iframe)
    post({ type: 'widget-action', action: 'a' }, child)
    expect(lastEarned()).toBe(false)
    // The frame took focus with the pointer elsewhere, then the pointer arrived before the post.
    arriveOver(iframe, true)
    post({ type: 'widget-action', action: 'b' }, child)
    expect(lastEarned()).toBe(true)
  })

  it('🔴 counts an arrival with no move only once the frame\'s current document has loaded and come to rest', async () => {
    const c = clock()
    try {
      c.at(0)
      const { iframe } = await mount('<button data-action="refresh">l</button>')
      // Its window, read at each post: a new document is a new window.
      const child = () => iframe.contentWindow
      leaveFrame(iframe)
      // Not loaded yet: however long after the mount.
      act(() => { iframe.dispatchEvent(new Event('load')) })
      act(() => { iframe.setAttribute('src', 'blob:a-new-document') })
      c.at(5_000); arriveOver(iframe, false); takeFocus(iframe)
      post({ type: 'widget-action', action: 'a' }, child())
      expect(published, 'premise: it was published').toHaveLength(1)
      expect(lastEarned(), 'its new document has not loaded').toBe(false)
      // Loaded, and a moment at rest: a tap or a pointer coming in from outside the window.
      leaveFrame(iframe)
      c.at(6_000); act(() => { iframe.dispatchEvent(new Event('load')) })
      c.at(6_200); arriveOver(iframe, false); takeFocus(iframe)
      post({ type: 'widget-action', action: 'b' }, child())
      expect(lastEarned(), 'just after it loaded').toBe(false)
      leaveFrame(iframe)
      c.at(7_000); arriveOver(iframe, false); takeFocus(iframe)
      post({ type: 'widget-action', action: 'c' }, child())
      expect(lastEarned()).toBe(true)
      // A height applied moves it again.
      leaveFrame(iframe)
      c.at(9_000); post({ type: 'widget-height', height: 420 }, child())
      c.at(9_100); arriveOver(iframe, false); takeFocus(iframe)
      post({ type: 'widget-action', action: 'd' }, child())
      expect(lastEarned(), 'just after a height').toBe(false)
      // So does anything above it growing.
      leaveFrame(iframe)
      c.at(11_000); act(() => { widgetFramesMayHaveMoved() })
      c.at(11_100); arriveOver(iframe, false); takeFocus(iframe)
      post({ type: 'widget-action', action: 'e' }, child())
      expect(lastEarned(), 'just after the page above it moved').toBe(false)
      // A move the page saw counts at once.
      leaveFrame(iframe)
      c.at(11_200); arriveOver(iframe, true); takeFocus(iframe)
      post({ type: 'widget-action', action: 'f' }, child())
      expect(lastEarned()).toBe(true)
    } finally { c.done() }
  })

  it('🔴 doesn\'t count an arrival with no move over a frame whose document hasn\'t loaded, however long after it appeared', async () => {
    // A widget streaming in appears at its closing tag under a resting pointer, and its own script
    // runs before its document has loaded.
    const c = clock()
    try {
      c.at(0)
      const view = render(<WidgetFrame html={'<button data-action="refresh">s</button>'} title="S" />)
      await act(async () => {})
      const iframe = view.container.querySelector('iframe') as HTMLIFrameElement
      c.at(5_000); arriveOver(iframe, false); takeFocus(iframe)
      post({ type: 'widget-action', action: 'a' }, iframe.contentWindow)
      expect(published, 'premise: it was published').toHaveLength(1)
      expect(lastEarned(), 'not loaded').toBe(false)
      leaveFrame(iframe)
      c.at(6_000); act(() => { iframe.dispatchEvent(new Event('load')) })
      c.at(7_000); arriveOver(iframe, false); takeFocus(iframe)
      post({ type: 'widget-action', action: 'b' }, iframe.contentWindow)
      expect(lastEarned(), 'loaded and at rest').toBe(true)
    } finally { c.done() }
  })

  it('🔴 doesn\'t count an arrival with no move over a frame that another frame\'s height just moved', async () => {
    const c = clock()
    try {
      c.at(0)
      const view = render(<><WidgetFrame html={'<button data-action="one">t</button>'} title="A" /><WidgetFrame html={'<button data-action="two">u</button>'} title="B" /></>)
      await act(async () => {})
      const [a, b] = Array.from(view.container.querySelectorAll('iframe'))
      act(() => { a.dispatchEvent(new Event('load')); b.dispatchEvent(new Event('load')) })
      // A grows, and B below it comes to rest under the pointer.
      c.at(5_000); post({ type: 'widget-height', height: 640 }, a.contentWindow)
      c.at(5_100); arriveOver(b, false); takeFocus(b)
      post({ type: 'widget-action', action: 'two' }, b.contentWindow)
      expect(published, 'premise: it was published').toHaveLength(1)
      expect(lastEarned(), 'just after A\'s height').toBe(false)
      leaveFrame(b)
      c.at(7_000); arriveOver(b, false); takeFocus(b)
      post({ type: 'widget-action', action: 'two' }, b.contentWindow)
      expect(lastEarned(), 'at rest again').toBe(true)
    } finally { c.done() }
  })

  it('starts over when the frame\'s iframe is a new element', async () => {
    function Swapping() {
      const ref = useRef<HTMLIFrameElement>(null)
      const [n, setN] = useState(0)
      useWidgetWire(ref, { forwardActions: true, title: 'S' })
      return <><iframe key={n} ref={ref} title={`s${n}`} /><button onClick={() => setN(n + 1)}>swap</button></>
    }
    const view = render(<Swapping />)
    const first = view.container.querySelector('iframe') as HTMLIFrameElement
    act(() => { pressIntoFrame(first) })
    act(() => { (view.container.querySelector('button') as HTMLButtonElement).click() })
    const second = view.container.querySelector('iframe') as HTMLIFrameElement
    expect(second, 'premise: a new element').not.toBe(first)
    takeFocus(second)
    post({ type: 'widget-action', action: 'next' }, second.contentWindow)
    expect(published.at(-1)).toBe('[UI] next')
    expect(lastEarned(), 'the pointer was over the old element').toBe(false)
  })

  it('🔴 reads a page that doesn\'t hold the window\'s focus, any key the page saw, and a Tab\'s own frame', async () => {
    const view = render(<><WidgetFrame html={'<button data-action="one">p</button>'} title="A" /><WidgetFrame html={'<button data-action="two">q</button>'} title="B" /></>)
    await act(async () => {})
    const [a, b] = Array.from(view.container.querySelectorAll('iframe'))
    // The page's window doesn't hold focus.
    const hasFocus = vi.spyOn(document, 'hasFocus').mockReturnValue(false)
    act(() => { pressIntoFrame(a) })
    post({ type: 'widget-action', action: 'one' }, a.contentWindow)
    hasFocus.mockRestore()
    expect(lastEarned(), 'no window focus').toBe(false)
    // A key the page saw, after the pointer arrived and outside any field.
    leaveFrame(a)
    arriveOver(a, true)
    act(() => { document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true })) })
    takeFocus(a)
    post({ type: 'widget-action', action: 'one' }, a.contentWindow)
    expect(lastEarned(), 'a key after the arrival').toBe(false)
    // A Tab into A grants A, not B taking focus after it with no blur of the page's.
    leaveFrame(a)
    const field = document.createElement('input')
    document.body.appendChild(field)
    act(() => { field.focus(); window.dispatchEvent(new Event('focus')) })
    act(() => { field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })) })
    takeFocus(a)
    post({ type: 'widget-action', action: 'one' }, a.contentWindow)
    expect(lastEarned(), 'the Tab into A').toBe(true)
    act(() => { b.focus() })
    post({ type: 'widget-action', action: 'two' }, b.contentWindow)
    expect(lastEarned(), 'B, after a Tab into A').toBe(false)
    // …and the pointer arriving over B ends A's grant.
    act(() => { a.focus() })
    arriveOver(b, true)
    act(() => { b.dispatchEvent(new MouseEvent('mouseout', { bubbles: true })) })
    act(() => { a.focus() })
    post({ type: 'widget-action', action: 'one' }, a.contentWindow)
    expect(lastEarned(), 'A, after the pointer went to B').toBe(false)
    field.remove()
  })

  it('says nothing of an empty post from a frame the owner turned down or answered a moment ago', async () => {
    const toasts: string[] = []
    const onToast = (e: Event) => { toasts.push(String((e as CustomEvent).detail?.message)) }
    window.addEventListener('ne:toast', onToast)
    const { child } = await mount('<button data-action="refresh">n</button>')
    post({ type: 'widget-action', action: 'refresh' }, child)
    const frameHost = String(details[0]?.host)
    try {
      expect(frameHost, 'premise: its mount id').toMatch(/^frame-\d+$/)
      declineWidgetHost(frameHost)
      post({ type: 'widget-action', action: '\u200b' }, child)
      expect(toasts, 'turned down').toEqual([])
      forgetWidgetHost(frameHost)
      coolWidgetHost(frameHost)
      post({ type: 'widget-action', action: '\u200b' }, child)
      expect(toasts, 'a moment after an answer').toEqual([])
    } finally {
      window.removeEventListener('ne:toast', onToast)
      forgetWidgetHost(frameHost)
    }
  })

  it('🔴 tells the owner when a post of theirs has no message to offer', async () => {
    const toasts: string[] = []
    const onToast = (e: Event) => { toasts.push(String((e as CustomEvent).detail?.message)) }
    window.addEventListener('ne:toast', onToast)
    try {
      const { iframe, child } = await mount('<button data-action="refresh">e</button>')
      post({ type: 'widget-action', action: '\u200b' }, child)
      expect(published).toEqual([])
      expect(toasts).toEqual(['“W” had no message to offer.'])
      // A frame that keeps posting after the one press says it once.
      for (let i = 0; i < 20; i++) post({ type: 'widget-action', action: '\u200b' }, child)
      expect(toasts).toHaveLength(1)
      // The pointer leaves and presses in again: a new arrival, so its post says it again.
      leaveFrame(iframe)
      act(() => { pressIntoFrame(iframe) })
      post({ type: 'widget-action', action: '\u200b' }, child)
      expect(toasts, 'once more after the pointer came back').toHaveLength(2)
    } finally { window.removeEventListener('ne:toast', onToast) }
  })

  it('tells the owner again when they Tab into the frame after a tell', async () => {
    const toasts: string[] = []
    const onToast = (e: Event) => { toasts.push(String((e as CustomEvent).detail?.message)) }
    window.addEventListener('ne:toast', onToast)
    const field = document.createElement('input')
    document.body.appendChild(field)
    try {
      const { iframe, child } = await mount('<button data-action="refresh">tab</button>')
      post({ type: 'widget-action', action: '\u200b' }, child)
      expect(toasts, 'premise: the first tell').toEqual(['“W” had no message to offer.'])
      // The owner Tabs from a field into the frame, with no pointer arrival: a new press to tell them about.
      act(() => { field.focus(); window.dispatchEvent(new Event('focus')) })
      act(() => { field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })) })
      takeFocus(iframe)
      post({ type: 'widget-action', action: '\u200b' }, child)
      expect(toasts).toHaveLength(2)
    } finally {
      field.remove()
      window.removeEventListener('ne:toast', onToast)
    }
  })

  it('hands on the action of a frame an assistive technology activated, as not earned', async () => {
    // A screen reader or voice control focuses the control in the frame with no Tab and no
    // pointer. A chat offers its action as a chip, and any other host asks.
    const { iframe, child } = await mount('<button data-action="refresh">v</button>')
    const field = document.createElement('input')
    document.body.appendChild(field)
    act(() => { iframe.dispatchEvent(new MouseEvent('mouseout', { bubbles: true })); field.focus(); window.dispatchEvent(new Event('focus')) })
    act(() => { activateInFrame(iframe) })
    post({ type: 'widget-action', action: 'refresh' }, child)
    expect(published).toEqual(['[UI] refresh'])
    expect(details[0].earned).toBeUndefined()
    field.remove()
  })

  it('🔴 drops an action that arrives while the page holds no user activation', async () => {
    // A post with no gesture in the last few seconds is dropped.
    const { child } = await mount('<button data-action="refresh">i</button>')
    activation(false)
    post({ type: 'widget-action', action: 'refresh' }, child)
    expect(published).toEqual([])
    activation(true)
    post({ type: 'widget-action', action: 'refresh' }, child)
    expect(published).toEqual(['[UI] refresh'])
  })

  it('drops the colon when the payload is empty or absent', async () => {
    const { child } = await mount('<button data-action="refresh">b</button>')
    post({ type: 'widget-action', action: 'refresh', payload: {} }, child)
    post({ type: 'widget-action', action: 'ping' }, child)
    expect(published).toEqual(['[UI] refresh', '[UI] ping'])
  })

  it('refuses a forged action from a foreign window — no turn', async () => {
    await mount('<button data-action="refresh">c</button>')
    post({ type: 'widget-action', action: 'exfiltrate', payload: { secret: 1 } }, foreignWindow())
    post({ type: 'widget-action', action: 'exfiltrate' }, null)
    post({ type: 'widget-action', action: 'exfiltrate' }, window)
    expect(published).toEqual([])
  })

  it('ignores a message whose type is not in the contract', async () => {
    const { child } = await mount('<button data-action="refresh">d</button>')
    post({ type: 'widget-exec', action: 'rm -rf', payload: {} }, child)
    post({ action: 'refresh', payload: {} }, child)
    post('widget-action', child)
    post(null, child)
    expect(published).toEqual([])
  })

  it('syncs height from THIS frame and ignores a non-numeric height', async () => {
    const { iframe, child } = await mount('<button data-action="refresh">e</button>')
    post({ type: 'widget-height', height: 421, width: 300 }, child)
    expect(iframe.style.height).toBe('421px')
    post({ type: 'widget-height', height: '9999' }, child)
    expect(iframe.style.height).toBe('421px')
  })

  it('applies at most one height a frame reports in 300 ms, and then the last of them', async () => {
    const { iframe, child } = await mount('<button data-action="refresh">l</button>')
    post({ type: 'widget-height', height: 300 }, child)
    for (const h of [301, 302, 303, 500]) post({ type: 'widget-height', height: h }, child)
    expect(iframe.style.height).toBe('300px')
    await act(async () => { await new Promise((r) => setTimeout(r, 320)) })
    expect(iframe.style.height).toBe('500px')
  })

  it('keeps no height held back once a later one applies at once', async () => {
    // A height held back is dropped when a later one applies, so its timer never sets the frame
    // back to the older height.
    const { iframe, child } = await mount('<button data-action="refresh">s</button>')
    const now = vi.spyOn(Date, 'now')
    try {
      now.mockReturnValue(10_000)
      post({ type: 'widget-height', height: 300 }, child)
      now.mockReturnValue(10_100)
      post({ type: 'widget-height', height: 400 }, child)
      now.mockReturnValue(10_300)
      post({ type: 'widget-height', height: 500 }, child)
      expect(iframe.style.height).toBe('500px')
      await act(async () => { await new Promise((r) => setTimeout(r, 260)) })
      expect(iframe.style.height, 'the held-back 400 is not applied after it').toBe('500px')
    } finally { now.mockRestore() }
  })

  it('floors a tiny reported height at the frame minimum', async () => {
    const { iframe, child } = await mount('<button data-action="refresh">f</button>')
    post({ type: 'widget-height', height: 4 }, child)
    expect(iframe.style.height).toBe('80px')
  })

  it('names the source artifact for a SAVED widget so the agent refreshes it in place', async () => {
    vi.mocked(api.artifactExists).mockResolvedValueOnce(true)
    const view = render(<WidgetFrame html="<button data-action='refresh'>g</button>" title="W" slug="sales-view" />)
    await act(async () => {})
    const frame = view.container.querySelector('iframe')
    act(() => { pressIntoFrame(frame) })
    const child = frame?.contentWindow ?? null
    post({ type: 'widget-action', action: 'refresh', payload: { range: '30d' } }, child)
    expect(published).toEqual(['[UI] refresh: {"range":"30d"} (refresh artifact "sales-view" in place)'])
  })
})

describe('widget wire — ReactWidgetFrame (height + error, same provenance rule)', () => {
  async function mount(jsx: string) {
    const view = render(<ReactWidgetFrame jsx={jsx} title="R" />)
    // The frame appears once its document is prepared (compiled component + inlined React).
    const iframe = await waitFor(() => {
      const f = view.container.querySelector('iframe')
      if (!f) throw new Error('ReactWidgetFrame rendered no iframe')
      return f
    })
    return { view, iframe, child: iframe.contentWindow }
  }

  it('clamps a height from THIS frame into [MIN, MAX]', async () => {
    const { iframe, child } = await mount('const App = () => 1')
    post({ type: 'widget-height', height: 10 }, child)
    expect(iframe.style.height).toBe('80px')
    // One height a frame in 300 ms: the next follows once the window passes.
    await act(async () => { await new Promise((r) => setTimeout(r, 320)) })
    post({ type: 'widget-height', height: 99_999 }, child)
    expect(iframe.style.height).toBe('640px')
  })

  it('surfaces widget-error from THIS frame and refuses a foreign one', async () => {
    const { view, child } = await mount('const App = () => 2')
    post({ type: 'widget-error', message: 'boom' }, foreignWindow())
    expect(view.container.textContent).not.toContain('error')
    post({ type: 'widget-error', message: 'boom' }, child)
    expect(view.container.textContent).toContain('error')
  })

})

describe('widget wire — what a frame\'s error can say', () => {
  function Harness({ onError }: { onError: (m: string) => void }) {
    const ref = useRef<HTMLIFrameElement>(null)
    useWidgetWire(ref, { title: 'Sales\u202e', onError })
    return <iframe ref={ref} title="harness" />
  }

  it('hands an error on clipped to 200 characters, named by the frame, and a repeat once in five seconds', () => {
    const onError = vi.fn()
    const view = render(<Harness onError={onError} />)
    const child = view.container.querySelector('iframe')?.contentWindow ?? null
    post({ type: 'widget-error', message: `${'x'.repeat(500)}TAIL` }, child)
    post({ type: 'widget-error', message: `${'x'.repeat(500)}TAIL` }, child)
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledWith(`Sales\\u202e: ${'x'.repeat(200)}`)
    // A new error is not a repeat: it shows at once, so a React widget's next document's error
    // shows too.
    post({ type: 'widget-error', message: 'second' }, child)
    expect(onError).toHaveBeenCalledTimes(2)
    expect(onError).toHaveBeenLastCalledWith('Sales\\u202e: second')
  })

  it('names a frame with no title "Widget", and clips a long title by code point, and leaves its quote marks as they are', () => {
    function Untitled({ onError, title }: { onError: (m: string) => void; title?: string }) {
      const ref = useRef<HTMLIFrameElement>(null)
      useWidgetWire(ref, { title, onError })
      return <iframe ref={ref} title="harness" />
    }
    const onError = vi.fn()
    const view = render(<Untitled onError={onError} />)
    post({ type: 'widget-error', message: 'm' }, view.container.querySelector('iframe')?.contentWindow ?? null)
    expect(onError).toHaveBeenLastCalledWith('Widget: m')
    view.unmount()
    const long = render(<Untitled onError={onError} title={`${'😀'.repeat(100)}”`} />)
    post({ type: 'widget-error', message: 'm' }, long.container.querySelector('iframe')?.contentWindow ?? null)
    expect(onError).toHaveBeenLastCalledWith(`${'😀'.repeat(80)}: m`)
    long.unmount()
    const quoted = render(<Untitled onError={onError} title={'Sales” asks you to'} />)
    post({ type: 'widget-error', message: 'm' }, quoted.container.querySelector('iframe')?.contentWindow ?? null)
    expect(onError).toHaveBeenLastCalledWith('Sales” asks you to: m')
  })
})
