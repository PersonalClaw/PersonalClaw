import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { measureWorkShare } from '../../e2e/workShare'

// ── The work surface's share counts the status line ─────────────────────────────────────────────
//
// `web/REDESIGN.md` §4: the work surface takes ~90% of the window in focus mode, and
// `e2e/focusAndReadingModes.spec.ts` measures it with `measureWorkShare`. The status line (#3823)
// is 24 px of chrome along the window's bottom edge, outside the work surface, so while it shows
// the share is what it leaves. Focus mode hides it while the gateway answers, and then the work
// reaches the window's bottom.
//
// jsdom lays nothing out, so each region's box is planted: a 1280 × 900 window in focus mode (no
// rail), the corner band's bottom at 52 px.

const VW = 1280
const VH = 900
const CORNERS = 52
const LINE = 24

type Rect = { x: number; y: number; w: number; h: number }
const rects = new Map<Element, Rect>()
const place = (el: Element, r: Rect) => { rects.set(el, r); return el }

function shell({ line, composer }: { line: boolean; composer?: number }) {
  const bottom = line ? VH - LINE : VH
  const root = document.createElement('div')
  const main = place(document.createElement('main'), { x: 0, y: 0, w: VW, h: bottom })
  const corners = place(document.createElement('div'), { x: 0, y: 0, w: VW, h: CORNERS })
  corners.className = 'absolute top-0'
  main.append(corners)
  if (composer) {
    const stage = place(document.createElement('div'), { x: 0, y: bottom - composer, w: VW, h: composer })
    stage.setAttribute('data-composer-stage', '')
    main.append(stage)
  }
  root.append(main)
  if (line) {
    const footer = place(document.createElement('footer'), { x: 0, y: VH - LINE, w: VW, h: LINE })
    footer.setAttribute('aria-label', 'Status')
    root.append(footer)
  }
  document.body.append(root)
}

const share = (h: number) => Math.round((VW * h) / (VW * VH) * 1000) / 10
// jsdom has no `checkVisibility`; a planted box is what is visible.
const ownCheckVisibility = Object.getOwnPropertyDescriptor(Element.prototype, 'checkVisibility')

beforeEach(() => {
  vi.stubGlobal('innerWidth', VW)
  vi.stubGlobal('innerHeight', VH)
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const r = rects.get(this) ?? { x: 0, y: 0, w: 0, h: 0 }
    return { x: r.x, y: r.y, left: r.x, top: r.y, width: r.w, height: r.h, right: r.x + r.w, bottom: r.y + r.h, toJSON: () => r } as DOMRect
  })
  Object.defineProperty(Element.prototype, 'checkVisibility', {
    configurable: true,
    value(this: Element) { return rects.has(this) },
  })
})
afterEach(() => {
  document.body.replaceChildren()
  rects.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  if (ownCheckVisibility) Object.defineProperty(Element.prototype, 'checkVisibility', ownCheckVisibility)
  else delete (Element.prototype as { checkVisibility?: unknown }).checkVisibility
})

describe('the work surface’s share of the window', () => {
  it('🔴 leaves out the status line while it shows', () => {
    shell({ line: true })
    expect(measureWorkShare()).toBe(share(VH - CORNERS - LINE))
  })

  it('🔴 leaves out the status line and the composer, each once', () => {
    shell({ line: true, composer: 120 })
    expect(measureWorkShare()).toBe(share(VH - CORNERS - LINE - 120))
  })

  it('reaches the window’s bottom with no status line, as in focus mode while the gateway answers', () => {
    shell({ line: false })
    expect(measureWorkShare()).toBe(share(VH - CORNERS))
  })
})
