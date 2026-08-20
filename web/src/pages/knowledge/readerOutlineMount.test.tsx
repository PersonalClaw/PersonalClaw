import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { ReadingView } from './ReadingView'
import { parseOutline } from './readingOutline'
import type { KnowledgeItem } from '../../lib/api'

// ── The outline, mounted in the reader (KL-16) ─────────────────────────────────────────
//
// `readingOutline`/`DocumentOutline` own the parse and the panel. What is asserted here is
// the half the READER owns, which is the half that cannot be tested from either of them:
//
//   • THE ENTRY → ELEMENT MAPPING. An `OutlineEntry.offset` is a character index into the
//     markdown SOURCE and deliberately not a DOM coordinate, so the only correspondence that
//     survives rendering is DOCUMENT ORDER — the nth entry is the nth `h1`–`h6` in the
//     article. That is an assumption about the renderer, and this file drives both sides of
//     it: the order case, and the case where the counts disagree.
//   • THE RECT-BASED SCROLL SPY. `activeOffset` is decided out here. jsdom computes no
//     layout, so every rect involved is stubbed — which is the honest way to test a spy whose
//     entire input is `getBoundingClientRect`, and is why the fixture states the geometry
//     explicitly rather than pretending to scroll a real document.
//
// 🪤 THE DEGRADE IS THE REASON THE MAPPING NEEDS A TEST AT ALL. `parseOutline` skips setext
// headings, headings inside a blockquote or list item, and raw HTML `<h2>` — all of which DO
// render as heading elements. On such a body the article has MORE heading nodes than entries
// and every index after the extra one slips by one, so a mapping that trusted the index would
// scroll the reader confidently into the wrong section. The `RAW_HTML_HEADING` fixture below
// is that real case (rehype-raw passes the tag through `ui/Markdown`), not a contrived one.

const SECTIONED = [
  '# Widgets, considered',
  '',
  'Opening paragraph about widgets, with enough words to be a paragraph.',
  '',
  '## How widgets are made',
  '',
  'The middle section.',
  '',
  '## How widgets are sold',
  '',
  'The last section.',
].join('\n')

/** The same body with one heading the parser refuses and the renderer emits — the off-by-one
 *  the degrade exists for. */
const RAW_HTML_HEADING = SECTIONED.replace(
  '## How widgets are sold',
  '<h2>How widgets are sold</h2>',
)

function item(content: string): KnowledgeItem {
  return {
    id: 'k1',
    // Equal to the body's first heading on purpose: the reader SUPPRESSES its own <h2> item
    // title in that case, so every heading element inside the scroller belongs to the
    // article. Asserted below rather than assumed — if it stopped being suppressed, this
    // file's index arithmetic would be measuring the wrong nodes.
    title: 'Widgets, considered',
    content,
    item_type: 'note',
    word_count: 400,
  } as KnowledgeItem
}

function renderReader(content: string) {
  return render(
    <ReadingView item={item(content)} annotations={[]} onAnnotationsChanged={() => {}} />,
  )
}

