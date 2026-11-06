import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, render } from '@testing-library/react'
import { Home, Library, Network, Scale } from 'lucide-react'
import { Segmented } from './Segmented'

// ── A strip in a `display: contents` wrapper measures the row that lays it out ─
//
// The header's probes mark each member with a `display: contents` span (`useHeaderChild`'s
// `mark`), so the cluster can read a member's width by name. `collapse="menu"` measured its room
// in `wrap.parentElement`, which there is the mark: a box of 0 px. So every probe drew the view
// switch folded to its 44 px pill, the cluster chose its tier for a row about 100 px narrower than
// the one it drew, and the title paid. Measured at 390 px on a project's Tasks: the icon probe read
// 242 px, the row drew 342 px, and the heading was a lone "›".
//
// jsdom has no layout, so this models it as `segmentedReexpandsAfterCollapse` does: a row whose
// width is the sum of its children's unless it is capped, a sibling 50 px wide, and a wrap that is
// 32 px holding the pill and 300 px holding the strip or given a width. The wrap sits in a mark.

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

function setup(cap: number | null) {
  vi.stubGlobal('ResizeObserver', class {
    observe() {} unobserve() {} disconnect() {}
  })
  const isRow = (el: Element) => el instanceof HTMLElement && el.dataset.testid === 'row'
  const isWrap = (el: Element) => el.parentElement?.dataset.testid === 'mark' && el.querySelector('[aria-hidden]') !== null
  const widthOf = (el: Element): number => {
    if (el instanceof HTMLElement && el.dataset.testid === 'sibling') return SIBLING
    if (isWrap(el)) {
      const w = (el as HTMLElement).style.width
      if (w.endsWith('px')) return parseFloat(w)
      const live = Array.from(el.children).find((c) => !c.hasAttribute('aria-hidden'))
      return live?.getAttribute('role') === 'radiogroup' ? NEED : PILL
    }
    // A `display: contents` box has no box of its own: the browser reports it as 0.
    return 0
  }
  const laidOut = (el: Element): number => Array.from(el.children).reduce(
    (s, c) => s + ((c as HTMLElement).style.display === 'contents' ? laidOut(c) : widthOf(c)), 0)
  vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(NEED)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
    if (!isRow(this)) return 0
    return cap === null ? laidOut(this) : cap
  })
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const width = widthOf(this)
    return { width, height: 32, top: 0, left: 0, right: width, bottom: 32, x: 0, y: 0, toJSON() {} } as DOMRect
  })
  let r!: ReturnType<typeof render>
  act(() => {
    r = render(
      <div data-testid="row">
        <span data-testid="mark" style={{ display: 'contents' }}>
          <Segmented options={OPTS} value="library" onChange={() => {}} ariaLabel="View" collapse="menu" />
        </span>
        <button data-testid="sibling" type="button">Add</button>
      </div>,
    )
  })
  const wrap = r.getByTestId('mark').firstElementChild!
  return Array.from(wrap.children).find((c) => !c.hasAttribute('aria-hidden'))!
}

describe('Segmented collapse="menu" in a display: contents wrapper', () => {
  it('draws the strip where the row has room for it', () => {
    expect(
      setup(null).getAttribute('role'),
      'the row grows with its content, so the strip fits; measuring the 0 px wrapper folded it',
    ).toBe('radiogroup')
  })

  it('still folds where the row is capped below it', () => {
    expect(setup(100).getAttribute('role'), 'a 300 px strip in a 100 px row folds to the pill').not.toBe('radiogroup')
  })
})
