import { useEffect, useRef } from 'react'

/** Trap keyboard focus within a container while it's mounted (the modal
 *  contract: a dialog with aria-modal must keep Tab focus inside it, not let it
 *  escape to the page behind). Tab/Shift+Tab cycle through the container's
 *  focusable elements; focus restores to the previously-focused element on
 *  unmount. Attach the returned ref to the dialog's root element. */
export function useFocusTrap<T extends HTMLElement = HTMLDivElement>() {
  const ref = useRef<T>(null)
  // Capture the element to restore focus to on close DURING RENDER (first run),
  // NOT inside the effect: React applies a child's `autoFocus` during the same
  // commit, BEFORE effects run, so by effect-time document.activeElement is
  // already the in-dialog field — capturing there would "restore" focus to a
  // node that's being unmounted (focus then falls to <body>). Reading it at the
  // hook's first render happens before the dialog's children commit, so it's the
  // true external trigger.
  const prevActiveRef = useRef<HTMLElement | null>(null)
  if (prevActiveRef.current === null) {
    prevActiveRef.current = document.activeElement as HTMLElement | null
  }

  useEffect(() => {
    const root = ref.current
    if (!root) return
    const prevActive = prevActiveRef.current

    const focusables = () =>
      Array.from(
        root.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement)

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const els = focusables()
      if (els.length === 0) { e.preventDefault(); return }
      const first = els[0]
      const last = els[els.length - 1]
      const active = document.activeElement as HTMLElement | null
      if (e.shiftKey) {
        if (active === first || !root.contains(active)) { e.preventDefault(); last.focus() }
      } else {
        if (active === last || !root.contains(active)) { e.preventDefault(); first.focus() }
      }
    }

    // Move focus INTO the dialog on open — the modal contract: a just-opened
    // aria-modal dialog should own focus, not leave it on the trigger behind the
    // scrim (Tab/typing would leak to the page + a screen reader wouldn't announce
    // the dialog). Skip if focus is ALREADY inside (an autoFocus input ran first —
    // React applies it during commit, before this effect — so we don't steal it).
    if (!root.contains(document.activeElement)) {
      const initial = focusables()
      if (initial.length > 0) {
        initial[0].focus()
      } else {
        root.setAttribute('tabindex', '-1')
        root.focus()
      }
    }

    root.addEventListener('keydown', onKey)
    return () => {
      root.removeEventListener('keydown', onKey)
      // Restore focus to the element focused BEFORE the dialog opened — but only
      // if it's still in the document and outside this (closing) dialog. Focusing
      // a detached or in-dialog node is a no-op that drops focus to <body>.
      if (prevActive && prevActive.isConnected && !root.contains(prevActive) && typeof prevActive.focus === 'function') {
        prevActive.focus()
      }
    }
  }, [])

  return ref
}
