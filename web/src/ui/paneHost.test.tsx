import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { PaneHost, type PaneContent } from './PaneHost'

// ── One host for the detail a surface opens beside its work ───────────────────────────────────
//
// A review, a workflow's detail, an output or a background task opens in the same pane host: it
// docks the pane beside the surface's work, resizes it by pointer and by key, remembers the size
// for that surface, and gives focus back to the control the pane was opened from when it closes.
// What the pane shows, and whether it is open, are the feature's: a feature that keeps its open
// content in its address hands the host what the address names. Every host control is
// presentation only, so the work beside the pane is never remounted by it.

beforeEach(() => localStorage.clear())
afterEach(cleanup)

const content = (id: string, onClose: () => void = () => {}): PaneContent => ({
  id, title: `Diff — ${id}`, label: 'diff', onClose, body: <p>the diff of {id}</p>,
})

/** A surface with two sources that open the pane, a field in its work, and the host beside it. */
function Surface({ surface = 'code', dock, initial = null }: { surface?: string; dock?: 'left' | 'right'; initial?: string | null }) {
  const [open, setOpen] = useState<string | null>(initial)
  return (
    <PaneHost surface={surface} dock={dock} pane={open ? content(open, () => setOpen(null)) : null}>
      <button type="button" onClick={() => setOpen('a.py')}>Open a.py</button>
      <button type="button" onClick={() => setOpen('b.py')}>Open b.py</button>
      <button type="button" onClick={() => setOpen(null)}>Back</button>
      <input aria-label="Draft" defaultValue="" />
    </PaneHost>
  )
}

const pane = (name = 'Diff — a.py') => screen.getByRole('region', { name })
const handle = () => screen.getByRole('separator')
const key = (el: HTMLElement, k: string) => fireEvent.keyDown(el, { key: k })
const follows = (a: Node, b: Node) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

