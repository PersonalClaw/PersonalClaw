import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createRef } from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, waitFor, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FindBar } from './FindBar'

// #546 regression cover for the PAINTER half of find-in-<surface>. The matcher is
// covered in findText.test.ts; this drives the real component so the paint loop's own
// offset math (and its defensive clamp) is exercised, not a copy of it.
//
// Moved here from pages/chat with the promotion (KL-16), and re-driven over PLAIN
// STRINGS rather than ChatTurns — if the bar still needed chat's shapes to be tested,
// it would still need them to be mounted.
//
// jsdom ships neither CSS.highlights nor Highlight, so we stub the minimum the
// component feature-detects: a Map to write into and a constructor that records
// the Ranges it was handed. The Ranges themselves are real jsdom Ranges, so an
// out-of-bounds offset throws a real DOMException — which is the bug's mechanism.

interface Win { CSS?: { highlights?: Map<string, unknown> }; Highlight?: new (...r: Range[]) => unknown }

class FakeHighlight {
  ranges: Range[]
  constructor(...ranges: Range[]) { this.ranges = ranges }
}

function installHighlightApi() {
  const w = window as unknown as Win
  const highlights = new Map<string, unknown>()
  w.CSS = { ...(w.CSS ?? {}), highlights }
  w.Highlight = FakeHighlight as unknown as new (...r: Range[]) => unknown
  return highlights
}

function uninstallHighlightApi() {
  const w = window as unknown as Win
  delete w.CSS
  delete w.Highlight
}

/** One searchable string per item — the simplest possible host. */
const oneSegment = (s: string) => [s]

/** Render the bar over a scroll container holding `text`, type `query`, and return
 *  the strings the painter actually highlighted. */
async function paintedTexts(text: string, query: string): Promise<string[]> {
  const highlights = installHighlightApi()
  const host = document.createElement('div')
  host.textContent = text
  document.body.appendChild(host)
  const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
  scrollRef.current = host

  // Scope queries to this render's own container — each call adds another bar to
  // document.body (RTL only cleans up between tests, not within one).
  const { container } = render(
    <FindBar items={[text]} segmentsOf={oneSegment} nodeOf={() => null}
      scrollRef={scrollRef} label="Find in page" onClose={() => {}} />,
  )
  const input = within(container).getByLabelText('Find in page')
  // Set the value directly: enough for the controlled field, and keeps this test
  // independent of user-event's per-keystroke timing.
  fireEvent.change(input, { target: { value: query } })

  let painted: string[] = []
  await waitFor(() => {
    const h = highlights.get('pc-find') as FakeHighlight | undefined
    expect(h).toBeDefined()
    painted = (h as FakeHighlight).ranges.map((r) => r.toString())
  }, { timeout: 2000 })
  host.remove()
  return painted
}

describe('FindBar painter (#546)', () => {
  beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}) })
  afterEach(() => { uninstallHighlightApi(); vi.restoreAllMocks() })

  it('highlights the right characters when İ (U+0130) precedes the match', async () => {
    // The folded copy is longer, so the old math painted "et" (or threw). One İ is
    // enough to push the end offset past the node length.
    expect(await paintedTexts('İİİİtarget', 'target')).toEqual(['target'])
    expect(await paintedTexts('İtarget', 'target')).toEqual(['target'])
  })

  it('paints every occurrence instead of blanking the page on one bad node', async () => {
    // Previously the first out-of-range setEnd threw and aborted the whole walk,
    // so NOTHING on the page highlighted. Now later matches still paint.
    expect(await paintedTexts('İtarget and target again', 'target')).toEqual(['target', 'target'])
  })

  it('paints the correct span mid-text (the silent off-by-one case)', async () => {
    expect(await paintedTexts('İzmir kiln target 1240C', 'target')).toEqual(['target'])
  })

  it('still paints plain ASCII matches case-insensitively', async () => {
    expect(await paintedTexts('Docker Compose and docker again', 'DOCKER')).toEqual(['Docker', 'docker'])
  })
})

