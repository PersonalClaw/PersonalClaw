import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import { WindowedList, WINDOWING_THRESHOLD, DEFAULT_OVERSCAN } from './WindowedList'

// ── DSC-13: the windowing primitive's contract, clause by clause ────────────
//
// The atom names five things naive virtualization breaks and requires each to be
// ASSERTED, not claimed. One `describe` per clause below, plus the perf clause itself
// (only visible rows + overscan render) and the honesty clause (the count a screen
// reader hears is the TRUE total, never the rendered one).
//
// 🪤 jsdom has no layout: `getBoundingClientRect` is all zeros, `clientHeight` is 0 and
// `offsetHeight` is 0. So `mount()` below WIRES the three metrics the primitive reads —
// the scroller's `clientHeight`/`scrollTop` and both elements' rects — and `scrollTo`
// moves them together the way a real scroller does. Without that wiring every range
// computation collapses to `from = 0` and the tests would pass against a window that
// never moves, which is the fake-clean this file exists to avoid: the perf test below
// is falsified by making the window render everything, and it must go RED.

const ROW_H = 50
const VIEWPORT = 400

// The primitive coalesces scroll handling to one recompute per animation frame (a
// trackpad fling outruns React's commit). jsdom's rAF is a ~16ms timer, so a synchronous
// test would assert against a window that has not moved yet. This queues the callbacks
// and `flushRaf` drains them inside `act` — which exercises the coalescing guard for
// real rather than stubbing it away.
const rafQueue: FrameRequestCallback[] = []
beforeEach(() => {
  rafQueue.length = 0
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { rafQueue.push(cb); return rafQueue.length })
  vi.stubGlobal('cancelAnimationFrame', () => { /* the queue is drained per assertion */ })
})
const flushRaf = () => act(() => { for (const cb of rafQueue.splice(0)) cb(0) })

interface Row { id: string; title: string }

function rows(n: number): Row[] {
  return Array.from({ length: n }, (_, i) => ({ id: `r${i}`, title: `Row ${i}` }))
}

interface MountOpts {
  n: number
  anchorKey?: string
  rowHeights?: 'uniform' | 'variable'
  enableRowKeyboard?: boolean
}

function mount({ n, anchorKey, rowHeights = 'uniform', enableRowKeyboard }: MountOpts) {
  const items = rows(n)
  const tree = (anchor?: string) => (
    <div data-testid="scroller" style={{ overflowY: 'auto' }}>
      <WindowedList
        items={items}
        rowKey={(it) => it.id}
        rowHeights={rowHeights}
        estimateRowHeight={ROW_H}
        gap={0}
        noun="rows"
        findHint="use the Search rows field above."
        anchorKey={anchor}
        className="flex flex-col"
        enableRowKeyboard={enableRowKeyboard}
      >
        {(it) => <button data-row-btn={it.id}>{it.title}</button>}
      </WindowedList>
    </div>
  )
  const view = render(tree(undefined))
  const scroller = screen.getByTestId('scroller')
  const list = screen.getByRole('list')
  let top = 0
  Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => VIEWPORT })
  Object.defineProperty(scroller, 'scrollTop', { configurable: true, get: () => top, set: (v: number) => { top = v } })
  scroller.getBoundingClientRect = () => ({ top: 0, bottom: VIEWPORT, left: 0, right: 0, width: 0, height: VIEWPORT, x: 0, y: 0, toJSON: () => ({}) })
  // The list scrolls UP out of the scroller as scrollTop grows, so its rect top is
  // -scrollTop. That is what makes `listTop` resolve to 0 and `from` to `scrollTop`.
  list.getBoundingClientRect = () => ({ top: -top, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) })
  const scrollTo = (px: number) => {
    act(() => { scroller.scrollTop = px; fireEvent.scroll(scroller) })
    flushRaf()
  }
  scrollTo(0)
  // 🪤 The anchor is applied AFTER the metric stubs are wired, not as a mount prop:
  // jsdom has no layout until we install the getters above, so a mount-time anchor would
  // compute every offset against a zero-height scroller and then be overwritten by the
  // first scroll event. This is also the realistic path — a deep link arrives as a URL
  // param change on an already-mounted list.
  const setAnchor = (key: string) => act(() => { view.rerender(tree(key)) })
  if (anchorKey) setAnchor(anchorKey)
  return { items, scroller, list, scrollTo, setAnchor, ...view }
}

