import { useEffect, useRef } from 'react'

// ── Where focus goes when the sidebar opens and closes (#3812) ─────────────────────────────────────
//
// Opening the rail (the desktop rail expanding, or the phone's drawer) puts focus on the row that
// last had it, when that row is still there; else on the row the address lights, a thread before a
// destination; else on the rail's first row. A collapsed rail holds no thread rows, so when it
// opens they may still be on their way: focus waits on the nearest row there is, and moves to the
// thread's row when it arrives, unless the owner has moved it meanwhile. Closing the rail
// (collapsing, the drawer shutting, focus mode hiding it) puts focus back on the sidebar's toggle,
// when focus was in the rail: a collapsed rail drops its thread rows, and a shut drawer is inert,
// so focus left there would fall to the page's body.
//
// Each move yields to what the owner is doing: opening never takes focus from a field they are
// typing in (⌘B from the composer opens the rail and leaves the caret where it was), and closing
// moves focus only when it was in the rail. Nothing moves on the first paint.

/** The sidebar's own toggle (`ShellCornerLeft`) carries this attribute, so focus can come back to it. */
const RAIL_TOGGLE = '[data-rail-toggle]'

const RAIL = 'nav[data-tour="rail"]'
const ROW = 'button, a[href]'
const LIT_THREAD = '[data-thread-key][aria-current="page"]'
/** How long an opened rail's thread rows may take to arrive before focus stays where it waited. */
const ROWS_ARRIVE_MS = 2_000

/** A row's name for finding it again after the rail redraws it: a thread row's key, or a
 *  destination's label. */
function rowId(el: Element): string | null {
  const thread = el.closest('[data-thread-key]')
  if (thread) return `thread:${thread.getAttribute('data-thread-key')}`
  const label = el.getAttribute('aria-label')
  return label ? `label:${label}` : null
}

function findRow(rail: HTMLElement, id: string): HTMLElement | null {
  const [kind, value] = [id.slice(0, id.indexOf(':')), id.slice(id.indexOf(':') + 1)]
  const rows = [...rail.querySelectorAll<HTMLElement>(ROW)]
  return rows.find((r) => (kind === 'thread'
    ? r.closest('[data-thread-key]')?.getAttribute('data-thread-key') === value
    : r.getAttribute('aria-label') === value)) ?? null
}

/** Whether *el* takes typing: a text field, a text area, or editable content. */
function isTyping(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  if (el.isContentEditable) return true
  if (el instanceof HTMLTextAreaElement) return true
  return el instanceof HTMLInputElement && !['button', 'checkbox', 'radio', 'range', 'submit', 'reset', 'file', 'color'].includes(el.type)
}

/** Moves focus as the rail opens and closes. *open* is whether the rail shows its rows: expanded on
 *  a desktop, the drawer open on a phone, and not hidden by focus mode. */
export function useRailFocus(open: boolean): void {
  const lastRow = useRef<string | null>(null)
  const inRail = useRef(false)
  useEffect(() => {
    const onFocusIn = (e: FocusEvent) => {
      const target = e.target instanceof Element ? e.target : null
      const rail = target?.closest(RAIL)
      inRail.current = !!rail
      if (rail && target) {
        const row = target.closest(ROW)
        const id = row && rowId(row)
        if (id) lastRow.current = id
      }
    }
    document.addEventListener('focusin', onFocusIn)
    return () => document.removeEventListener('focusin', onFocusIn)
  }, [])

  const was = useRef(open)
  useEffect(() => {
    if (open === was.current) return
    was.current = open
    const active = document.activeElement
    if (!open) {
      const lost = !active || active === document.body || !!active.closest(RAIL)
      if (inRail.current && lost) document.querySelector<HTMLElement>(RAIL_TOGGLE)?.focus()
      return
    }
    if (isTyping(active) && !active?.closest(RAIL)) return
    const rail = document.querySelector<HTMLElement>(RAIL)
    if (!rail) return
    const remembered = lastRow.current
    // The row focus belongs on: the one that last had it, else the lit thread. While the rail was
    // collapsed the thread's destination was the one lit, so that is only where focus waits.
    const wanted = () => (remembered ? findRow(rail, remembered) : null) ?? rail.querySelector<HTMLElement>(LIT_THREAD)
    const now = wanted()
    if (now) { now.focus(); return }
    const waiting = rail.querySelector<HTMLElement>('[aria-current="page"]') ?? rail.querySelector<HTMLElement>(ROW)
    waiting?.focus()
    const observer = new MutationObserver(() => {
      const row = wanted()
      if (!row) return
      if (document.activeElement === waiting) row.focus()
      stop()
    })
    const timer = window.setTimeout(() => stop(), ROWS_ARRIVE_MS)
    const stop = () => { observer.disconnect(); window.clearTimeout(timer) }
    observer.observe(rail, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-current'] })
    return stop
  }, [open])
}
