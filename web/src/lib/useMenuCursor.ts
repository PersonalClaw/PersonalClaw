import { useCallback, useEffect, useRef, useState } from 'react'

/** The keyboard contract shared by the app's two context menus: **the cursor IS focus.**
 *
 *  Both menus already tracked a highlighted row in React state and painted it (heavier label
 *  weight + a trailing primary dot). Measured on the shared row menu before this hook existed,
 *  with the menu open on `#/inbox`: ArrowDown moved the paint (`wght 500` from row 0 to row 1,
 *  the dot with it) while `document.activeElement` stayed on the row BEHIND the menu the whole
 *  time — so the cursor existed only for people who could see it. axe reported **0 violations**
 *  on that open menu, because role, name and children were all correct; the missing part was the
 *  cursor's exposure, which no automated rule can ask about.
 *
 *  Moving real focus is what `ui/Popover` — the canonical popup in this kit — already does
 *  ("restores focus to the trigger on Escape/selection, so keyboard focus isn't dropped to
 *  <body>"), so this converges the two context menus onto the kit's own contract rather than
 *  inventing one. Focus, unlike a font weight:
 *    • is announced ("Open, menu item, 1 of 2"),
 *    • draws the focus ring, so sighted keyboard users see the cursor too,
 *    • activates on Enter/Space natively, with no parallel key handler to keep in sync,
 *    • collapses the menu's tab stops to ONE (roving tabindex), instead of leaving every row a
 *      stray tab stop at the end of the document.
 *
 *  Usage: give the container a ref, mark the rows `role="menuitem"`, spread `tabIndexFor(i)`,
 *  route Arrow keys to `move(±1)`, and call `restoreFocus()` on the Escape and selection paths
 *  (NOT on outside-click — the pointer is already elsewhere, mirroring Popover's split).
 */
export function useMenuCursor({
  containerRef,
  count,
  openKey,
}: {
  /** The element that owns the `role="menuitem"` rows. */
  containerRef: React.RefObject<HTMLElement | null>
  count: number
  /** `null` while closed; any NEW value means "opened afresh" — a reposition (right-clicking a
   *  second row without closing first) has to reset the cursor to the top, which a boolean
   *  `open` flag cannot express because it never changes. */
  openKey: string | null
}) {
  const [active, setActive] = useState(0)
  const invokerRef = useRef<HTMLElement | null>(null)

  // Remember what to hand focus back to, and start the cursor at the top, on every fresh open.
  // Declared BEFORE the focus effect so it captures the invoker while it is still the active
  // element — swapping these two would record the menu's own first row as its own invoker.
  useEffect(() => {
    if (openKey === null) return
    const prev = document.activeElement as HTMLElement | null
    // On a REPOSITION (right-clicking a second row without closing first) focus is already inside
    // the menu, and capturing a row as its own invoker would "restore" focus to a node about to
    // unmount — dropping it on <body>. Same trap `ui/useFocusReturn` documents; keep the original.
    if (prev && !containerRef.current?.contains(prev)) invokerRef.current = prev
    setActive(0)
  }, [openKey, containerRef])

  const rows = useCallback(
    () => Array.from(containerRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []),
    [containerRef],
  )

  useEffect(() => {
    if (openKey === null) return
    rows()[active]?.focus({ preventScroll: true })
  }, [openKey, active, count, rows])

  /** Clamped, not wrapping — matches what both menus did before, so only the channel changed. */
  const move = useCallback(
    (delta: number) => setActive((i) => Math.max(0, Math.min(count - 1, i + delta))),
    [count],
  )

  /** The same guard set `ui/useFocusReturn` uses — converged rather than re-derived, since it is
   *  the kit's existing answer to "restore focus without dropping it on <body>". The difference is
   *  WHEN: that hook fires on unmount (a panel), this one is called explicitly, so an outside
   *  click can decline it the way `ui/Popover` does. */
  const restoreFocus = useCallback(() => {
    const el = invokerRef.current
    // A detached node, or one inside the closing menu, cannot take focus — focusing it is a no-op
    // that leaves focus on <body>. Deleting an entry from its own row menu is the live case: the
    // row that opened the menu is gone by the time it closes.
    if (!el || !el.isConnected || typeof el.focus !== 'function') return
    if (containerRef.current?.contains(el)) return
    el.focus({ preventScroll: true })
  }, [containerRef])

  return {
    active,
    move,
    restoreFocus,
    /** Roving tabindex: the cursor row is the menu's single tab stop. */
    tabIndexFor: (i: number) => (i === active ? 0 : -1),
  }
}
