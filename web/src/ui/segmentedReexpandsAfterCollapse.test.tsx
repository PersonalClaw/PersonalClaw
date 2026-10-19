import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, render } from '@testing-library/react'
import { Home, Library, Network, Scale } from 'lucide-react'
import { Segmented } from './Segmented'

// ── A collapsed strip must expand again once its row has room ────────────────
//
// `collapse="menu"` decides to fold by comparing the strip's natural width against the room its
// parent row gives it. A header row is CONTENT-SIZED: it is exactly as wide as what it holds. So
// once the strip had folded, which happens on a load where the header's control rail is briefly
// capped (its width settles over a few frames), the row shrank to the pill, the room the
// comparison saw was the pill's own width, and the strip stayed folded for the rest of the visit.
// Measured on the Knowledge page's view switcher: some loads showed all seven views and others
// one pill, at the same width.
//
// jsdom has no layout, so this models it: a row whose width is the sum of its children's (the
// content-sized rail) unless it is capped, a sibling control 50px wide, and a wrap that is 32px
// while it holds the pill and 300px while it holds the strip or is given a width.

const OPTS = [
  { key: 'home', label: 'Home', icon: Home },
  { key: 'library', label: 'Library', icon: Library },
  { key: 'graph', label: 'Graph', icon: Network },
  { key: 'conflicts', label: 'Conflicts', icon: Scale },
]
const NEED = 300
const PILL = 32
const SIBLING = 50

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function setup() {
  const observers: Array<() => void> = []
  vi.stubGlobal('ResizeObserver', class {
    cb: () => void
    constructor(cb: () => void) { this.cb = cb; observers.push(() => cb()) }
    observe() {} unobserve() {} disconnect() {}
  })
  let cap: number | null = 100
  const isRow = (el: Element) => el instanceof HTMLElement && el.dataset.testid === 'row'
  const isWrap = (el: Element) => !!el.parentElement && isRow(el.parentElement) && el.querySelector('[aria-hidden]') !== null
  const widthOf = (el: Element): number => {
    if (el instanceof HTMLElement && el.dataset.testid === 'sibling') return SIBLING
    if (isWrap(el)) {
      const w = (el as HTMLElement).style.width
      if (w.endsWith('px')) return parseFloat(w)
      const live = Array.from(el.children).find((c) => !c.hasAttribute('aria-hidden'))
      return live?.getAttribute('role') === 'radiogroup' ? NEED : PILL
    }
    return 0
  }
  vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(NEED)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
    if (!isRow(this)) return 0
    const content = Array.from(this.children).reduce((s, c) => s + widthOf(c), 0)
    return cap === null ? content : cap
  })
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const width = widthOf(this)
    return { width, height: 32, top: 0, left: 0, right: width, bottom: 32, x: 0, y: 0, toJSON() {} } as DOMRect
  })
  const r = render(
    <div data-testid="row">
      <Segmented options={OPTS} value="library" onChange={() => {}} ariaLabel="Knowledge view" collapse="menu" />
      <button data-testid="sibling" type="button">Add</button>
    </div>,
  )
  const live = () => {
    const wrap = r.getByTestId('row').firstElementChild!
    return Array.from(wrap.children).find((c) => !c.hasAttribute('aria-hidden'))!
  }
  const remeasure = () => act(() => { for (const o of observers) o() })
  const uncap = () => { cap = null; remeasure() }
  return { live, uncap, remeasure }
}

describe('Segmented collapse="menu"', () => {
  it('folds when the row is capped below the strip', () => {
    const { live } = setup()
    expect(live().getAttribute('role'), 'a 300px strip in a 100px row folds to the pill').not.toBe('radiogroup')
  })

  it('expands again once the cap is gone, though the row is only as wide as the pill', () => {
    const { live, uncap } = setup()
    expect(live().getAttribute('role')).not.toBe('radiogroup')
    uncap()
    expect(
      live().getAttribute('role'),
      'the row now grows with its content, so the strip fits; measuring the pill-wide row kept it folded',
    ).toBe('radiogroup')
  })

  it('stays folded while the cap holds', () => {
    const { live, remeasure } = setup()
    // A re-measure with the cap still in place must not flap the strip open.
    remeasure()
    remeasure()
    expect(live().getAttribute('role')).not.toBe('radiogroup')
  })
})
