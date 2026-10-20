import type { KeyboardEvent } from 'react'

/** Arrow/Home/End navigation for a `role="tablist"`, as WAI-ARIA expects it.
 *
 *  A tab strip is ONE tab stop: Tab reaches the selected tab, then Arrow keys move between
 *  tabs (roving `tabIndex`, `0` on the selected one and `-1` on the rest). Without that,
 *  every tab is either unreachable or a separate stop, and neither is the pattern a
 *  screen-reader or keyboard user is expecting from something announced as a tablist.
 *
 *  `#/chat`'s activity panel already shipped exactly this logic; this is that function,
 *  lifted so the terminal strips and the loop cockpit read from one copy instead of each
 *  growing their own. It resolves the tabs from the DOM (`[role="tab"]` inside the element
 *  the handler is bound to) rather than from an array, so it works for strips whose tabs are
 *  dynamic — a terminal session list changes as sessions open and close.
 *
 *  Activation is AUTOMATIC (moving selects), which is the APG's recommendation when showing a
 *  panel is cheap: switching a terminal tab or a cockpit tab only swaps already-loaded state.
 *
 *  @param select  called with the new tab's INDEX among the strip's `[role="tab"]` elements,
 *                 in DOM order.
 */
export function tabListKeys(select: (index: number) => void) {
  return (e: KeyboardEvent<HTMLElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
    const strip = e.currentTarget
    const tabs = [...strip.querySelectorAll<HTMLElement>('[role="tab"]')]
      .filter((t) => !t.hasAttribute('disabled') && t.getAttribute('aria-disabled') !== 'true')
    if (tabs.length < 2) return
    // The current tab is where focus is; falling back to the SELECTED one matters when focus
    // sits on the strip itself (clicking a tab's close button, then pressing an arrow).
    const active = tabs.indexOf(document.activeElement as HTMLElement)
    const cur = active >= 0 ? active : tabs.findIndex((t) => t.getAttribute('aria-selected') === 'true')
    const last = tabs.length - 1
    const next = e.key === 'Home' ? 0
      : e.key === 'End' ? last
      : e.key === 'ArrowLeft' ? (cur <= 0 ? last : cur - 1)
      : (cur >= last ? 0 : cur + 1)
    // Wrapping in both directions, so End→ArrowRight returns to the first tab rather than
    // dead-ending. `preventDefault` keeps a horizontally scrollable strip from also panning.
    e.preventDefault()
    select(next)
    tabs[next].focus()
  }
}
