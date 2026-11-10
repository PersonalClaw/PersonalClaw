import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/** How long the pointer rests on a control before its tooltip shows. */
export const TOOLTIP_DELAY_MS = 400
/** How long a tooltip waits after the pointer leaves, so the pointer can move onto it. */
const HIDE_GRACE_MS = 100
/** The gap between a control and its tooltip, and the least distance from the window's edge. */
const GAP = 8
/** The widest a tooltip grows before its text wraps, and never wider than the window. */
const BUBBLE_MAX_WIDTH = `min(20rem, calc(100vw - ${GAP * 2}px))`

/** What a trigger spreads onto the control it renders. */
export interface TooltipTriggerProps {
  'aria-describedby': string
  onPointerEnter: (e: React.PointerEvent) => void
  onPointerLeave: (e: React.PointerEvent) => void
  onPointerDown: (e: React.PointerEvent) => void
  onPointerUp: (e: React.PointerEvent) => void
  onPointerCancel: (e: React.PointerEvent) => void
  onFocus: (e: React.FocusEvent) => void
  onBlur: (e: React.FocusEvent) => void
  onKeyDown: (e: React.KeyboardEvent) => void
  ref: (el: HTMLElement | null) => void
}

/** A tooltip: says what a control does before you use it.
 *
 *  It shows after `TOOLTIP_DELAY_MS` of hover and at once on keyboard focus, stays while the
 *  pointer is over it, and hides on leave, blur and Escape. Escape is handled on the control (its
 *  own keydown, with `preventDefault`) and only while the tooltip shows, so it runs before the
 *  page's Escape listeners and closing a tooltip does not also stop a running turn. Its text is
 *  always the control's description: the control's `aria-describedby` names a hidden copy, and the
 *  bubble is that copy's visual twin, portaled at `--z-menu`. It never opens on touch, nor on the
 *  focus a press gives. `children` renders the control from the trigger props. */
export function Tooltip({ text, children }: { text: string; children: (trigger: TooltipTriggerProps) => ReactNode }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null)
  const triggerRef = useRef<HTMLElement | null>(null)
  const bubbleRef = useRef<HTMLDivElement | null>(null)
  const showTimer = useRef<number | null>(null)
  const hideTimer = useRef<number | null>(null)
  // A pointer press is about to focus the control: that focus is not a keyboard's, so it shows nothing.
  const pressing = useRef(false)

  const clear = (t: React.MutableRefObject<number | null>) => {
    if (t.current !== null) { window.clearTimeout(t.current); t.current = null }
  }
  const hide = useCallback(() => {
    clear(showTimer)
    clear(hideTimer)
    setOpen(false)
  }, [])
  const keepOpen = () => clear(hideTimer)
  const hideSoon = () => {
    clear(showTimer)
    clear(hideTimer)
    hideTimer.current = window.setTimeout(() => { hideTimer.current = null; setOpen(false) }, HIDE_GRACE_MS)
  }
  useEffect(() => () => { clear(showTimer); clear(hideTimer) }, [])

  // Above the control, centred on it and kept inside the window; below it when there is no room
  // above. The bubble is as wide as its text (up to a cap), wherever it sits, so a place near the
  // window's edge never squeezes it; it is placed again once painted, and as the page moves.
  useLayoutEffect(() => {
    if (!open) { setPlace(null); return }
    const measure = () => {
      const trigger = triggerRef.current
      const bubble = bubbleRef.current
      if (!trigger || !bubble) return
      const at = trigger.getBoundingClientRect()
      const size = bubble.getBoundingClientRect()
      const above = at.top - size.height - GAP
      const top = above >= GAP ? above : at.bottom + GAP
      const left = Math.min(Math.max(GAP, at.left + at.width / 2 - size.width / 2), Math.max(GAP, window.innerWidth - size.width - GAP))
      setPlace((cur) => (cur && cur.top === top && cur.left === left ? cur : { top, left }))
    }
    measure()
    const frame = window.requestAnimationFrame(measure)
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [open, text])

  const trigger: TooltipTriggerProps = {
    'aria-describedby': id,
    onPointerEnter: (e) => {
      if (e.pointerType === 'touch') return
      keepOpen()
      if (open || showTimer.current !== null) return
      showTimer.current = window.setTimeout(() => { showTimer.current = null; setOpen(true) }, TOOLTIP_DELAY_MS)
    },
    onPointerLeave: (e) => {
      if (e.pointerType === 'touch') return
      hideSoon()
    },
    onPointerDown: () => { pressing.current = true },
    // A mouse press that gave no focus (WebKit does not focus a pressed button) is over at its
    // release; a tap's focus comes after its pointerup, so touch and pen keep the mark until then.
    onPointerUp: (e) => { if (e.pointerType === 'mouse') pressing.current = false },
    onPointerCancel: () => { pressing.current = false },
    onFocus: (e) => {
      if (pressing.current) { pressing.current = false; return }
      if (e.currentTarget.hasAttribute('data-focus-returned')) return
      keepOpen()
      setOpen(true)
    },
    onBlur: () => {
      pressing.current = false
      hide()
    },
    onKeyDown: (e) => {
      if (e.key !== 'Escape' || !open) return
      e.preventDefault()
      hide()
    },
    ref: (el) => { triggerRef.current = el },
  }

  return (
    <>
      {children(trigger)}
      <span id={id} role="tooltip" hidden>{text}</span>
      {open && createPortal(
        <div ref={bubbleRef} data-tooltip-bubble="" aria-hidden data-type="caption"
          className="glass pointer-events-auto fixed z-[var(--z-menu)] rounded-sm px-s py-xs text-on-surface"
          style={{ width: 'max-content', maxWidth: BUBBLE_MAX_WIDTH, ...(place ? { top: place.top, left: place.left } : { top: 0, left: 0, visibility: 'hidden' }) }}
          onPointerEnter={keepOpen} onPointerLeave={hideSoon}>
          {text}
        </div>,
        document.body,
      )}
    </>
  )
}