const renderedKeys = (list: HTMLElement) =>
  Array.from(list.children).map((c) => (c as HTMLElement).dataset.rowKey).filter(Boolean) as string[]

afterEach(() => { vi.unstubAllGlobals() })

describe('WindowedList — the perf clause: only visible rows plus a small overscan', () => {
  it('renders ~viewport+overscan rows out of 5,000, not 5,000', () => {
    const { list } = mount({ n: 5000 })
    const keys = renderedKeys(list)
    const visible = Math.ceil(VIEWPORT / ROW_H)                    // 8
    // 🔴 FALSIFIED by making the window render every row: this asserted ceiling is the
    // only thing standing between the primitive and a no-op adoption.
    expect(keys.length).toBeLessThanOrEqual(visible + DEFAULT_OVERSCAN * 2 + 2)
    expect(keys.length).toBeGreaterThan(0)
    expect(keys[0]).toBe('r0')
    expect(screen.queryByText('Row 4999')).toBeNull()
  })

  it('a list at or below the threshold renders EVERY row and adds no padding', () => {
    const { list } = mount({ n: WINDOWING_THRESHOLD })
    expect(renderedKeys(list)).toHaveLength(WINDOWING_THRESHOLD)
    // Byte-identical to the plain <div> it replaces: no inline padding, no hint.
    expect(list.getAttribute('style')).toBeNull()
    expect(list.getAttribute('aria-describedby')).toBeNull()
  })

  it('the window MOVES with the scroller, so a deep scroll renders deep rows', () => {
    const { list, scrollTo } = mount({ n: 5000 })
    expect(renderedKeys(list)[0]).toBe('r0')
    scrollTo(2000 * ROW_H)
    const keys = renderedKeys(list)
    expect(keys[0]).toBe(`r${2000 - DEFAULT_OVERSCAN}`)
    expect(keys).toContain('r2000')
    expect(keys).not.toContain('r0')
  })

  it('the scroll height the un-windowed list would have had is preserved in padding', () => {
    const { list, scrollTo } = mount({ n: 5000 })
    scrollTo(1000 * ROW_H)
    const keys = renderedKeys(list)
    const pt = Number.parseFloat(list.style.paddingTop)
    const pb = Number.parseFloat(list.style.paddingBottom)
    expect(pt + keys.length * ROW_H + pb).toBe(5000 * ROW_H)
  })
})

