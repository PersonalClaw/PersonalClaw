// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { slotWidths } from './HeaderActions'

// ── A title's part it cannot shed counts in its slot's minimum ───────────────────────────────────
//
// A project's Tasks heading is a breadcrumb, "Garden › Tasks", whose "› Tasks" does not shrink.
// The bar clips every `[data-type]` descendant of its title slot, so that part added nothing to
// the slot's shrunk width, the cluster took its room, and at 390 px the heading read a lone "›"
// with the project link 0 px wide. A part marked `data-title-keep` counts in the minimum now.

afterEach(() => { vi.restoreAllMocks() })

/** A slot whose own shrunk width is *slot* px, holding a kept part *kept* px wide. */
function slot(slotW: number, keptW: number): HTMLElement {
  const el = document.createElement('div')
  el.innerHTML = '<h1 data-type="title-l"><a>Garden</a> <span data-title-keep>› Tasks</span></h1>'
  vi.spyOn(Element.prototype, 'scrollWidth', 'get').mockImplementation(function (this: Element) {
    if (this === el) return slotW
    return this.hasAttribute('data-title-keep') ? keptW : 0
  })
  return el
}

describe("a title slot's minimum", () => {
  it('holds the part the title cannot shed, though the heading clips it', () => {
    expect(slotWidths(slot(0, 62)).min).toBe(62)
  })

  it('is the slot\'s own shrunk width where that is wider', () => {
    expect(slotWidths(slot(90, 62)).min).toBe(90)
  })
})
