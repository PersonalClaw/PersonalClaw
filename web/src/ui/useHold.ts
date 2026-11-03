import { useCallback, useState, type FocusEvent } from 'react'

/** The handlers that say the pointer or the keyboard focus is on an element. */
export interface HoldHandlers {
  onPointerEnter: () => void
  onPointerLeave: () => void
  onFocus: () => void
  onBlur: (e: FocusEvent<HTMLElement>) => void
}

/** Whether the pointer or the keyboard focus is on an element, and the handlers to put on it.
 *
 *  For a surface that leaves on a timer: while you point at it or tab into it, its time stops
 *  (WCAG 2.2.1), as the buddy's card holds its fold (`BuddyCard`'s `onHold`). The two are told
 *  apart, so the pointer leaving while focus is still inside keeps the hold, and focus moving
 *  between two controls inside never lets it go. */
export function useHold(): {
  held: boolean
  hold: HoldHandlers
  release: () => void
  releaseFocus: () => void
} {
  const [pointer, setPointer] = useState(false)
  const [focus, setFocus] = useState(false)
  const onPointerEnter = useCallback(() => setPointer(true), [])
  const onPointerLeave = useCallback(() => setPointer(false), [])
  const onFocus = useCallback(() => setFocus(true), [])
  const onBlur = useCallback((e: FocusEvent<HTMLElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocus(false)
  }, [])
  /** Let go of both, for a surface that leaves by its own control (its Dismiss). */
  const release = useCallback(() => { setPointer(false); setFocus(false) }, [])
  /** Let go of focus alone, for a part that leaves with focus in it: a removed element sends no
   *  blur, so the hold would last until focus next moved. A pointer still on it keeps holding. */
  const releaseFocus = useCallback(() => setFocus(false), [])
  return {
    held: pointer || focus,
    hold: { onPointerEnter, onPointerLeave, onFocus, onBlur },
    release,
    releaseFocus,
  }
}
