import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type ReactNode,
} from 'react'

// ── The shared windowing primitive (DSC-13, resuming SM-3) ──────────────────
//
// 🔴 MEASURED, not assumed. SM-3 deferred sidebar windowing "pending measurement",
// so this atom took the measurement first. A real gateway (port 10777, home
// `/private/tmp/dsc13-wt/.dev-home`, both asserted from its READY line) over a real
// populated store — 5,000 session `.jsonl` files, 5,000 knowledge rows — driving the
// real built SPA at `#/chat/history` under a 4x CPU throttle (the Lighthouse
// convention; unthrottled on an M-series Mac the same curve tops out at 39.7ms and
// flatters the defect):
//
//     rows   DOM nodes   keystroke→paint   wheel→paint   nav→last-row
//      100      4,182          14.0ms         12.7ms        5,314ms
//      250      9,432          20.2ms         21.3ms        3,883ms
//      500     18,182          25.2ms         32.0ms        4,494ms
//    1,000     35,682          40.2ms         51.6ms        5,416ms
//    2,000     70,682          69.0ms         97.4ms        8,389ms
//    5,000    175,683         137.1ms        273.4ms       13,446ms
//
// 🔑 THE NUMBER SM-3 ASKED FOR: interaction degrades at **250 rows** (scroll first
// costs more than one 16.7ms frame) and is unambiguously broken by **1,000** (51.6ms
// scroll = three dropped frames, and the curve turns superlinear from there — 2x the
// rows costs 2.8x the scroll between 2,000 and 5,000). Keystroke latency crosses the
// 100ms "response feels instant" bound at 5,000. DOM nodes are linear at ~35/row, so
// nothing self-limits.
//
// So the threshold below is 64 — a ~4x margin under the first measurable frame loss,
// and low enough that the surfaces whose server page size is 100 (knowledge, runs)
// really do window rather than adopting an inert control.
//
// 🪤 WHAT NAIVE VIRTUALIZATION BREAKS, and what this does about each — every one has
// a test in `windowedList.test.tsx`:
//   · keyboard nav stops at the window edge → this owns Arrow/Home/End/Page keys and
//     scrolls + mounts the target row before focusing it, so Home/End reach row 1 and
//     row 5,000 that were never rendered.
//   · a focused row unmounts and focus falls to `<body>` → focus is parked on the
//     named container (never lost to the document) and RESTORED to the row when it
//     scrolls back.
//   · the announced count becomes the rendered count → `aria-setsize` is ALWAYS
//     `items.length`. A windowed list that says "20 items" when there are 5,000 is an
//     a11y regression, so the total is the only number this exposes.
//   · deep links stop working → `anchorKey` scrolls to and focuses a row at any
//     index, rendered or not.
//   · find-in-page silently stops finding things. This one CANNOT be preserved:
//     Ctrl+F searches the DOM, and the whole point is that most rows are not in it.
//     So it is STATED instead of dropped — `findHint` is REQUIRED, it names the
//     surface's own search field, and it is announced with the true total.
//
// Row height is DECLARED per surface via `rowHeights`, never assumed: 'variable'
// measures every mounted row with a ResizeObserver (log lines wrap; a row's padding
// is `--space-scale`, a user setting), 'uniform' trusts `estimateRowHeight`.

/** Rows above this count window; at or below it every row renders, exactly as before.
 *  ~4x margin under the measured 250-row first-frame-loss point (see the table above). */
export const WINDOWING_THRESHOLD = 64

/** Rows kept mounted above and below the viewport. Small on purpose: the overscan is
 *  what makes a fast scroll not flash empty, and every extra row is back on the curve. */
export const DEFAULT_OVERSCAN = 8

/** Focusable descendants of a row, in DOM order — the first one is the row's tab stop.
 *  `ListRow`'s hit target is an empty `absolute inset-0 -z-10` <button>, so this finds it. */
const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** The nearest scrollable ancestor. Every adopting surface scrolls in an ANCESTOR —
 *  `ui/WorkbenchLayout` (knowledge, inbox), the page's own `flex-1 overflow-y-auto`
 *  (sessions, runs), the log pane's `max-h-[60vh] overflow-y-auto` (diagnostics) — so
 *  the primitive must observe a scroller it does not own. `null` means "no scroller
 *  found yet": window everything conservatively rather than guessing at `document`. */