describe('the pane host docks a pane beside the work', () => {
  it('shows no pane until the feature opens one, and then a region named by its title', () => {
    render(<Surface />)
    expect(screen.queryByRole('region')).toBeNull()
    expect(screen.queryByRole('separator')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open a.py' }))
    expect(pane()).toHaveTextContent('the diff of a.py')
    expect(screen.getByRole('heading', { name: 'Diff — a.py' })).toBeInTheDocument()
  })

  it('docks on the right of the work by default, and on the left when asked', () => {
    render(<Surface initial="a.py" />)
    expect(follows(screen.getByRole('textbox', { name: 'Draft' }), pane())).toBe(true)
    cleanup()
    render(<Surface initial="a.py" dock="left" />)
    expect(follows(pane(), screen.getByRole('textbox', { name: 'Draft' }))).toBe(true)
  })

  it('keeps the work beside it mounted while it opens, changes and closes', () => {
    render(<Surface />)
    const draft = screen.getByRole('textbox', { name: 'Draft' }) as HTMLInputElement
    fireEvent.change(draft, { target: { value: 'half a thought' } })
    fireEvent.click(screen.getByRole('button', { name: 'Open a.py' }))
    fireEvent.click(screen.getByRole('button', { name: 'Open b.py' }))
    fireEvent.click(screen.getByRole('button', { name: 'Close diff' }))
    expect(screen.getByRole('textbox', { name: 'Draft' })).toBe(draft)
    expect(draft.value).toBe('half a thought')
  })

  it("closes through the feature's own close, named for what it shows", () => {
    render(<Surface initial="a.py" />)
    fireEvent.click(screen.getByRole('button', { name: 'Close diff' }))
    expect(screen.queryByRole('region')).toBeNull()
  })
})

describe('the pane host resizes the pane', () => {
  it('is a splitter on the edge that faces the work, reporting its width', () => {
    render(<Surface initial="a.py" />)
    const h = handle()
    expect(h).toHaveAttribute('aria-orientation', 'vertical')
    expect(h).toHaveAttribute('tabindex', '0')
    expect(h).toHaveAccessibleName('Resize diff — arrow keys to resize')
    expect(h).toHaveAttribute('aria-valuenow', '480')
    expect(pane().style.width).toBe('480px')
  })

  it('grows a right pane with ArrowLeft and a left pane with ArrowRight, within its bounds', () => {
    render(<Surface initial="a.py" />)
    key(handle(), 'ArrowLeft')
    expect(handle()).toHaveAttribute('aria-valuenow', '496')
    expect(pane().style.width).toBe('496px')
    key(handle(), 'Home')
    expect(handle()).toHaveAttribute('aria-valuenow', '320')
    key(handle(), 'ArrowRight')
    expect(handle()).toHaveAttribute('aria-valuenow', '320')
    key(handle(), 'End')
    expect(handle()).toHaveAttribute('aria-valuenow', '960')
    cleanup()
    localStorage.clear()
    render(<Surface initial="a.py" dock="left" />)
    key(handle(), 'ArrowRight')
    expect(handle()).toHaveAttribute('aria-valuenow', '496')
  })

  it('resizes by dragging the handle', () => {
    render(<Surface initial="a.py" />)
    const h = handle()
    fireEvent.pointerDown(h, { clientX: 600, pointerId: 1 })
    fireEvent.pointerMove(h, { clientX: 500, pointerId: 1 })
    fireEvent.pointerUp(h, { clientX: 500, pointerId: 1 })
    expect(h).toHaveAttribute('aria-valuenow', '580')
  })
})

describe('the pane host remembers the size for each surface', () => {
  it('opens again at the size it was left at, after a close and after a reload', () => {
    const first = render(<Surface initial="a.py" />)
    key(handle(), 'ArrowLeft'); key(handle(), 'ArrowLeft')
    fireEvent.click(screen.getByRole('button', { name: 'Close diff' }))
    fireEvent.click(screen.getByRole('button', { name: 'Open b.py' }))
    expect(handle()).toHaveAttribute('aria-valuenow', '512')
    first.unmount()
    render(<Surface initial="a.py" />)
    expect(handle()).toHaveAttribute('aria-valuenow', '512')
    expect(pane().style.width).toBe('512px')
  })

  it('keeps one size per surface', () => {
    const code = render(<Surface surface="code" initial="a.py" />)
    key(handle(), 'Home')
    code.unmount()
    render(<Surface surface="workflow" initial="a.py" />)
    expect(handle()).toHaveAttribute('aria-valuenow', '480')
    cleanup()
    render(<Surface surface="code" initial="a.py" />)
    expect(handle()).toHaveAttribute('aria-valuenow', '320')
  })

  it('writes nothing but its own size', () => {
    const { unmount } = render(<Surface initial="a.py" />)
    key(handle(), 'ArrowLeft')
    unmount()
    expect(Object.keys(localStorage).sort()).toEqual(['pane-host-code-w'])
  })
})

describe('the pane host returns focus to where the pane was opened from', () => {
  const opener = (name: string) => screen.getByRole('button', { name })

  it('gives focus back to the control that opened the pane when its close is used', () => {
    render(<Surface />)
    opener('Open a.py').focus()
    fireEvent.click(opener('Open a.py'))
    const close = screen.getByRole('button', { name: 'Close diff' })
    close.focus()
    fireEvent.click(close)
    expect(screen.queryByRole('region')).toBeNull()
    expect(document.activeElement).toBe(opener('Open a.py'))
  })

  it('gives focus back to the control that opened what the pane shows last', () => {
    render(<Surface />)
    opener('Open a.py').focus()
    fireEvent.click(opener('Open a.py'))
    opener('Open b.py').focus()
    fireEvent.click(opener('Open b.py'))
    expect(pane('Diff — b.py')).toBeInTheDocument()
    screen.getByRole('button', { name: 'Close diff' }).focus()
    fireEvent.click(screen.getByRole('button', { name: 'Close diff' }))
    expect(document.activeElement).toBe(opener('Open b.py'))
  })

  it('keeps the opener when focus moves into the pane and the content changes there', () => {
    function Linked() {
      const [open, setOpen] = useState<string | null>(null)
      const c = open ? content(open, () => setOpen(null)) : null
      return (
        <PaneHost surface="code" pane={c && { ...c, body: <button type="button" onClick={() => setOpen('b.py')}>Next file</button> }}>
          <button type="button" onClick={() => setOpen('a.py')}>Open a.py</button>
        </PaneHost>
      )
    }
    render(<Linked />)
    opener('Open a.py').focus()
    fireEvent.click(opener('Open a.py'))
    screen.getByRole('button', { name: 'Next file' }).focus()
    fireEvent.click(screen.getByRole('button', { name: 'Next file' }))
    expect(pane('Diff — b.py')).toBeInTheDocument()
    screen.getByRole('button', { name: 'Close diff' }).focus()
    fireEvent.click(screen.getByRole('button', { name: 'Close diff' }))
    expect(document.activeElement).toBe(opener('Open a.py'))
  })

  it('leaves focus where it is when the pane closes while focus is in the work', () => {
    render(<Surface />)
    opener('Open a.py').focus()
    fireEvent.click(opener('Open a.py'))
    const draft = screen.getByRole('textbox', { name: 'Draft' })
    draft.focus()
    fireEvent.click(opener('Back'))
    expect(screen.queryByRole('region')).toBeNull()
    expect(document.activeElement).toBe(draft)
  })

  it('moves no focus for a pane opened with nothing focused, as an address opens it', () => {
    render(<Surface initial="a.py" />)
    expect(document.activeElement).toBe(document.body)
    act(() => { screen.getByRole('button', { name: 'Close diff' }).focus() })
    fireEvent.click(screen.getByRole('button', { name: 'Close diff' }))
    expect(document.activeElement).toBe(document.body)
  })

  it('moves no focus when the pane opens', () => {
    render(<Surface />)
    opener('Open a.py').focus()
    fireEvent.click(opener('Open a.py'))
    expect(document.activeElement).toBe(opener('Open a.py'))
  })
})

// ── A compact surface stacks the pane under the work ─────────────────────────────────────────
//
// "Compact layouts stack the work and artifact without clipping either one" (#3820's approved
// update). Beside the work, a pane takes at least its smallest width and the work keeps its own
// 12rem, so a surface narrower than the two together cannot hold them side by side without one
// giving way. There the host stacks the pane under the work, each at the surface's full width,
// and offers no width handle, since there is no width to choose. The room read is the host's own
// box, not the window's: a docked sidebar beside it takes its share first.

describe('the pane host stacks the pane under the work on a compact surface', () => {
  /** The ResizeObservers the host holds, so a test can say the surface changed size. */
  const watchers = new Set<() => void>()
  let boxWidth = 0
  const resizeTo = (width: number) => act(() => { boxWidth = width; for (const fire of watchers) fire() })
  const layout = () => document.querySelector<HTMLElement>('[data-pane-layout]')

  beforeEach(() => {
    watchers.clear()
    vi.stubGlobal('ResizeObserver', class {
      fire: () => void
      constructor(callback: ResizeObserverCallback) { this.fire = () => callback([], this as unknown as ResizeObserver) }
      observe() { watchers.add(this.fire) }
      unobserve() { watchers.delete(this.fire) }
      disconnect() { watchers.delete(this.fire) }
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() =>
      ({ width: boxWidth, height: 800, top: 0, left: 0, right: boxWidth, bottom: 800, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect)
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  for (const width of [320, 390, 412]) {
    it(`at ${width} px it stacks the pane under the work, at the surface's width, with no handle`, () => {
      boxWidth = width
      render(<Surface initial="a.py" />)
      expect(layout()).toHaveAttribute('data-pane-layout', 'stacked')
      expect(follows(screen.getByRole('textbox', { name: 'Draft' }), pane()), 'the pane is under the work').toBe(true)
      expect(pane().style.width, 'the pane takes the surface width, not a chosen one').toBe('')
      expect(screen.queryByRole('separator'), 'there is no width to choose').toBeNull()
      expect(pane()).toBeVisible()
      expect(screen.getByRole('textbox', { name: 'Draft' })).toBeVisible()
    })
  }

  it('stacks under the work whichever side the pane docks on', () => {
    boxWidth = 390
    render(<Surface initial="a.py" dock="left" />)
    expect(follows(screen.getByRole('textbox', { name: 'Draft' }), pane())).toBe(true)
  })

  it('sits beside the work again once the surface has the room, at the width it was left at', () => {
    boxWidth = 1000
    render(<Surface initial="a.py" />)
    expect(layout()).toHaveAttribute('data-pane-layout', 'beside')
    key(handle(), 'ArrowLeft')
    const draft = screen.getByRole('textbox', { name: 'Draft' }) as HTMLInputElement
    fireEvent.change(draft, { target: { value: 'half a thought' } })
    resizeTo(390)
    expect(layout()).toHaveAttribute('data-pane-layout', 'stacked')
    expect(screen.queryByRole('separator')).toBeNull()
    resizeTo(1000)
    expect(layout()).toHaveAttribute('data-pane-layout', 'beside')
    expect(handle()).toHaveAttribute('aria-valuenow', '496')
    expect(pane().style.width).toBe('496px')
    expect(screen.getByRole('textbox', { name: 'Draft' }), 'the work stayed mounted through both changes').toBe(draft)
    expect(draft.value).toBe('half a thought')
  })

  it('reads the room it has as its least pane width beside the work\'s 12rem', () => {
    // The default pane is at least 320 px and the work keeps 192 px: 512 is the least room.
    boxWidth = 511
    const narrow = render(<Surface initial="a.py" />)
    expect(layout()).toHaveAttribute('data-pane-layout', 'stacked')
    narrow.unmount()
    boxWidth = 512
    render(<Surface initial="a.py" />)
    expect(layout()).toHaveAttribute('data-pane-layout', 'beside')
  })

  it('keeps the last reading while its box has no width yet, which starts beside', () => {
    boxWidth = 0
    render(<Surface initial="a.py" />)
    expect(layout()).toHaveAttribute('data-pane-layout', 'beside')
    expect(handle()).toBeInTheDocument()
  })
})

describe('the pane host keeps a new width the moment it is chosen', () => {
  it('saves a drag when it ends, so a reload right after opens at that width', () => {
    render(<Surface initial="a.py" />)
    const h = handle()
    fireEvent.pointerDown(h, { clientX: 600, pointerId: 1 })
    fireEvent.pointerMove(h, { clientX: 500, pointerId: 1 })
    fireEvent.pointerUp(h, { clientX: 500, pointerId: 1 })
    expect(localStorage.getItem('pane-host-code-w'), 'no debounce stands between the drag and a reload').toBe('580')
  })

  it('saves a key resize as the page goes away, so a reload right after keeps it', () => {
    render(<Surface initial="a.py" />)
    key(handle(), 'ArrowLeft')
    window.dispatchEvent(new Event('pagehide'))
    expect(localStorage.getItem('pane-host-code-w')).toBe('496')
  })
})

describe('a stacked pane can go into a slot the work gives it', () => {
  const watchers = new Set<() => void>()
  let boxWidth = 0
  const resizeTo = (width: number) => act(() => { boxWidth = width; for (const fire of watchers) fire() })

  beforeEach(() => {
    watchers.clear()
    vi.stubGlobal('ResizeObserver', class {
      fire: () => void
      constructor(callback: ResizeObserverCallback) { this.fire = () => callback([], this as unknown as ResizeObserver) }
      observe() { watchers.add(this.fire) }
      unobserve() { watchers.delete(this.fire) }
      disconnect() { watchers.delete(this.fire) }
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() =>
      ({ width: boxWidth, height: 800, top: 0, left: 0, right: boxWidth, bottom: 800, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect)
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  /** A work with content, a slot for a stacked pane, and a foot it keeps last (a composer). */
  function SlottedSurface() {
    const [slot, setSlot] = useState<HTMLDivElement | null>(null)
    return (
      <PaneHost surface="code" pane={content('a.py')} stackInto={slot}>
        <p>the thread</p>
        <div ref={setSlot} className="contents" data-testid="slot" />
        <input aria-label="Draft" defaultValue="" />
      </PaneHost>
    )
  }

  it('stacked, the pane is in the slot: under the content, above the foot', () => {
    boxWidth = 390
    render(<SlottedSurface />)
    expect(screen.getByTestId('slot').contains(pane()), 'the pane is in the slot').toBe(true)
    expect(follows(screen.getByText('the thread'), pane())).toBe(true)
    expect(follows(pane(), screen.getByRole('textbox', { name: 'Draft' })), 'the foot stays last').toBe(true)
  })

  it('beside, the pane is beside the work, and the work never remounts between the two', () => {
    boxWidth = 1000
    render(<SlottedSurface />)
    expect(screen.getByTestId('slot').contains(pane())).toBe(false)
    expect(follows(screen.getByRole('textbox', { name: 'Draft' }), pane()), 'beside: after the whole work').toBe(true)
    const draft = screen.getByRole('textbox', { name: 'Draft' }) as HTMLInputElement
    fireEvent.change(draft, { target: { value: 'half a thought' } })
    resizeTo(390)
    expect(screen.getByTestId('slot').contains(pane())).toBe(true)
    resizeTo(1000)
    expect(screen.getByTestId('slot').contains(pane())).toBe(false)
    expect(screen.getByRole('textbox', { name: 'Draft' })).toBe(draft)
    expect(draft.value).toBe('half a thought')
  })
})
