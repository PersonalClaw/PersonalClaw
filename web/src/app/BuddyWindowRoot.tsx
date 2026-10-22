import { Suspense, useEffect, useSyncExternalStore } from 'react'

import { buddyBridge, type BuddyBridge, type BuddyRect } from '../lib/desktopBridge'
import { BuddyRoutePage } from '../pages/buddy/buddyRoute'
import { ErrorBoundary } from './ErrorBoundary'
import { useHashRoute } from './useHashRoute'

// ── The buddy's own window (#3831) ─────────────────────────────────────────────────────────────
//
// The page the shell's buddy window loads (`desktop/buddyWindow.js`): the buddy route, under
// the theme, appearance and personality providers and nothing else. None of `App()`'s socket
// hooks, polls or toasts run here: they belong to the main window, and a second copy would hold
// its own socket and raise every native banner twice.
//
// Free while hidden: while `document.hidden`, nothing below the root is mounted, so the route
// schedules no timer and no frame, and no pointer is read. The shell stops forwarding the pointer
// to a hidden window as well.
//
// Two marks are the window's contract with what the route draws (#3832):
// - `data-buddy-shape` on the face and the open card: clicks there reach the page, and everywhere
//   else they pass through to the app underneath.
// - `data-buddy-drag` on the face: a drag of it moves the window, docked or floating.

export const SHAPE = '[data-buddy-shape]'
export const DRAG = '[data-buddy-drag]'

/** Whether this page is the buddy's own window: the shell's buddy bridge, on the buddy route. A
 *  browser tab or the main window on the buddy route is the app's shell, as before. */
export function isBuddyWindow(): boolean {
  return buddyBridge() !== null && /^#\/buddy(?:[/?]|$)/.test(window.location.hash)
}

const onVisibility = (changed: () => void) => {
  document.addEventListener('visibilitychange', changed)
  return () => document.removeEventListener('visibilitychange', changed)
}

/** Whether the page is hidden: its window hidden, or minimised out of sight. */
export function useDocumentHidden(): boolean {
  return useSyncExternalStore(onVisibility, () => document.hidden, () => false)
}

/** The rectangles of everything marked as the shape, in the window's own points. */
export function shapeRects(root: ParentNode = document): BuddyRect[] {
  return [...root.querySelectorAll(SHAPE)].map((el) => {
    const r = el.getBoundingClientRect()
    return { x: r.left, y: r.top, width: r.width, height: r.height }
  }).filter((r) => r.width > 0 && r.height > 0)
}

/** Clicks pass through everywhere but the shape. On macOS the shell forwards the pointer's moves
 *  and the page says when it is over the shape; on Linux the shape's rectangles are the window's
 *  own shape, sent again when they change. The shell keeps the one its platform uses. */
function useClickThrough(bridge: BuddyBridge) {
  useEffect(() => {
    let over = false
    const say = (hit: boolean) => {
      if (hit === over) return
      over = hit
      bridge.pointerOver(hit)
    }
    const onMove = (e: MouseEvent) => say(e.target instanceof Element && e.target.closest(SHAPE) !== null)
    const onLeave = () => say(false)
    document.addEventListener('mousemove', onMove, { passive: true })
    document.documentElement.addEventListener('mouseleave', onLeave)

    let sent = ''
    const regions = () => {
      const rects = shapeRects()
      const key = JSON.stringify(rects)
      if (key === sent) return
      sent = key
      bridge.setRegions(rects)
    }
    // Observers, not a timer: they fire only when the shape is added, removed or resized.
    const sizes = new ResizeObserver(regions)
    const follow = () => {
      sizes.disconnect()
      for (const el of document.querySelectorAll(SHAPE)) sizes.observe(el)
      regions()
    }
    const marks = new MutationObserver(follow)
    marks.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-buddy-shape', 'class'] })
    follow()

    return () => {
      document.removeEventListener('mousemove', onMove)
      document.documentElement.removeEventListener('mouseleave', onLeave)
      marks.disconnect()
      sizes.disconnect()
      if (over) bridge.pointerOver(false)
    }
  }, [bridge])
}

/** A drag of the face moves the window by the pointer's deltas on the screen. The shell decides on
 *  release whether it docks or floats; under reduced motion it lands without a move. A drag that
 *  moved is not also a click on the face. */
function useFaceDrag(bridge: BuddyBridge) {
  useEffect(() => {
    let last: { x: number; y: number } | null = null
    let moved = false
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0 || !(e.target instanceof Element) || e.target.closest(DRAG) === null) return
      last = { x: e.screenX, y: e.screenY }
      moved = false
      e.target.setPointerCapture?.(e.pointerId)
      bridge.dragStart()
    }
    const onMove = (e: PointerEvent) => {
      if (!last) return
      const dx = e.screenX - last.x
      const dy = e.screenY - last.y
      if (dx === 0 && dy === 0) return
      last = { x: e.screenX, y: e.screenY }
      moved = true
      bridge.dragBy(dx, dy)
    }
    const swallowClick = (e: MouseEvent) => {
      e.stopPropagation()
      e.preventDefault()
    }
    const onUp = () => {
      if (!last) return
      last = null
      bridge.dragEnd(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true)
      if (moved) document.addEventListener('click', swallowClick, { capture: true, once: true })
    }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onUp)
    document.addEventListener('pointercancel', onUp)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('pointermove', onMove)
      document.removeEventListener('pointerup', onUp)
      document.removeEventListener('pointercancel', onUp)
      document.removeEventListener('click', swallowClick, { capture: true })
      if (last) bridge.dragEnd(true)
    }
  }, [bridge])
}

/** The route, while the window shows. */
function Shown({ bridge }: { bridge: BuddyBridge }) {
  useClickThrough(bridge)
  useFaceDrag(bridge)
  const { sub, navigate, navEpoch, query, setQuery } = useHashRoute('buddy')
  return (
    <ErrorBoundary resetKey={`buddy:${sub}`}>
      <Suspense fallback={null}>
        <BuddyRoutePage sub={sub} navigate={navigate} navEpoch={navEpoch} query={query} setQuery={setQuery} />
      </Suspense>
    </ErrorBoundary>
  )
}

export function BuddyWindowRoot() {
  const hidden = useDocumentHidden()
  const bridge = buddyBridge()
  useEffect(() => {
    // The window is transparent; so is the page, but for what the route draws.
    const root = document.documentElement
    root.dataset.buddyWindow = ''
    root.style.background = 'transparent'
    document.body.style.background = 'transparent'
  }, [])
  if (hidden || !bridge) return null
  return <Shown bridge={bridge} />
}
