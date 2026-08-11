import { useEffect, useRef } from 'react'

/** Restore focus to whatever was focused before this element mounted, when it unmounts.
 *
 *  The RESTORE half of `useFocusTrap`, without the trap. A modal owns focus and must keep Tab
 *  inside it; a non-modal DOCK (`SidePanel`) is a sibling of the list beside it, so Tab should flow
 *  straight through — trapping there would be a bug, not a fix. But both owe the same courtesy on
 *  close: a panel dismissed while focus sits inside it drops focus to `<body>`, which throws a
 *  keyboard user back to the top of the page.
 *
 *  Measured before this existed: focusing SidePanel's Close button and pressing Escape left
 *  `document.activeElement === document.body`. `ui/Popover`, `useFocusTrap` and `DegradedChip` all
 *  restore; the dock was the outlier.
 *
 *  Attach the returned ref to the panel's root so the "is it still inside me?" guard can work.
 */
export function useFocusReturn<T extends HTMLElement = HTMLDivElement>() {
  const ref = useRef<T>(null)
  // Capture DURING RENDER (first run), not inside the effect — the subtlety `useFocusTrap`
  // documents and the reason this is a shared hook rather than a copied `useEffect`: React applies
  // a child's `autoFocus` during the same commit, BEFORE effects run. Capturing in the effect would
  // record the in-panel field as the "trigger" and then "restore" focus to a node being unmounted,
  // which drops focus to <body> — exactly the bug this hook exists to prevent.
  const prevActiveRef = useRef<HTMLElement | null>(null)
  if (prevActiveRef.current === null) {
    prevActiveRef.current = document.activeElement as HTMLElement | null
  }

  useEffect(() => {
    const root = ref.current
    const prevActive = prevActiveRef.current
    return () => {
      // Only restore a node that is still in the document AND outside this (closing) panel:
      // focusing a detached or in-panel element is a no-op that leaves focus on <body>.
      if (
        prevActive
        && prevActive.isConnected
        && !(root && root.contains(prevActive))
        && typeof prevActive.focus === 'function'
      ) {
        prevActive.focus()
      }
    }
  }, [])

  return ref
}