// ── The promotion itself: this primitive does not know what it is searching ────────
//
// The bar shipped for a year as `pages/chat/FindBar.tsx` over `ChatTurn[]` and a
// `Map<turnIndex, HTMLDivElement>`. KL-16 mounts it in the knowledge reader too, so
// "works when driven by something that is not chat" is the contract, and these drive it
// with article-shaped rows: no ChatTurn, no chat import, nothing from pages/.

describe('FindBar is surface-agnostic', () => {
  const SRC = join(process.cwd(), 'src')
  const src = readFileSync(join(SRC, 'ui/FindBar.tsx'), 'utf8')
  const PAGES_IMPORT = /^\s*import[^\n]*from\s*'[^']*\bpages\//m

  it('imports nothing from pages/ — a shared primitive cannot depend on one caller', () => {
    expect(PAGES_IMPORT.test(src), 'ui/FindBar.tsx must not import from pages/').toBe(false)
  })

  it('the import rail is not vacuous — it sees this file\'s real imports, and would flag one', () => {
    // Without this, a regex that matches nothing (a renamed directory, a `.tsx` that stopped
    // being read) reports a clean layering forever.
    expect(src, 'the file under test must actually have imports').toMatch(
      /^import \{ findInText, matchingIndices \} from '\.\/findText'$/m)
    expect(PAGES_IMPORT.test("import { ChatTurn } from '../pages/chat/chatTypes'"), 'positive control')
      .toBe(true)
    expect(PAGES_IMPORT.test("import { spring } from '../design/motion'"), 'negative control').toBe(false)
  })

  it('counts and cycles ITEMS the host defines, and scrolls the one it is on', async () => {
    const rows = [
      { heading: 'Kiln schedule', body: 'cone 6 target 1240C' },
      { heading: 'Glaze notes', body: 'nothing to see' },
      { heading: 'Target list', body: 'and another target' },
    ]
    // Real nodes, so scrollIntoView is observed on the element the bar chose.
    const nodes = rows.map(() => {
      const el = document.createElement('div')
      el.scrollIntoView = vi.fn()
      return el
    })
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    const { container } = render(
      <FindBar items={rows} segmentsOf={(r) => [r.heading, r.body]} nodeOf={(_r, i) => nodes[i]}
        scrollRef={scrollRef} label="Find in article" onClose={() => {}} />,
    )
    const input = within(container).getByLabelText('Find in article')
    fireEvent.change(input, { target: { value: 'target' } })

    // Rows 0 and 2 match; row 1 does not. The counter counts stops, not occurrences —
    // row 2 holds two "target"s and is still one stop.
    await waitFor(() => expect(within(container).getByText('1/2')).toBeTruthy())

    await userEvent.click(within(container).getByLabelText('Next match'))
    expect(within(container).getByText('2/2')).toBeTruthy()
    expect(nodes[2].scrollIntoView, 'the second stop is row 2, not row 1').toHaveBeenCalled()
    expect(nodes[1].scrollIntoView, 'row 1 matches nothing and is never a stop').not.toHaveBeenCalled()

    // …and it wraps back to the first stop rather than dead-ending.
    await userEvent.click(within(container).getByLabelText('Next match'))
    expect(within(container).getByText('1/2')).toBeTruthy()
    expect(nodes[0].scrollIntoView).toHaveBeenCalled()
  })

  it('says "No matches" through the host\'s own vocabulary, with nothing chat-shaped mounted', async () => {
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    const { container } = render(
      <FindBar items={['a paragraph of prose']} segmentsOf={oneSegment} nodeOf={() => null}
        scrollRef={scrollRef} label="Find in article" onClose={() => {}} />,
    )
    // The label is the host's, not "Find in conversation" — the old hard-coded string.
    expect(within(container).queryByLabelText('Find in conversation')).toBeNull()
    const input = within(container).getByLabelText('Find in article')
    expect(input.getAttribute('placeholder')).toBe('Find in article')

    fireEvent.change(input, { target: { value: 'zebra' } })
    await waitFor(() => expect(within(container).getByRole('status').textContent).toBe('No matches'))
    // Positive control for the same region, so an always-"No matches" bar fails here.
    fireEvent.change(input, { target: { value: 'prose' } })
    await waitFor(() => expect(within(container).getByRole('status').textContent).toBe('Match 1 of 1'))
  })
})