function findScroller(el: HTMLElement | null): HTMLElement | null {
  let p = el?.parentElement ?? null
  while (p) {
    const oy = getComputedStyle(p).overflowY
    if (oy === 'auto' || oy === 'scroll' || oy === 'overlay') return p
    p = p.parentElement
  }
  return null
}

export interface WindowedListRenderContext {
  /** True when the window is engaged (`items.length > threshold`). Adopters pass
   *  `index={ctx.windowed ? 0 : i}` to `ListRow`: its entrance stagger is keyed on the
   *  row index, and a windowed row REMOUNTS every time it scrolls back in — so an
   *  uncapped stagger replays the fade mid-scroll, on every row, forever. */
  windowed: boolean
}

export interface WindowedListProps<T> {
  /** The FULL collection. Never pre-slice: the count semantics, the keyboard reach and
   *  the deep-link all read `items.length`, and a pre-sliced list re-introduces exactly
   *  the "20 of 5,000" defect this primitive exists to prevent. */
  items: readonly T[]
  /** Stable identity per row. Focus survival and `anchorKey` are keyed on it, so an
   *  index-derived key silently breaks both the moment the list re-sorts. */
  rowKey: (item: T, index: number) => string
  /** DECLARED per surface, not inferred. 'variable' measures each mounted row;
   *  'uniform' takes `estimateRowHeight` as exact. */
  rowHeights: 'uniform' | 'variable'
  /** Row height in px INCLUDING nothing else — the gap is `gap`. Under 'variable' this
   *  is the first-paint estimate for rows never yet measured. */
  estimateRowHeight: number
  /** The vertical gap between rows that the container's own class applies
   *  (`gap-s` → 8px, `gap-xs` → 4px, `gap-1` → 4px). Folded into the offsets so the
   *  scroll height matches what the un-windowed list would have produced. */
  gap?: number
  /** Plural noun for the count sentence — "chats", "items", "runs", "lines". */
  noun: string
  /** REQUIRED, and it must name a real in-app affordance. Browser find cannot see
   *  un-rendered rows; this is the stated alternative, announced with the true total.
   *  A windowed list with no stated alternative is a silently defeated Ctrl+F. */
  findHint: string
  /** `rowKey` of a row to scroll to and focus once — the deep-link/anchor path.
   *  Changing it re-anchors; clearing it does nothing (the user's scroll wins). */
  anchorKey?: string
  overscan?: number
  /** Applied to the list container — pass the same layout classes the un-windowed
   *  `<div>` had (`flex flex-col gap-s`), so short lists are byte-identical to before. */
  className?: string
  /** Set false only for a list whose rows are not individually focusable. */
  enableRowKeyboard?: boolean
  children: (item: T, index: number, ctx: WindowedListRenderContext) => ReactNode
}

