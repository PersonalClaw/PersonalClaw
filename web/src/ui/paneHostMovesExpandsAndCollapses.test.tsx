import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { PaneHost, type PaneContent } from './PaneHost'

// ── The host moves, expands and collapses a pane, and none of it touches the work (#3821) ────────
//
// The Oct 18 amendment: the pane host docks a pane beside the work, expands it over the work
// surface and collapses it back to the split, moves it to the other dock (by pointer, or by a
// keyboard move mode that announces its preview, its commit and its cancel), and closes it, and
// every one of those is presentation only. The dock it was moved to is remembered for the surface,
// as its width is. Esc collapses an expanded pane, and closes one beside the work. A screen reader
// hears the pane's title, where it is docked, the move's preview and what became of it.

beforeEach(() => localStorage.clear())
afterEach(cleanup)

const content = (onClose: () => void): PaneContent => ({
  id: 'a.py', title: 'Diff — a.py', label: 'diff', onClose, body: <p>the diff of a.py</p>,
})

/** A surface whose work holds a field (to show it is never remounted) and a running count (to show
 *  no host control changes the work), with the pane open. */
function Surface({ surface = 'code' }: { surface?: string }) {
  const [open, setOpen] = useState(true)
  const [ticks, setTicks] = useState(0)
  return (
    <PaneHost surface={surface} pane={open ? content(() => setOpen(false)) : null}>
      <input aria-label="Draft" defaultValue="" />
      <button type="button" onClick={() => setTicks((t) => t + 1)}>Work {ticks}</button>
      <button type="button" onClick={() => setOpen(true)}>Open a.py</button>
    </PaneHost>
  )
}

const pane = () => screen.getByRole('region', { name: 'Diff — a.py' })
const host = () => document.querySelector('[data-pane-host]') as HTMLElement
const draft = () => screen.getByRole('textbox', { name: 'Draft', hidden: true }) as HTMLInputElement
const status = () => screen.getByRole('status').textContent ?? ''
const follows = (a: Node, b: Node) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
const onLeft = () => follows(pane(), draft())
const btn = (name: string | RegExp) => screen.getByRole('button', { name })
const key = (el: HTMLElement, k: string) => fireEvent.keyDown(el, { key: k })

describe('expand and collapse', () => {
  it('expands the pane over the work and collapses it back, the work still mounted', () => {
    render(<Surface />)
    draft().value = 'half a thought'
    fireEvent.click(btn('Expand diff'))
    expect(host().dataset.paneLayout).toBe('expanded')
    expect(draft().closest('[hidden]'), 'the work is hidden, not unmounted').not.toBeNull()
    expect(pane()).toHaveAccessibleDescription(/over the work/)
    fireEvent.click(btn('Collapse diff'))
    expect(host().dataset.paneLayout).toBe('beside')
    expect(draft().closest('[hidden]')).toBeNull()
    expect(draft().value, 'the work kept its state').toBe('half a thought')
    expect(pane()).toHaveAccessibleDescription(/right of the work/)
  })

  it('Esc collapses an expanded pane, then closes it, and never from a field', () => {
    render(<Surface />)
    fireEvent.click(btn('Expand diff'))
    key(pane(), 'Escape')
    expect(host().dataset.paneLayout).toBe('beside')
    key(pane(), 'Escape')
    expect(screen.queryByRole('region', { name: 'Diff — a.py' })).toBeNull()
    fireEvent.click(btn('Open a.py'))
    key(draft(), 'Escape')
    expect(pane(), 'an Esc in the work is the work\'s').toBeInTheDocument()
  })
})

describe('move', () => {
  it('a pointer moves the pane to the other dock, and the surface remembers it', () => {
    render(<Surface />)
    expect(onLeft()).toBe(false)
    fireEvent.click(btn('Move diff'))
    expect(onLeft(), 'the preview is drawn').toBe(true)
    expect(status()).toMatch(/preview.*left/i)
    fireEvent.click(btn('Place diff here'))
    expect(onLeft()).toBe(true)
    expect(status()).toMatch(/moved.*left/i)
    expect(pane()).toHaveAccessibleDescription(/left of the work/)
    cleanup()
    render(<Surface />)
    expect(onLeft(), 'a reload opens it where it was moved').toBe(true)
    cleanup()
    render(<Surface surface="chat" />)
    expect(onLeft(), 'another surface keeps its own dock').toBe(false)
  })

  it('by keyboard: arrows choose the side, Enter places it, Esc cancels, each announced', () => {
    render(<Surface />)
    fireEvent.click(btn('Move diff'))
    // The focus stays on Place as the preview is drawn on each side.
    const placing = () => document.activeElement as HTMLElement
    expect(placing(), 'move mode takes the focus to its controls').toBe(btn('Place diff here'))
    key(placing(), 'ArrowRight')
    expect(onLeft()).toBe(false)
    expect(status()).toMatch(/preview.*right/i)
    expect(placing()).toBe(btn('Place diff here'))
    key(placing(), 'ArrowLeft')
    expect(onLeft()).toBe(true)
    key(placing(), 'Escape')
    expect(onLeft(), 'a cancelled move leaves it where it was').toBe(false)
    expect(status()).toMatch(/cancel.*right/i)
    expect(screen.queryByRole('button', { name: 'Place diff here' })).toBeNull()
    expect(pane(), 'Esc in move mode cancels the move, not the pane').toBeInTheDocument()
    fireEvent.click(btn('Move diff'))
    key(btn('Place diff here'), 'Enter')
    expect(onLeft()).toBe(true)
    expect(localStorage.getItem('pane-host-code-dock')).toBe('left')
  })
})

describe('no host control touches the work', () => {
  it('expand, collapse, move, cancel and close leave the work as it was', () => {
    render(<Surface />)
    fireEvent.click(btn('Work 0'))
    draft().value = 'kept'
    for (const name of ['Expand diff', 'Collapse diff', 'Move diff', 'Cancel the move', 'Move diff', 'Place diff here']) fireEvent.click(btn(name))
    fireEvent.click(btn('Close diff'))
    expect(btn('Work 1'), 'the work did not run or reset').toBeInTheDocument()
    expect(draft().value).toBe('kept')
  })
})
