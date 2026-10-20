import { afterEach, beforeEach, describe, expect, it } from 'vitest'
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