const scroller = () => screen.getByRole('group', { name: 'Article body' })
const headings = () => Array.from(scroller().querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6'))
const outlineRows = () => screen.getAllByRole('button', { pressed: false }).concat(screen.queryAllByRole('button', { pressed: true }))
const pressedRow = () => document.querySelector('[aria-pressed="true"]')

/** The scroller's box and each heading's top, in the one place jsdom will never supply them.
 *  `top: 0, height: 400` puts the reading line at 100 (a quarter down), so a heading counts as
 *  current once its top is at or above 100. */
function stubGeometry(tops: number[]) {
  const hs = headings()
  expect(hs.length, 'the fixture must render one node per stubbed top').toBe(tops.length)
  scroller().getBoundingClientRect = () => ({ top: 0, height: 400, bottom: 400, left: 0, right: 0, width: 700, x: 0, y: 0, toJSON: () => ({}) })
  hs.forEach((h, i) => {
    h.getBoundingClientRect = () => ({ top: tops[i], height: 24, bottom: tops[i] + 24, left: 0, right: 0, width: 600, x: 0, y: tops[i], toJSON: () => ({}) })
  })
  return hs
}

/** Let the rAF-coalesced spy run. The handler is deliberately not synchronous with the event
 *  — one layout read per heading per scroll event would force a reflow dozens of times a
 *  frame — so the assertion has to wait for the frame it batches into. */
async function scrollTick() {
  await act(async () => {
    scroller().dispatchEvent(new Event('scroll'))
    await new Promise((r) => requestAnimationFrame(() => r(null)))
  })
}

afterEach(() => { vi.restoreAllMocks() })

describe('the outline is mounted in the reader', () => {
  it('renders a row per parsed heading, and the reader is not printing a second title', () => {
    renderReader(SECTIONED)
    const entries = parseOutline(SECTIONED)
    expect(entries.length, 'the fixture has to have sections at all').toBe(3)
    // The precondition this whole file's index arithmetic rests on.
    expect(headings().length, 'no extra item-title heading inside the scroller').toBe(entries.length)
    for (const e of entries) expect(screen.getByRole('button', { name: e.text })).toBeInTheDocument()
  })
})

describe('the active row tracks the scroll position, by rect', () => {
  it('names the section whose heading last passed the reading line', async () => {
    renderReader(SECTIONED)

    // Only the h1 is above the line: the reader is in the opening section.
    stubGeometry([50, 300, 600])
    await scrollTick()
    expect(pressedRow()?.textContent).toBe('Widgets, considered')

    // Scrolled on: the h1 is off the top, the second heading has crossed the line, the
    // third has not. The LAST one to have passed is the current one — not the first, which
    // is the off-by-one a "first heading above the line" reading would make.
    stubGeometry([-200, 50, 600])
    await scrollTick()
    expect(pressedRow()?.textContent).toBe('How widgets are made')

    stubGeometry([-500, -200, 40])
    await scrollTick()
    expect(pressedRow()?.textContent).toBe('How widgets are sold')
  })

  it('marks no row while the reader is still above the first heading', async () => {
    renderReader(SECTIONED)
    // Every heading below the line — the preamble. "None" is a real answer here, and
    // defaulting to the first row would claim the reader is somewhere they are not.
    stubGeometry([500, 700, 900])
    await scrollTick()
    expect(pressedRow(), 'no section is current in the preamble').toBeNull()
    // Vacuity guard: the rows exist, so "nothing pressed" is a measurement and not an
    // absent outline.
    expect(outlineRows().length).toBeGreaterThanOrEqual(3)
  })
})

describe('selecting a row scrolls the article to that section', () => {
  it('scrolls the heading the row NAMES, by document order', async () => {
    renderReader(SECTIONED)
    const spies = headings().map((h) => {
      const fn = vi.fn()
      ;(h as unknown as { scrollIntoView: unknown }).scrollIntoView = fn
      return fn
    })

    fireEvent.click(screen.getByRole('button', { name: 'How widgets are made' }))
    expect(spies[1], 'the second entry maps to the second heading node').toHaveBeenCalled()
    expect(spies[0], 'and to no other').not.toHaveBeenCalled()
    expect(spies[2]).not.toHaveBeenCalled()
    // `block: 'start'` — a section heading belongs at the top of the reading column, not
    // centred, which would leave the previous section's tail above it. And the jump SWEEPS
    // by default, which is what makes it read as movement through one document rather than
    // a cut to a different one.
    expect(spies[1].mock.calls[0][0]).toMatchObject({ block: 'start', behavior: 'smooth' })
  })

  it('jumps without sweeping under prefers-reduced-motion', () => {
    // A smooth scroll IS motion, and a whole article sliding past is the largest sweep on
    // this surface. `design/motion`'s `prefersReducedMotion` is read per call (no cached
    // singleton), so unlike framer-motion's it can be stubbed here rather than needing its
    // own file — which is the only reason this assertion is not in
    // `readingViewReducedMotion.test.tsx` beside the progress arc's.
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('prefers-reduced-motion'), media: q, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }))
    renderReader(SECTIONED)
    const spy = vi.fn()
    ;(headings()[1] as unknown as { scrollIntoView: unknown }).scrollIntoView = spy

    fireEvent.click(screen.getByRole('button', { name: 'How widgets are made' }))
    // It must still ARRIVE — a jump that was suppressed entirely would also show no sweep,
    // and that is a different bug wearing this one's clothes.
    expect(spy, 'reduced motion means instant, not absent').toHaveBeenCalled()
    expect(spy.mock.calls[0][0]).toMatchObject({ block: 'start', behavior: 'auto' })
  })
})

describe('a heading-count mismatch degrades to a no-op', () => {
  it('does not scroll to the wrong section when the renderer emitted an extra heading', () => {
    renderReader(RAW_HTML_HEADING)
    const entries = parseOutline(RAW_HTML_HEADING)
    const hs = headings()
    // The premise of the test, measured rather than asserted in prose: the parser saw one
    // fewer heading than the renderer produced. If this ever stops being true the test below
    // would be proving nothing, so it fails here instead.
    expect(entries.length, 'parseOutline skips the raw <h2>').toBe(2)
    expect(hs.length, 'the renderer emits it (rehype-raw)').toBe(3)

    const spies = hs.map((h) => {
      const fn = vi.fn()
      ;(h as unknown as { scrollIntoView: unknown }).scrollIntoView = fn
      return fn
    })
    // The rows are still offered — the outline is a real, useful list of what the parser
    // found, and hiding it would be a bigger loss than a dead click.
    const row = screen.getByRole('button', { name: 'How widgets are made' })
    fireEvent.click(row)
    // …but the jump is declined. Index 1 in `entries` is NOT heading node 1 here (the extra
    // node is node 2, so this particular row would still be right — and that is exactly why
    // a per-row guess is unsafe: the mapping either holds for the document or it does not).
    for (const [i, s] of spies.entries()) {
      expect(s, `heading ${i} must not be scrolled to on a mismatched document`).not.toHaveBeenCalled()
    }
  })

  it('marks no active row on a mismatched document', async () => {
    renderReader(RAW_HTML_HEADING)
    // Geometry that WOULD select a row on a matching document (the earlier case proves it).
    stubGeometry([-200, 50, 600])
    await scrollTick()
    expect(pressedRow(), 'a wrong highlight is worse than no highlight').toBeNull()
    expect(outlineRows().length, 'and the rows are still there — not an empty outline').toBeGreaterThanOrEqual(2)
  })
})
