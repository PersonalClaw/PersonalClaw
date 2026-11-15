import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from 'react'

/** The ground a pane's fade starts from: its own, so the cut edge reads as the pane going on. The
 *  canvas under a page's list, `surface` in a detail pane. */
export type FadeGround = 'canvas' | 'surface'

/** How far the fade reaches into the pane, and how far a reveal stops clear of it (`h-2xl`). */
export const FADE_PX = 24

const FROM: Record<FadeGround, string> = { canvas: 'from-canvas', surface: 'from-surface' }

/** Where *el*'s content runs past its edges: below its bottom edge, and above its top once it has
 *  moved past it. A pane that fits fades nowhere. A pixel of slack is read as none, as a
 *  rounding of `scrollTop` at a fractional zoom leaves one. */
export function cutEdges(el: Pick<HTMLElement, 'scrollTop' | 'clientHeight' | 'scrollHeight'>): { top: boolean; bottom: boolean } {
  return {
    top: el.scrollTop > 1,
    bottom: el.scrollTop + el.clientHeight < el.scrollHeight - 1,
  }
}

/** A scrolling pane with the app's one cue that it runs past its edge (`web/DESIGN.md` §6).
 *  Scrollbars stay hidden app-wide (the tenet of 2026-07-07, `tokens.css`), so while the pane's
 *  content runs past its bottom edge a fade from the pane's own ground to transparent lies over
 *  that edge, and once it has moved past the top edge the same fade lies at the top.
 *
 *  It takes no pointer and says nothing (`aria-hidden`), and shows and goes at once, with no
 *  motion. It is measured on scroll, whenever the pane or anything in it changes size (a body that
 *  arrives after its skeleton, a Show all, a card that leaves, the list rewrapping when a panel
 *  opens, the sidebar collapsing: none of them resizes the window), and once the faces have
 *  loaded. The pane's `scroll-padding` keeps a card reached with the keys, a focused control and a
 *  reveal clear of the fade. Under forced colours, which drop a gradient, the fade keeps itself
 *  and runs from the user's own `Canvas`, so the cue is the same there. */
export function ScrollFade({ ground, className = '', style, scrollRef, children, ...rest }: {
  /** The pane's own ground, which the fade starts from. */
  ground: FadeGround
  /** Classes for the scrolling element itself (padding, its own data attributes' styling). */
  className?: string
  style?: CSSProperties
  /** The scrolling element, for a caller that scrolls or measures it. */
  scrollRef?: RefObject<HTMLDivElement | null>
  children: ReactNode
} & Record<`data-${string}`, string | boolean | undefined>) {
  const own = useRef<HTMLDivElement>(null)
  const pane = scrollRef ?? own
  const [edges, setEdges] = useState({ top: false, bottom: false })
  const measure = useCallback(() => {
    const el = pane.current
    if (!el) return
    const next = cutEdges(el)
    setEdges((was) => (was.top === next.top && was.bottom === next.bottom ? was : next))
  }, [pane])
  useLayoutEffect(measure, [measure, children])
  useEffect(() => {
    const el = pane.current
    if (!el) return
    const resized = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null
    const watch = () => {
      if (!resized) return
      resized.disconnect()
      resized.observe(el)
      for (const child of Array.from(el.children)) resized.observe(child)
    }
    watch()
    // A child that arrives or leaves is watched from then on.
    const rebound = typeof MutationObserver === 'function' ? new MutationObserver(() => { watch(); measure() }) : null
    rebound?.observe(el, { childList: true })
    let live = true
    document.fonts?.ready.then(() => { if (live) measure() }).catch(() => {})
    return () => {
      live = false
      resized?.disconnect()
      rebound?.disconnect()
    }
  }, [pane, measure])
  const fade = `pointer-events-none absolute inset-x-0 h-2xl ${FROM[ground]} to-transparent forced-colors:[forced-color-adjust:none] forced-colors:from-[Canvas]`
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        {...rest}
        ref={pane}
        onScroll={measure}
        className={`min-h-0 flex-1 overflow-y-auto ${className}`}
        style={{ scrollPaddingTop: FADE_PX, scrollPaddingBottom: FADE_PX, ...style }}
      >
        {children}
      </div>
      {edges.top && <span aria-hidden="true" data-fade="top" className={`${fade} top-0 bg-gradient-to-b`} />}
      {edges.bottom && <span aria-hidden="true" data-fade="bottom" className={`${fade} bottom-0 bg-gradient-to-t`} />}
    </div>
  )
}