export function WindowedList<T>({
  items, rowKey, rowHeights, estimateRowHeight, gap = 0, noun, findHint,
  anchorKey, overscan = DEFAULT_OVERSCAN, className, enableRowKeyboard = true, children,
}: WindowedListProps<T>) {
  const total = items.length
  const windowed = total > WINDOWING_THRESHOLD
  const containerRef = useRef<HTMLDivElement | null>(null)
  const scrollerRef = useRef<HTMLElement | null>(null)
  /** Measured row heights live in a REF, with a version counter as the only state.
   *
   *  🔴 MEASURED: with heights in state, `attachRow` and the row-measuring observer both
   *  depended on it, so every scroll re-created the ref callback for all 18 rendered rows
   *  and React detached + re-attached (and the observer re-observed) each one. Cost:
   *  **46ms per wheel event, flat at every N** — better than un-windowed past ~1,000 rows
   *  but three dropped frames where the un-windowed list cost 12.7ms at 100. With the
   *  churn removed it is 12-16ms at every N (see windowedListAdoption.baseline.json). */
  const heightsRef = useRef<Map<string, number>>(new Map())
  const [heightsVersion, setHeightsVersion] = useState(0)
  const [range, setRange] = useState({ start: 0, end: Math.min(total, WINDOWING_THRESHOLD) })
  /** The row that had focus when it scrolled out of the window. Focus is parked on the
   *  container meanwhile — NEVER dropped to <body> — and returns here on remount. */
  const parkedRef = useRef<string | null>(null)
  /** A row we have asked to become focused once it mounts (keyboard reach / anchor). */
  const pendingFocusRef = useRef<string | null>(null)

  const keys = useMemo(() => items.map((it, i) => rowKey(it, i)), [items, rowKey])

  /** Cumulative offsets, one entry per row plus the terminator. Built from measured
   *  heights where known and the estimate elsewhere, so a never-scrolled list still
   *  has a correct-enough scroll height on first paint. */
  const offsets = useMemo(() => {
    const out = new Float64Array(total + 1)
    for (let i = 0; i < total; i++) {
      const h = heightsRef.current.get(keys[i]) ?? estimateRowHeight
      out[i + 1] = out[i] + h + gap
    }
    return out
    // heightsVersion is the dependency that makes a measurement re-run this; the map
    // itself is a stable ref precisely so nothing ELSE re-runs when a row is measured.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [total, keys, heightsVersion, estimateRowHeight, gap])

  const totalHeight = total > 0 ? offsets[total] - gap : 0

  /** Recompute the visible range from the scroller's current position. */
  const recompute = useCallback(() => {
    if (!windowed) return
    const el = containerRef.current
    const sc = scrollerRef.current
    if (!el) return
    let from = 0
    let viewport = 0
    if (sc) {
      // The container's top in the SCROLLER's content coordinates. Recomputed each
      // time rather than cached: filter chips and a selection bar above the list
      // appear and disappear, which moves this by ~40px.
      const listTop = el.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop
      from = sc.scrollTop - listTop
      viewport = sc.clientHeight
    } else {
      // No scroller resolved (jsdom, or a not-yet-laid-out mount): render a
      // threshold-sized window from the top rather than guessing.
      viewport = estimateRowHeight * Math.min(total, WINDOWING_THRESHOLD)
    }
    const to = from + viewport
    let start = 0
    while (start < total && offsets[start + 1] <= from) start++
    let end = start
    while (end < total && offsets[end] < to) end++
    start = Math.max(0, start - overscan)
    end = Math.min(total, end + overscan)
    // Anchor and parked-focus rows must stay reachable, but a row 4,000 away must not
    // drag 4,000 siblings into the window with it — the range stays contiguous and the
    // scroll (below) is what brings the target in.
    setRange((prev) => (prev.start === start && prev.end === end ? prev : { start, end }))
  }, [windowed, total, offsets, overscan, estimateRowHeight])

  // `recompute`'s identity changes whenever the offsets do, i.e. on every measurement.
  // Held in a ref so the scroll subscription below is installed ONCE — re-adding a
  // listener per measured row is the same churn the heights ref exists to remove.
  const recomputeRef = useRef(recompute)
  recomputeRef.current = recompute

  // Resolve the scroller and subscribe. Passive scroll listener + a resize observer on
  // the scroller, because a window resize changes how many rows fit.
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const sc = findScroller(el)
    scrollerRef.current = sc
    recomputeRef.current()
    if (!windowed) return
    // Coalesce to one recompute per frame: a trackpad fling delivers scroll events
    // faster than React can commit, and every uncoalesced one is a wasted render.
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => { raf = 0; recomputeRef.current() })
    }
    sc?.addEventListener('scroll', onScroll, { passive: true })
    let ro: ResizeObserver | undefined
    if (sc && typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => recomputeRef.current())
      ro.observe(sc)
    }
    return () => {
      if (raf) cancelAnimationFrame(raf)
      sc?.removeEventListener('scroll', onScroll)
      ro?.disconnect()
    }
  }, [windowed])

  // ── Row measurement ('variable' only) ─────────────────────────────────────
  // 🔑 Sub-pixel churn is filtered at 0.5px: below anything a user can see, above the
  // noise floor, and re-rendering on every fractional height change would put the
  // measurement itself back on the cost curve.
  const roRef = useRef<ResizeObserver | null>(null)
  const record = useCallback((key: string, h: number) => {
    if (!(h > 0)) return false
    const prev = heightsRef.current.get(key)
    if (prev !== undefined && Math.abs(prev - h) <= 0.5) return false
    heightsRef.current.set(key, h)
    return true
  }, [])

  useEffect(() => {
    if (rowHeights !== 'variable' || typeof ResizeObserver === 'undefined') return
    // The row's key is read off the element, so this observer needs no side map and no
    // dependency on the heights — installed once per surface, for the life of the list.
    const ro = new ResizeObserver((entries) => {
      let changed = false
      for (const e of entries) {
        const el = e.target as HTMLElement
        const key = el.dataset?.rowKey
        if (key && record(key, el.offsetHeight)) changed = true
      }
      if (changed) setHeightsVersion((v) => v + 1)
    })
    roRef.current = ro
    return () => { ro.disconnect(); roRef.current = null }
  }, [rowHeights, record])

  // Stable for the life of the list: React must not detach and re-attach every row's ref
  // on every scroll (see the heightsRef note above).
  const attachRow = useCallback((el: HTMLDivElement | null) => {
    if (!el) return
    roRef.current?.observe(el)
    // jsdom reports offsetHeight 0, so this is a no-op there and the estimate stands; in
    // a browser it seeds the height before the observer's first callback.
    const key = el.dataset?.rowKey
    if (key && record(key, el.offsetHeight)) setHeightsVersion((v) => v + 1)
  }, [record])

  /** Focus the first focusable descendant of a mounted row, or the row wrapper.
   *  Row wrappers are direct children, so this is a scan rather than a selector — a
   *  `rowKey` is caller data (a session key, a URL) and `CSS.escape` is not in every
   *  runtime this suite runs in. */
  const focusRow = useCallback((key: string): boolean => {
    const kids = containerRef.current?.children
    let el: HTMLElement | null = null
    for (let i = 0; kids && i < kids.length; i++) {
      const c = kids[i] as HTMLElement
      if (c.dataset?.rowKey === key) { el = c; break }
    }
    if (!el) return false
    const target = el.querySelector<HTMLElement>(FOCUSABLE) ?? el
    target.focus()
    return true
  }, [])

  /** Bring row `idx` into the window and focus it once it mounts — the one path shared
   *  by keyboard reach and `anchorKey`, so they cannot drift apart. */
  const reveal = useCallback((idx: number, focus: boolean) => {
    if (idx < 0 || idx >= total) return
    const key = keys[idx]
    if (focus) pendingFocusRef.current = key
    const sc = scrollerRef.current
    const el = containerRef.current
    if (windowed && sc && el) {
      const listTop = el.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop
      const rowTop = listTop + offsets[idx]
      const rowBottom = rowTop + (heightsRef.current.get(key) ?? estimateRowHeight)
      if (rowTop < sc.scrollTop) sc.scrollTop = rowTop
      else if (rowBottom > sc.scrollTop + sc.clientHeight) sc.scrollTop = rowBottom - sc.clientHeight
    }
    // Re-window AROUND the target rather than stretching the range to reach it: a
    // `{ start: 0, end: 4009 }` range would mount 4,009 rows to focus one, which is the
    // defect this primitive exists to remove.
    setRange({ start: Math.max(0, idx - overscan), end: Math.min(total, idx + Math.ceil((scrollerRef.current?.clientHeight ?? 0) / Math.max(1, estimateRowHeight)) + overscan + 1) })
    // 🪤 The row may ALREADY be mounted, in which case nothing re-renders and the
    // layout effect that delivers `pendingFocusRef` never runs — measured: ArrowDown
    // from row 0 stayed on row 0 while End (which always changes the range) worked. So
    // deliver the focus inline when we can.
    if (focus && focusRow(key)) pendingFocusRef.current = null
    if (!focus) recompute()
  }, [total, keys, windowed, offsets, estimateRowHeight, overscan, recompute, focusRow])

  // ── Focus survival ────────────────────────────────────────────────────────
  // 🔴 THE DEFECT: a row that unmounts while it holds focus drops focus to `<body>`.
  // The user's place in the list is gone AND their next Tab restarts from the top of
  // the document. Measured in this suite before the fix: after focusing row 3 and
  // scrolling to row 2,000, `document.activeElement === document.body`.
  //
  // 🪤 IT CANNOT BE DONE FROM A BLUR HANDLER. Removing a focused element does not
  // reliably fire a `focusout` React can see (it does not in jsdom at all), so the
  // handler that looked right never ran. This is checked from a layout effect keyed on
  // the RANGE instead: after every window move, ask whether the row that had focus is
  // still mounted. Deterministic, and it cannot miss the case that matters.
  const focusedKeyRef = useRef<string | null>(null)
  const onFocusIn = useCallback((e: React.FocusEvent<HTMLDivElement>) => {
    const row = (e.target as HTMLElement).closest<HTMLElement>('[data-row-key]')
    if (row) { focusedKeyRef.current = row.dataset.rowKey ?? null; parkedRef.current = null }
  }, [])

  const isMounted = useCallback((key: string) => {
    const kids = containerRef.current?.children
    for (let i = 0; kids && i < kids.length; i++) {
      if ((kids[i] as HTMLElement).dataset?.rowKey === key) return true
    }
    return false
  }, [])

  useLayoutEffect(() => {
    // Deliver a focus requested by keyboard reach / anchor once its row is in the DOM.
    const pending = pendingFocusRef.current
    if (pending && focusRow(pending)) { pendingFocusRef.current = null; return }
    if (!windowed) return
    const el = containerRef.current
    if (!el) return
    const parked = parkedRef.current
    if (parked) {
      // …and give it back the moment the row returns, but only while focus is still
      // parked here — if the user has moved on, we must not yank it back.
      if (document.activeElement === el && isMounted(parked)) {
        if (focusRow(parked)) { focusedKeyRef.current = parked; parkedRef.current = null }
      }
      return
    }
    const focused = focusedKeyRef.current
    if (!focused || isMounted(focused)) return
    // The focused row just left the window. Park, don't drop.
    parkedRef.current = focused
    focusedKeyRef.current = null
    el.focus()
  })

  // ── Keyboard reach ────────────────────────────────────────────────────────
  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!enableRowKeyboard || total === 0) return
    const el = containerRef.current
    const row = (e.target as HTMLElement).closest<HTMLElement>('[data-row-key]')
    const cur = row ? keys.indexOf(row.dataset.rowKey ?? '') : (parkedRef.current ? keys.indexOf(parkedRef.current) : -1)
    const page = Math.max(1, Math.floor((scrollerRef.current?.clientHeight ?? estimateRowHeight * 10) / Math.max(1, estimateRowHeight)))
    let next: number | null = null
    switch (e.key) {
      case 'ArrowDown': next = cur < 0 ? 0 : Math.min(total - 1, cur + 1); break
      case 'ArrowUp': next = cur <= 0 ? 0 : cur - 1; break
      case 'Home': next = 0; break
      case 'End': next = total - 1; break
      case 'PageDown': next = Math.min(total - 1, (cur < 0 ? 0 : cur) + page); break
      case 'PageUp': next = Math.max(0, (cur < 0 ? 0 : cur) - page); break
      default: return
    }
    if (next === null || !el) return
    e.preventDefault()
    // ⚠️ Home/End/Arrow past the window edge is the whole point: `reveal` scrolls AND
    // extends the range, so the target mounts on this commit and the layout effect
    // above focuses it. Without this, keyboard nav stops dead at the last rendered row.
    parkedRef.current = null
    reveal(next, true)
  }, [enableRowKeyboard, total, keys, estimateRowHeight, reveal])

  // ── Deep link / anchor ────────────────────────────────────────────────────
  const lastAnchor = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (!anchorKey || anchorKey === lastAnchor.current) { lastAnchor.current = anchorKey; return }
    lastAnchor.current = anchorKey
    const idx = keys.indexOf(anchorKey)
    if (idx >= 0) reveal(idx, true)
  }, [anchorKey, keys, reveal])

  const start = windowed ? Math.min(range.start, Math.max(0, total - 1)) : 0
  const end = windowed ? Math.min(range.end, total) : total
  const slice: ReactNode[] = []
  for (let i = start; i < end; i++) {
    const key = keys[i]
    slice.push(
      <div
        key={key}
        role="listitem"
        data-row-key={key}
        // 🔑 ALWAYS the true total, never `end - start`. A windowed list that tells a
        // screen reader "item 3 of 28" when the library holds 5,000 has traded a
        // rendering cost for an accessibility lie.
        aria-posinset={i + 1}
        aria-setsize={total}
        ref={attachRow}
      >
        {children(items[i], i, { windowed })}
      </div>,
    )
  }

  const hintId = `windowed-hint-${noun.replace(/\W+/g, '')}`
  return (
    <>
      {windowed && (
        <span id={hintId} className="sr-only">
          {`Showing ${end - start} of ${total} ${noun} at a time so the list stays fast. `
            + `Browser find only searches what is on screen — ${findHint}`}
        </span>
      )}
      <div
        ref={containerRef}
        role="list"
        // -1, not 0: this must be a focus PARKING spot when a row unmounts under the
        // user, and must not add a tab stop before every list in the app.
        tabIndex={-1}
        aria-describedby={windowed ? hintId : undefined}
        onKeyDown={onKeyDown}
        onFocus={onFocusIn}
        className={className}
        style={windowed
          ? { paddingTop: offsets[start], paddingBottom: Math.max(0, totalHeight - offsets[end] + gap) }
          : undefined}
      >
        {slice}
      </div>
    </>
  )
}
