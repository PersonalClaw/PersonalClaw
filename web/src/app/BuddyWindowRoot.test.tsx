import { act, cleanup, render } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { BuddyBridge } from '../lib/desktopBridge'

// ── The buddy's own window (#3831) ─────────────────────────────────────────────────────────────
//
// The route alone, free while hidden, clicks through everywhere but the shape, and a drag of the
// face moves the window. The route is a stand-in here: what it draws is #3832's, and the root's
// contract with it is the two marks, `data-buddy-shape` and `data-buddy-drag`.

/** A route that, while mounted, ticks as a live page would: a timer, an interval and a frame. */
vi.mock('../pages/buddy/buddyRoute', () => ({
  BuddyRoutePage: function Route() {
    useEffect(() => {
      const t = setInterval(() => {}, 1000)
      const f = requestAnimationFrame(() => {})
      return () => { clearInterval(t); cancelAnimationFrame(f) }
    }, [])
    return (
      <div>
        <button data-buddy-shape data-buddy-drag aria-label="Buddy">face</button>
        <p>under the shape</p>
      </div>
    )
  },
}))

const { BuddyWindowRoot, isBuddyWindow, shapeRects } = await import('./BuddyWindowRoot')

function fakeBridge() {
  return {
    pointerOver: vi.fn(),
    setRegions: vi.fn(),
    dragStart: vi.fn(),
    dragBy: vi.fn(),
    dragEnd: vi.fn(),
    setPresentation: vi.fn(),
    openInApp: vi.fn(),
    onRing: vi.fn(() => () => {}),
    onConnection: vi.fn(() => () => {}),
  } satisfies BuddyBridge
}

let hidden = false
function setHidden(next: boolean) {
  hidden = next
  act(() => { document.dispatchEvent(new Event('visibilitychange')) })
}

/** Lay the shape out where a real window would: jsdom measures nothing. */
function placeShape(el: Element, rect = { left: 0, top: 0, width: 56, height: 28 }) {
  el.getBoundingClientRect = () => ({ ...rect, x: rect.left, y: rect.top, right: rect.left + rect.width, bottom: rect.top + rect.height, toJSON: () => rect })
}

let bridge: ReturnType<typeof fakeBridge>
beforeEach(() => {
  hidden = false
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  bridge = fakeBridge()
  window.pclawBuddy = bridge
  window.location.hash = '#/buddy'
})
afterEach(() => {
  cleanup()
  delete window.pclawBuddy
  vi.restoreAllMocks()
})

describe('which root the page mounts', () => {
  it('is the buddy window only with the buddy bridge, on the buddy route', () => {
    expect(isBuddyWindow()).toBe(true)
    window.location.hash = '#/buddy/playground'
    expect(isBuddyWindow()).toBe(true)
    window.location.hash = '#/buddyish'
    expect(isBuddyWindow()).toBe(false)
    window.location.hash = '#/chat'
    expect(isBuddyWindow()).toBe(false)
    window.location.hash = '#/buddy'
    delete window.pclawBuddy
    expect(isBuddyWindow()).toBe(false)
  })
})

describe('free while hidden', () => {
  it('mounts the route while shown and nothing while hidden, and schedules no timer and no frame while hidden', async () => {
    const view = render(<BuddyWindowRoot />)
    expect(await view.findByRole('button', { name: 'Buddy' })).toBeTruthy()

    setHidden(true)
    expect(view.container.innerHTML).toBe('')

    const timeout = vi.spyOn(window, 'setTimeout')
    const interval = vi.spyOn(window, 'setInterval')
    const frame = vi.spyOn(window, 'requestAnimationFrame')
    // What would wake a page: the pointer, a resize, the hash.
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }))
    document.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    window.dispatchEvent(new Event('resize'))
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    await act(async () => {})
    expect(timeout).not.toHaveBeenCalled()
    expect(interval).not.toHaveBeenCalled()
    expect(frame).not.toHaveBeenCalled()
    expect(bridge.pointerOver).not.toHaveBeenCalled()
    expect(bridge.dragStart).not.toHaveBeenCalled()

    vi.restoreAllMocks()
    setHidden(false)
    expect(await view.findByRole('button', { name: 'Buddy' })).toBeTruthy()
  })

  it('a page that was mid-drag when hidden ends the drag, and the window lets go of the pointer', async () => {
    const view = render(<BuddyWindowRoot />)
    const face = await view.findByRole('button', { name: 'Buddy' })
    face.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }))
    face.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, screenX: 10, screenY: 10 }))
    setHidden(true)
    expect(bridge.dragEnd).toHaveBeenCalledWith(true)
    expect(bridge.pointerOver).toHaveBeenLastCalledWith(false)
  })
})

