import { useEffect, useRef, type RefObject } from 'react'

// ── The sidebar keeps where it was scrolled (#3812) ────────────────────────────────────────────────
//
// The rail's scroll area remembers how far down it was, on this device, as its width and open state
// are remembered: a reload, or the rail opening again after a collapse, comes back to the same rows.
// Only the full rail's position counts. The collapsed rail holds no thread rows, so its scroll area
// is short and always at the top; letting it write would forget the full rail's place on every
// collapse.
//
// The rows a restored position needs may still be loading when the rail mounts (the threads come
// from their own reads), so the position is applied once the scroll area is tall enough to reach
// it. It is given up, and whatever the rows allow is kept, once the owner scrolls, presses a key or
// touches the rail themselves, or after `SETTLE_MS`. The address still wins: when the restored
// position would leave the thread the page shows out of view, the rail scrolls just far enough to
// show it, as it does when the address changes.

export const SCROLL_KEY = 'nav-scroll'
/** How long a restore waits for the rows it needs before it keeps what they allow. */
const SETTLE_MS = 3_000

/** Scrolls *sc* just far enough to show the lit thread's row, when the position left it out of view,
 *  clear of the top the scroller keeps clear (its `scroll-padding-top`, the threads' pinned bar).
 *  Never `scrollIntoView`, which may scroll the page as well, as `ThreadRail`'s `reveal` says. */
function keepLitThreadShown(sc: HTMLElement): void {
  const row = sc.querySelector<HTMLElement>('[data-thread-key][aria-current="page"]')
  if (!row) return
  const box = sc.getBoundingClientRect()
  const r = row.getBoundingClientRect()
  const top = box.top + (parseFloat(getComputedStyle(sc).scrollPaddingTop) || 0)
  if (r.top < top) sc.scrollTop -= top - r.top
  else if (r.bottom > box.bottom) sc.scrollTop += r.bottom - box.bottom
}

function readSaved(): number {
  const v = Number(localStorage.getItem(SCROLL_KEY))
  return Number.isFinite(v) && v > 0 ? v : 0
}

/** Remembers and restores the scroll of *ref*'s element while *full* (the expanded rail, or the
 *  phone's drawer). */
export function useRememberedScroll(ref: RefObject<HTMLElement | null>, full: boolean): void {
  const restoring = useRef(false)
  useEffect(() => {
    const sc = ref.current
    if (!sc || !full) return
    const target = readSaved()
    let done = target === 0
    restoring.current = !done
    const finish = () => {
      if (done) return
      done = true
      restoring.current = false
      observer.disconnect()
      window.clearTimeout(timer)
      for (const ev of ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const) sc.removeEventListener(ev, finish)
    }
    const tryApply = () => {
      if (done) return
      if (sc.scrollHeight - sc.clientHeight >= target) {
        sc.scrollTop = target
        keepLitThreadShown(sc)
        finish()
      }
    }
    const observer = new MutationObserver(tryApply)
    const timer = window.setTimeout(() => {
      if (!done) { sc.scrollTop = target; keepLitThreadShown(sc) }
      finish()
    }, SETTLE_MS)
    if (!done) {
      for (const ev of ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const) sc.addEventListener(ev, finish, { passive: true })
      observer.observe(sc, { childList: true, subtree: true })
      tryApply()
    }
    const onScroll = () => {
      if (restoring.current) return
      try { localStorage.setItem(SCROLL_KEY, String(Math.round(sc.scrollTop))) } catch { /* private mode: the rail still scrolls */ }
    }
    sc.addEventListener('scroll', onScroll, { passive: true })
    return () => { finish(); sc.removeEventListener('scroll', onScroll) }
  }, [ref, full])
}