describe('WindowedList — clause: keyboard navigation reaches rows not yet rendered', () => {
  it('End focuses row 5,000, which was never rendered', () => {
    const { list } = mount({ n: 5000 })
    expect(renderedKeys(list)).not.toContain('r4999')
    const first = list.querySelector<HTMLElement>('[data-row-btn]')!
    act(() => first.focus())
    act(() => { fireEvent.keyDown(first, { key: 'End' }) })
    // 🔴 FALSIFIED by deleting the `reveal()` call from the key handler: nav then stops
    // dead at the last rendered row and this goes RED.
    expect(renderedKeys(list)).toContain('r4999')
    expect((document.activeElement as HTMLElement).dataset.rowBtn).toBe('r4999')
  })

  it('Home from the bottom of a 5,000-row list focuses row 1', () => {
    const { list, scrollTo } = mount({ n: 5000 })
    scrollTo(4990 * ROW_H)
    expect(renderedKeys(list)).not.toContain('r0')
    const someRow = list.querySelector<HTMLElement>('[data-row-btn]')!
    act(() => someRow.focus())
    act(() => { fireEvent.keyDown(someRow, { key: 'Home' }) })
    expect((document.activeElement as HTMLElement).dataset.rowBtn).toBe('r0')
  })

  it('ArrowDown across the window edge keeps walking (row 8 → 9 → 10 …)', () => {
    const { list } = mount({ n: 5000 })
    let el = list.querySelector<HTMLElement>('[data-row-btn]')!
    act(() => el.focus())
    for (let i = 0; i < 40; i++) {
      el = document.activeElement as HTMLElement
      act(() => { fireEvent.keyDown(el, { key: 'ArrowDown' }) })
    }
    expect((document.activeElement as HTMLElement).dataset.rowBtn).toBe('r40')
  })

  it('PageDown advances by a viewport of rows, not by one', () => {
    const { list } = mount({ n: 5000 })
    const first = list.querySelector<HTMLElement>('[data-row-btn]')!
    act(() => first.focus())
    act(() => { fireEvent.keyDown(first, { key: 'PageDown' }) })
    expect((document.activeElement as HTMLElement).dataset.rowBtn).toBe(`r${VIEWPORT / ROW_H}`)
  })

  it('enableRowKeyboard=false leaves the keys to the browser (a log tail)', () => {
    const { list } = mount({ n: 5000, enableRowKeyboard: false })
    const first = list.querySelector<HTMLElement>('[data-row-btn]')!
    act(() => first.focus())
    act(() => { fireEvent.keyDown(first, { key: 'End' }) })
    expect(renderedKeys(list)).not.toContain('r4999')
  })
})

describe('WindowedList — clause: screen-reader row/count semantics stay correct', () => {
  it('aria-setsize is the TRUE total on every rendered row, never the rendered count', () => {
    const { list } = mount({ n: 5000 })
    const kids = Array.from(list.children) as HTMLElement[]
    expect(kids.length).toBeLessThan(100)          // we really are windowed
    // 🔴 FALSIFIED by setting aria-setsize to the rendered count: this goes RED, which
    // is the whole point — "20 of 5,000" is the accessibility regression the atom names.
    for (const k of kids) expect(k.getAttribute('aria-setsize')).toBe('5000')
    expect(list.getAttribute('role')).toBe('list')
    for (const k of kids) expect(k.getAttribute('role')).toBe('listitem')
  })

  it('aria-posinset is the row\'s position in the WHOLE list, not in the window', () => {
    const { list, scrollTo } = mount({ n: 5000 })
    scrollTo(3000 * ROW_H)
    const kids = Array.from(list.children) as HTMLElement[]
    const posOf = (key: string) => kids.find((k) => k.dataset.rowKey === key)!.getAttribute('aria-posinset')
    expect(posOf('r3000')).toBe('3001')
    expect(posOf('r3001')).toBe('3002')
  })

  it('role="list" has ONLY listitem children — the spacers are padding, not elements', () => {
    const { list, scrollTo } = mount({ n: 5000 })
    scrollTo(1000 * ROW_H)
    const nonItems = Array.from(list.children).filter((c) => c.getAttribute('role') !== 'listitem')
    expect(nonItems).toHaveLength(0)
    expect(Number.parseFloat(list.style.paddingTop)).toBeGreaterThan(0)
  })
})

describe('WindowedList — clause: find-in-page is not silently defeated', () => {
  it('states the alternative AND the true total, and the list points at it', () => {
    const { list } = mount({ n: 5000 })
    const id = list.getAttribute('aria-describedby')
    expect(id).toBeTruthy()
    const hint = document.getElementById(id!)!
    expect(hint.className).toContain('sr-only')
    expect(hint.textContent).toContain('of 5000 rows')
    expect(hint.textContent).toContain('Browser find only searches what is on screen')
    expect(hint.textContent).toContain('use the Search rows field above.')
  })

  it('says nothing when the list is short, because nothing is hidden from find', () => {
    mount({ n: 10 })
    expect(screen.queryByText(/Browser find/)).toBeNull()
  })
})