describe('clicks pass through everywhere but the shape', () => {
  it('says when the forwarded pointer comes over the shape and when it leaves, once each', async () => {
    const view = render(<BuddyWindowRoot />)
    const face = await view.findByRole('button', { name: 'Buddy' })
    const under = view.getByText('under the shape')
    face.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }))
    face.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }))
    expect(bridge.pointerOver.mock.calls).toEqual([[true]])
    under.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }))
    expect(bridge.pointerOver.mock.calls).toEqual([[true], [false]])
    face.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }))
    document.documentElement.dispatchEvent(new MouseEvent('mouseleave'))
    expect(bridge.pointerOver.mock.calls).toEqual([[true], [false], [true], [false]])
  })

  it('sends the shape as rectangles, and again only when it changes', async () => {
    const view = render(<BuddyWindowRoot />)
    const face = await view.findByRole('button', { name: 'Buddy' })
    placeShape(face)
    expect(shapeRects()).toEqual([{ x: 0, y: 0, width: 56, height: 28 }])
    await act(async () => { face.setAttribute('class', 'open') })
    expect(bridge.setRegions).toHaveBeenLastCalledWith([{ x: 0, y: 0, width: 56, height: 28 }])
    const calls = bridge.setRegions.mock.calls.length
    await act(async () => { face.setAttribute('class', 'open again') })
    expect(bridge.setRegions.mock.calls.length).toBe(calls)
    await act(async () => { face.removeAttribute('data-buddy-shape') })
    expect(bridge.setRegions).toHaveBeenLastCalledWith([])
  })
})

describe('a drag of the face moves the window', () => {
  it('sends the start, each move by its screen delta, and the end', async () => {
    const view = render(<BuddyWindowRoot />)
    const face = await view.findByRole('button', { name: 'Buddy' })
    face.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, screenX: 100, screenY: 100 }))
    face.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, screenX: 103, screenY: 160 }))
    face.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, screenX: 103, screenY: 160 }))
    face.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, screenX: 90, screenY: 200 }))
    face.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
    expect(bridge.dragStart).toHaveBeenCalledTimes(1)
    expect(bridge.dragBy.mock.calls).toEqual([[3, 60], [-13, 40]])
    expect(bridge.dragEnd).toHaveBeenCalledWith(false)
  })

  it('lands without a move under reduced motion', async () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((q: string) => ({ matches: q.includes('reduce'), media: q } as MediaQueryList))
    const view = render(<BuddyWindowRoot />)
    const face = await view.findByRole('button', { name: 'Buddy' })
    face.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, screenX: 0, screenY: 0 }))
    face.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
    expect(bridge.dragEnd).toHaveBeenCalledWith(true)
  })

  it('is not also a click on the face once it moved, and a press that did not move still clicks', async () => {
    const view = render(<BuddyWindowRoot />)
    const face = await view.findByRole('button', { name: 'Buddy' })
    const clicked = vi.fn()
    face.addEventListener('click', clicked)
    face.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, screenX: 0, screenY: 0 }))
    face.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, screenX: 0, screenY: 80 }))
    face.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
    face.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(clicked).not.toHaveBeenCalled()
    face.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, screenX: 0, screenY: 80 }))
    face.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
    face.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(clicked).toHaveBeenCalledTimes(1)
  })

  it('starts only from the face, and only with the main button', async () => {
    const view = render(<BuddyWindowRoot />)
    const face = await view.findByRole('button', { name: 'Buddy' })
    view.getByText('under the shape').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
    face.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 2 }))
    expect(bridge.dragStart).not.toHaveBeenCalled()
  })
})
