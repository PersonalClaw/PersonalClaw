// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { slotWidths } from './HeaderActions'

// ── A title's part it cannot shed counts in its slot's minimum ───────────────────────────────────
//
// A project's Tasks heading is a breadcrumb, "Garden › Tasks", whose "› Tasks" does not shrink.
// The bar clips every `[data-type]` descendant of its title slot, so that part added nothing to
// the slot's shrunk width, the cluster took its room, and at 390 px the heading read a lone "›"
// with the project link 0 px wide. A part marked `data-title-keep` counts in the minimum now: what
// its heading clips (the part with the gap before it) is added to the slot's own shrunk width,
// which holds the heading's clipped box and none of its overflow. The slot's own width alone is
// `hard`, what would overlap the row if squeezed.

afterEach(() => { vi.restoreAllMocks() })

/** A slot whose own shrunk width is *slotW* px, its heading clipping *overflow* px of kept text. */
function slot(slotW: number, overflow: number, clips = true): HTMLElement {
  const el = document.createElement('div')
  el.innerHTML = `<h1 data-type="title-l" style="overflow-x:${clips ? 'hidden' : 'visible'}"><a>Garden</a> <span data-title-keep>› Tasks</span></h1>`
  document.body.append(el)
  const heading = el.querySelector('h1')!
  vi.spyOn(Element.prototype, 'scrollWidth', 'get').mockImplementation(function (this: Element) {
    if (this === el) return slotW
    return this === heading ? overflow : 0
  })
  vi.spyOn(Element.prototype, 'clientWidth', 'get').mockImplementation(() => 0)
  return el
}

describe("a title slot's minimum", () => {
  it('holds what the heading clips of the part it cannot shed', () => {
    expect(slotWidths(slot(0, 66))).toMatchObject({ min: 66, hard: 0 })
  })

  it("adds it to the slot's own shrunk width, which holds none of it", () => {
    expect(slotWidths(slot(30, 66))).toMatchObject({ min: 96, hard: 30 })
  })

  it("is the slot's own width where the heading does not clip, its overflow counted there", () => {
    expect(slotWidths(slot(96, 66, false))).toMatchObject({ min: 96, hard: 96 })
  })
})
