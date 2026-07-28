import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { Popover } from './Popover'

// ── Popover placement contract ────────────────────────────────────────────────
// `placement` is a PREFERENCE, not a promise. The same composer renders docked low
// in a chat (menus open upward) and high on the dashboard launcher (upward would
// clip off the top of the page), so a fixed direction is wrong for one of them no
// matter which is chosen. These tests lock the flip: a preferred side without room
// yields to the roomier one, and a side WITH room is left alone — the second half
// matters just as much, since a menu that flips when it didn't need to would move
// every chat composer menu to the wrong side.
//
// The trigger's position is the only input, so each test stubs
// getBoundingClientRect on the element and asserts which anchoring class the
// flyout picked (`bottom-full` = above, `top-full` = below).

const VIEWPORT_H = 800

/** Place the next-rendered trigger at `top`, `height` tall, in an 800px viewport. */
function anchorAt(top: number, height = 32) {
  Object.defineProperty(window, 'innerHeight', { value: VIEWPORT_H, configurable: true })
  Object.defineProperty(window, 'innerWidth', { value: 1280, configurable: true })
  Element.prototype.getBoundingClientRect = function () {
    return {
      top, bottom: top + height, left: 100, right: 300,
      width: 200, height, x: 100, y: top, toJSON: () => ({}),
    } as DOMRect
  }
}

function openMenu(placement?: 'top' | 'bottom') {
  render(
    <Popover
      placement={placement}
      trigger={(_open, toggle) => <button onClick={toggle}>Open</button>}
    >
      {() => <div data-testid="menu-body">rows</div>}
    </Popover>,
  )
  act(() => { screen.getByText('Open').click() })
  // The flyout is the menu body's positioned ancestor.
  return screen.getByTestId('menu-body').parentElement as HTMLElement
}

const realRect = Element.prototype.getBoundingClientRect

describe('Popover placement flips to the side with room', () => {
  beforeEach(() => { Object.defineProperty(window, 'innerHeight', { value: VIEWPORT_H, configurable: true }) })
  afterEach(() => { Element.prototype.getBoundingClientRect = realRect })

  it('flips a top-preferring menu DOWN when the trigger is near the top of the page', () => {
    // The dashboard case: composer high on the page, ~40px above it.
    anchorAt(40)
    const flyout = openMenu('top')
    expect(flyout.className).toContain('top-full')
    expect(flyout.className).not.toContain('bottom-full')
  })

  it('keeps a top-preferring menu UP when there is room above', () => {
    // The chat case: composer docked low. Must NOT flip.
    anchorAt(700)
    const flyout = openMenu('top')
    expect(flyout.className).toContain('bottom-full')
    expect(flyout.className).not.toContain('top-full')
  })

  it('flips a bottom-preferring menu UP when the trigger is near the bottom', () => {
    anchorAt(VIEWPORT_H - 60)
    const flyout = openMenu('bottom')
    expect(flyout.className).toContain('bottom-full')
  })

  it('keeps a bottom-preferring menu DOWN when there is room below', () => {
    anchorAt(80)
    const flyout = openMenu('bottom')
    expect(flyout.className).toContain('top-full')
  })

  it('defaults to opening upward, preserving the pre-existing composer behavior', () => {
    anchorAt(700)
    const flyout = openMenu()
    expect(flyout.className).toContain('bottom-full')
  })

  it('sets the transform origin to the side it actually used', () => {
    // A menu that flips but grows from the old origin animates from the wrong edge.
    anchorAt(40)
    const flyout = openMenu('top')
    expect(flyout.style.transformOrigin).toContain('top')
  })
})
