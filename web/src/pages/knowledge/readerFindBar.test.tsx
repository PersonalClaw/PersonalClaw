import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReadingView, articleBlocks } from './ReadingView'
import type { KnowledgeItem } from '../../lib/api'

// ── The find bar, mounted in the reader (KL-16) ────────────────────────────────────────
//
// `ui/FindBar` is surface-agnostic and has its own tests. What is asserted here is that the
// READER hands it the right surface, which is the one thing neither side can check alone:
//
//   • IT SEARCHES THE ARTICLE, not a transcript. The bar was born over `ChatTurn[]`; the
//     assertion that it is now reading this body is the COUNT — a query in 3 of 6 blocks has
//     to report 3.
//   • THE COUNT IS PER BLOCK. `ui/Markdown` wraps its whole output in one `<div>`, so a host
//     that walked the article ref's own children would hand the bar ONE item containing the
//     entire body and report "1/1" for every query — a bar that looks completely functional
//     and whose ↑/↓ can never move. That is the specific miss this file is built around, and
//     the fixture's counts are chosen so 1 and 6 are both wrong answers.
//   • ↑/↓ REACH THE ARTICLE'S OWN NODES, via the `nodeOf` getter.
//
// 🪤 The bar debounces its scan 150ms, so every count assertion waits rather than reading
// straight after the keystroke. A synchronous read here would measure the empty query.

const ARTICLE = [
  '# Widgets, considered',            // block 0 — "widget"
  '',
  'An opening paragraph with no keyword in it at all.',   // block 1
  '',
  '## How widgets are made',          // block 2 — "widget"
  '',
  'A paragraph about manufacture, mentioning widgets once.', // block 3 — "widget"
  '',
  '## Afterword',                     // block 4
  '',
  'Nothing relevant here either.',    // block 5
].join('\n')

/** The blocks containing "widget", counted from the fixture rather than hard-coded twice. */
const EXPECTED_MATCHES = 3

function item(): KnowledgeItem {
  return {
    id: 'k1',
    // Equal to the first heading, so the reader suppresses its own title <h2> and every
    // block in the scroller belongs to the article.
    title: 'Widgets, considered',
    content: ARTICLE,
    item_type: 'note',
    word_count: 400,
  } as KnowledgeItem
}

function renderReader() {
  return render(<ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />)
}

const scroller = () => screen.getByRole('group', { name: 'Article body' })
const openFind = () => fireEvent.click(screen.getByRole('button', { name: 'Find' }))
const field = () => screen.getByLabelText('Find in article')
const counter = () => screen.getByText(/^(Match \d+ of \d+|No matches)$/)

/** Type a query and wait for the counter to say `expected`.
 *
 *  🪤 Waiting for the counter to merely EXIST is not enough: it is already on screen holding
 *  the PREVIOUS query's answer, so a second search resolves instantly against stale text and
 *  the assertion after it reads a value the keystroke never produced. Cost one false red here
 *  before the wait was keyed on the value. */
async function search(q: string, expected: string) {
  fireEvent.change(field(), { target: { value: q } })
  // Past the bar's 150ms debounce, and past the previous answer.
  await waitFor(() => expect(counter()).toHaveTextContent(expected), { timeout: 2000 })
}

afterEach(() => { vi.restoreAllMocks() })

describe('the reader hosts a find bar over its own article', () => {
  it('is not mounted until asked, and the control announces that it reveals it', () => {
    renderReader()
    expect(screen.queryByLabelText('Find in article'), 'no bar before it is opened').toBeNull()
    const control = screen.getByRole('button', { name: 'Find' })
    expect(control, 'a control that reveals content says so').toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(control)
    expect(field()).toBeInTheDocument()
    expect(control).toHaveAttribute('aria-expanded', 'true')
  })

  it('is named for THIS surface, not for the one it was born on', () => {
    renderReader()
    openFind()
    // The bar's label is a required prop precisely so no surface inherits chat's wording.
    expect(field()).toBeInTheDocument()
    expect(screen.queryByLabelText('Find in conversation')).toBeNull()
  })

  it('opens on Cmd/Ctrl+F, the binding a reader already has for this', async () => {
    renderReader()
    fireEvent.keyDown(window, { key: 'f', metaKey: true })
    expect(field(), 'the reader owns ⌘F while it is open').toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Find' })).toHaveAttribute('aria-expanded', 'true')

    // A second press closes it and falls through to the browser's own find. 🪤 The state
    // flips synchronously but the NODE does not: `AnimatePresence` keeps the bar mounted
    // through its exit transition, so asserting its absence straight after the keystroke
    // measures the animation rather than the toggle.
    fireEvent.keyDown(window, { key: 'f', metaKey: true })
    expect(screen.getByRole('button', { name: 'Find' })).toHaveAttribute('aria-expanded', 'false')
    await waitFor(() => expect(screen.queryByLabelText('Find in article')).toBeNull())
  })

  it('counts the ARTICLE\'S BLOCKS — not the whole body as one, and not every block', async () => {
    renderReader()
    // The premise, measured: the reader really did hand over per-block units. If this were
    // 1 the count below would still "work" and the arrows would be dead.
    const blocks = articleBlocks(scroller().querySelector('.reading')!.parentElement!)
    expect(blocks.length, 'the fixture renders six blocks').toBe(6)

    openFind()
    // `Match 1 of 3` — 3 of the six blocks contain it. Both 1 (the whole body as one item)
    // and 6 (every block counted) would be wrong, and this is the assertion that says so.
    await search('widget', `Match 1 of ${EXPECTED_MATCHES}`)
  })

  it('says so when the article does not contain the query', async () => {
    renderReader()
    openFind()
    await search('quatloos', 'No matches')
    // Vacuity guard: the same bar DOES find something, so "No matches" is a measurement of
    // this query and not of a bar wired to nothing.
    await search('widget', `Match 1 of ${EXPECTED_MATCHES}`)
  })
})

describe('cycling matches moves the article', () => {
  it('scrolls the article\'s own block for the match it lands on', async () => {
    renderReader()
    const article = scroller().querySelector('.reading')!.parentElement!
    const spies = articleBlocks(article).map((el) => {
      const fn = vi.fn()
      ;(el as unknown as { scrollIntoView: unknown }).scrollIntoView = fn
      return fn
    })

    openFind()
    await search('widget', `Match 1 of ${EXPECTED_MATCHES}`)
    // ↓ from match 1 lands on match 2, which is the THIRD block of the article (index 2 —
    // the `## How widgets are made` heading). The mapping from "match 2" to "block 2" is
    // the reader's `nodeOf`, and getting it wrong scrolls to a paragraph with no match in
    // it, which looks like the bar simply not working.
    fireEvent.keyDown(field(), { key: 'ArrowDown' })
    await waitFor(() => expect(spies[2], 'the second matching block').toHaveBeenCalled())
    for (const i of [1, 4, 5]) {
      expect(spies[i], `block ${i} has no match and must not be scrolled to`).not.toHaveBeenCalled()
    }
  })
})
