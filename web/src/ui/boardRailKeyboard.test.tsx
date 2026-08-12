import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Columns3 } from 'lucide-react'
import { CollapsedBoardColumn, CollapseColumnButton } from './BoardCollapse'

// ── role="button" is a promise of keyboard operability ─────────────────────────────────
//
// A collapsed board column renders as a slim vertical rail with `role="button"` and an `onClick`.
// Measured on #/tasks?view=board before this change:
//
//   { tag: "DIV", size: "44x739", accName: "0In progress", tabIndex: -1,
//     focusable: false, hasKeyHandler: false, ariaExpanded: null }
//
// A **44×739px** control — one of the largest targets on the board — that a keyboard user could not
// reach (no tab stop, matches no natively-tabbable selector) and could not activate (no key
// handler). So a column collapsed by the empty-column default, or by an earlier click, was
// **unrecoverable without a mouse**. WCAG 2.1.1 Keyboard, level A — stronger than the AA work
// around it.
//
// 🪤 NO axe RULE COVERS "role=button without a tab stop". This board's sibling views passed five
// clean audits; the mechanical pass cannot see this class of defect at all, so it is worth probing
// `tabIndex`/`focusable`/`hasKeyHandler` by hand on anything carrying a non-native role.
//
// Two details that are easy to get wrong:
//  · **Space must `preventDefault`**, or the page scrolls underneath the activation.
//  · **The name must carry the count itself.** An `aria-label` OVERRIDES the element's text, so the
//    visible "0" would be dropped from the accessible name — the same trap the nav count badge hit.

const rail = (onExpand?: () => void) =>
  render(<CollapsedBoardColumn icon={Columns3} label="In progress" count={0} onExpand={onExpand} />)

describe('a collapsed board rail is operable by keyboard', () => {
  it('takes a tab stop', () => {
    rail(vi.fn())
    const el = screen.getByRole('button')
    expect(el.tabIndex, 'role=button with no tab stop is unreachable').toBe(0)
    el.focus()
    expect(document.activeElement).toBe(el)
  })

  it('expands on Enter', () => {
    const onExpand = vi.fn()
    rail(onExpand)
    fireEvent.keyDown(screen.getByRole('button'), { key: 'Enter' })
    expect(onExpand).toHaveBeenCalledTimes(1)
  })

  it('expands on Space, and swallows the scroll', () => {
    const onExpand = vi.fn()
    rail(onExpand)
    const ev = createEvent()
    fireEvent(screen.getByRole('button'), ev)
    expect(onExpand).toHaveBeenCalledTimes(1)
    expect(ev.defaultPrevented, 'Space must not also scroll the page').toBe(true)
  })

  it('ignores other keys', () => {
    const onExpand = vi.fn()
    rail(onExpand)
    fireEvent.keyDown(screen.getByRole('button'), { key: 'a' })
    fireEvent.keyDown(screen.getByRole('button'), { key: 'Escape' })
    expect(onExpand).not.toHaveBeenCalled()
  })

  it('keeps the COUNT in its accessible name, and says what activating it does', () => {
    rail(vi.fn())
    expect(screen.getByRole('button', { name: 'In progress, 0 — expand column' })).toBeTruthy()
  })

  it('announces itself as the collapsed half of a disclosure', () => {
    rail(vi.fn())
    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('false')
  })

  it('stays a plain non-interactive rail when it cannot be expanded', () => {
    // The chat tag board renders read-only rails; giving those a tab stop would add dead stops.
    const { container } = rail(undefined)
    expect(screen.queryByRole('button')).toBeNull()
    const div = container.firstElementChild as HTMLElement
    expect(div.getAttribute('tabindex')).toBeNull()
    expect(div.getAttribute('aria-expanded')).toBeNull()
  })
})

describe('the two halves of the disclosure agree', () => {
  it('the collapse control announces the expanded state', () => {
    render(<CollapseColumnButton onCollapse={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Collapse column' }).getAttribute('aria-expanded')).toBe('true')
  })
})

/** A keydown event whose `defaultPrevented` can be inspected after dispatch. */
function createEvent(): KeyboardEvent {
  return new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true })
}