describe('WindowedList — clause: a focused row survives scrolling out and back', () => {
  it('focus is parked on the list (never dropped to <body>) and RESTORED on return', () => {
    const { list, scrollTo } = mount({ n: 5000 })
    const target = list.querySelector<HTMLElement>('[data-row-btn="r3"]')!
    act(() => target.focus())
    expect((document.activeElement as HTMLElement).dataset.rowBtn).toBe('r3')

    scrollTo(2000 * ROW_H)                       // r3 unmounts under the focus
    expect(renderedKeys(list)).not.toContain('r3')
    // The defect this prevents: activeElement === document.body, so the user's place in
    // the list AND their next Tab are gone.
    expect(document.activeElement).not.toBe(document.body)
    expect(document.activeElement).toBe(list)

    scrollTo(0)                                  // …and back
    expect(renderedKeys(list)).toContain('r3')
    expect((document.activeElement as HTMLElement).dataset.rowBtn).toBe('r3')
  })
})

describe('WindowedList — clause: anchor / deep-link to a row still scrolls to it', () => {
  it('anchorKey mounts, scrolls to and focuses a row 4,000 deep', () => {
    const { list, scroller } = mount({ n: 5000, anchorKey: 'r4000' })
    // 🔴 FALSIFIED by making the anchor effect a no-op: the row stays unrendered, the
    // scroller stays at 0 and this goes RED.
    expect(renderedKeys(list)).toContain('r4000')
    expect((document.activeElement as HTMLElement).dataset.rowBtn).toBe('r4000')
    expect(scroller.scrollTop).toBeGreaterThan(0)
  })

  it('an anchor that is not in the list is a no-op, not a crash', () => {
    const { list, scroller } = mount({ n: 5000, anchorKey: 'nope' })
    expect(renderedKeys(list)[0]).toBe('r0')
    expect(scroller.scrollTop).toBe(0)
  })
})

describe('WindowedList — clause: variable row heights are supported', () => {
  it('measured heights replace the estimate and move the offsets', () => {
    // A firing ResizeObserver, following ui/headerSegmentedCollapse.test.tsx's precedent
    // (the global setup stub is deliberately inert).
    const cbs: ResizeObserverCallback[] = []
    const observed = new Set<Element>()
    vi.stubGlobal('ResizeObserver', class {
      constructor(cb: ResizeObserverCallback) { cbs.push(cb) }
      observe(el: Element) { observed.add(el) }
      unobserve(el: Element) { observed.delete(el) }
      disconnect() { observed.clear() }
    })
    const { list, scrollTo } = mount({ n: 5000, rowHeights: 'variable' })
    const before = Number.parseFloat(list.style.paddingBottom)

    // Every mounted row turns out to be twice as tall as the estimate (a wrapped log
    // line, a row whose meta line ran onto a second line).
    const kids = Array.from(list.children) as HTMLElement[]
    for (const k of kids) Object.defineProperty(k, 'offsetHeight', { configurable: true, get: () => ROW_H * 2 })
    act(() => { for (const cb of cbs) cb(kids.map((t) => ({ target: t })) as unknown as ResizeObserverEntry[], {} as ResizeObserver) })
    scrollTo(0)

    // The rows we measured are now 100px each, so the total (and therefore the trailing
    // padding) grew. Under 'uniform' the estimate would be taken as exact and nothing
    // would move — which is why the mode is DECLARED per surface rather than guessed.
    expect(Number.parseFloat(list.style.paddingBottom)).toBeGreaterThan(before)
  })

  it('uniform mode never observes a row, so it costs nothing to scroll', () => {
    const observed: Element[] = []
    vi.stubGlobal('ResizeObserver', class {
      constructor(_cb: ResizeObserverCallback) {}
      observe(el: Element) { observed.push(el) }
      unobserve() {}
      disconnect() {}
    })
    mount({ n: 5000, rowHeights: 'uniform' })
    // The only observer a uniform list installs is the one on the SCROLLER (a window
    // resize changes how many rows fit); no row is measured.
    expect(observed.filter((e) => (e as HTMLElement).dataset?.rowKey)).toHaveLength(0)
  })
})
