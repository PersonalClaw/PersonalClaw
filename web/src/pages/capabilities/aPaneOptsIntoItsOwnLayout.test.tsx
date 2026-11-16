// The list beside a detail takes Needs you's layout only where a page asks for it, each part a
// named opt-in a Capabilities tab doesn't pass: the list's px widths beside a pane, the heading in
// the pane's scroller, 44 px Back and Close, focus on every open, and a fade at each cut edge.
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LIST_WIDTH, ListBesideDetail, besideWidth, type CapabilityDetail, type PaneOptions } from './ListBesideDetail'
import { TOUCH_TARGET } from '../../ui/Toggle'

let room = 1084

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  const box = HTMLElement.prototype.getBoundingClientRect
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (!this.hasAttribute('data-capability-layout')) return box.call(this)
    return { width: room, height: 800, top: 0, left: 0, right: room, bottom: 800, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
  })
})

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); room = 1084 })

const NEEDS_YOU: PaneOptions = { listWidth: 720, splitWidth: 440, headingScrolls: true, compactTargets: true, focusOnOpen: true, edgeFade: true }

function detail(over: Partial<CapabilityDetail> = {}): CapabilityDetail {
  return { id: 'a', title: 'Deletes files in ~/code/site', onClose: () => {}, body: <p>the ask</p>, ...over }
}

function open(options: PaneOptions, over: Partial<CapabilityDetail> = {}) {
  return render(
    <ListBesideDetail header={<h1>Needs you</h1>} noun="Needs you" detail={detail(over)} {...options}>
      <button type="button">a card</button>
    </ListBesideDetail>,
  )
}

function list(container: HTMLElement): HTMLElement {
  return container.querySelector('[data-capability-list]') as HTMLElement
}

describe('a pane opts into its own layout', () => {
  it('keeps the list at its width beside the pane while the pane still gets its floor, else snaps to the floor', () => {
    expect(besideWidth(1084, { listWidth: 720, splitWidth: 440 })).toBe('440px')
    expect(besideWidth(1160, { listWidth: 720, splitWidth: 440 })).toBe('720px')
    expect(besideWidth(1600, { listWidth: 720, splitWidth: 440 })).toBe('720px')
    expect(besideWidth(1600, {})).toBe(LIST_WIDTH)
  })

  it('draws the list at the width its room gives', () => {
    room = 1600
    const wide = open(NEEDS_YOU)
    expect(list(wide.container).style.width).toBe('720px')
    wide.unmount()
    room = 1084
    const narrower = open(NEEDS_YOU)
    expect(list(narrower.container).style.width).toBe('440px')
  })

  it('puts the heading first in the pane\'s scroller, wrapping in full, with the eyebrow in the header', () => {
    const { container } = open(NEEDS_YOU, { eyebrow: <span data-testid="tags">Permission needed</span> })
    const heading = screen.getByRole('heading', { level: 2, name: 'Deletes files in ~/code/site' })
    expect(heading.hasAttribute('title')).toBe(false)
    expect(heading.className.split(/\s+/)).not.toContain('truncate')
    const scroller = heading.closest('.overflow-y-auto') as HTMLElement
    expect(scroller).not.toBeNull()
    expect(scroller.firstElementChild?.firstElementChild).toBe(heading)
    const header = container.querySelector('section > div') as HTMLElement
    expect(header.contains(screen.getByTestId('tags'))).toBe(true)
    expect(header.contains(heading)).toBe(false)
  })

  it('gives Close a 44 px box below 1024 px and a themed tip, never a native title', () => {
    open(NEEDS_YOU)
    const close = screen.getByRole('button', { name: 'Close' })
    for (const cls of TOUCH_TARGET.split(' ')) expect(close.className).toContain(cls)
    expect(close.hasAttribute('title')).toBe(false)
  })

  it('takes the focus to the heading on an open at every width, but not when the detail follows a moved selection', () => {
    const first = open(NEEDS_YOU)
    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 2 }))
    first.unmount()
    open(NEEDS_YOU, { followsSelection: true })
    expect(document.activeElement).not.toBe(screen.getByRole('heading', { level: 2 }))
  })

  it('wraps both scrollers in the cut-edge fade', () => {
    const { container } = open(NEEDS_YOU)
    const panes = container.querySelectorAll<HTMLElement>('.overflow-y-auto')
    expect(panes).toHaveLength(2)
    for (const pane of Array.from(panes)) expect(pane.style.scrollPaddingTop).toBe('24px')
  })

  it('changes nothing for a page that passes none of them', () => {
    const { container } = open({})
    expect(list(container).style.width).toBe(LIST_WIDTH)
    const heading = screen.getByRole('heading', { level: 2 })
    expect(heading.getAttribute('title')).toBe('Deletes files in ~/code/site')
    expect(heading.className.split(/\s+/)).toContain('truncate')
    expect(document.activeElement).not.toBe(heading)
    for (const pane of Array.from(container.querySelectorAll<HTMLElement>('.overflow-y-auto'))) {
      expect(pane.style.scrollPaddingTop).toBe('')
    }
    expect(screen.getByRole('button', { name: 'Close' }).className).not.toContain(TOUCH_TARGET.split(' ')[0])
  })
})

describe('a pane with a decide bar', () => {
  it('its scroller is a size container, so the bar sticks only while the pane is tall enough', () => {
    const { container } = open({ ...NEEDS_YOU, decideBar: true })
    const scroller = container.querySelector('section[role="region"] .overflow-y-auto') as HTMLElement
    expect(scroller.style.containerType).toBe('size')
  })

  it('a pane without one keeps its scroller as it was', () => {
    const { container } = open(NEEDS_YOU)
    const scroller = container.querySelector('section[role="region"] .overflow-y-auto') as HTMLElement
    expect(scroller.style.containerType).toBe('')
  })
})

describe('Escape in the pane', () => {
  it('in a field only leaves the field; once out of it, Escape closes the pane', () => {
    const onClose = vi.fn()
    open(NEEDS_YOU, { onClose, body: <input aria-label="Reply" /> })
    const field = screen.getByRole('textbox', { name: 'Reply' })
    field.focus()
    field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(onClose).not.toHaveBeenCalled()
    expect(document.activeElement).not.toBe(field)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('one that ends a composition does nothing', () => {
    const onClose = vi.fn()
    open(NEEDS_YOU, { onClose })
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, isComposing: true }))
    expect(onClose).not.toHaveBeenCalled()
  })
})
