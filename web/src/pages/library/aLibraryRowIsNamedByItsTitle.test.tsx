/** A library row is named by its title: each row's name starts with its
 *  visible title; group headings are list items holding an `h2`; `aria-setsize` counts rows only; and
 *  a state mark is never cut. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { row } from '../../test/libraryHarness'
import { LibraryList } from './LibraryList'
import { LibraryRow } from '../../ui/LibraryRow'

afterEach(cleanup)

const NOW = Date.parse('2026-11-14T12:00:00Z')
const ROWS = [
  row('knowledge:k1', 'Router manual', { pinned: true }),
  row('artifact:lisbon', 'Lisbon trip plan', { activity_at: '2026-11-14T10:00:00Z' }),
  row('report:rpt-1a2b3c4d', 'Weekly perf digest', { activity_at: '2026-11-01T10:00:00Z' }),
]

function list() {
  return render(
    <LibraryList rows={ROWS} sort="activity" searching={false} open={new Set()} onShowAll={vi.fn()} selected="" ctx={{}}
      onOpen={vi.fn()} onFilterProject={vi.fn()} menuOf={() => []} bulk={false} checked={new Set()} onCheck={vi.fn()} now={NOW} />,
  )
}

describe('a library row is named by its title', () => {
  it('🔑 each row\'s name starts with the title it shows', () => {
    list()
    for (const r of ROWS) {
      const named = screen.getAllByRole('button').filter((b) => (b.getAttribute('aria-label') ?? '').startsWith(r.title))
      expect(named, r.title).toHaveLength(1)
    }
  })

  it('🔑 group headings are h2s inside list items, and aria-setsize counts the rows only', () => {
    list()
    const headings = screen.getAllByRole('heading', { level: 2 })
    expect(headings.map((h) => h.textContent)).toContain('Pinned')
    for (const h of headings) expect(h.closest('[role="listitem"], li'), `${h.textContent} sits in a list item`).not.toBeNull()
    const sized = [...document.querySelectorAll('[aria-setsize]')]
    expect(sized.length).toBe(ROWS.length)
    for (const el of sized) expect(el.getAttribute('aria-setsize')).toBe(String(ROWS.length))
    for (const h of headings) expect(h.closest('[aria-setsize]'), 'a heading carries no position').toBeNull()
  })

  it('🔑 a state mark is never cut: it gives way last', () => {
    render(<LibraryRow item={row('knowledge:k2', 'A very long title that runs on and on past the row', { state: 'unreadable' })} onOpen={vi.fn()} now={NOW} />)
    const mark = screen.getByText("Couldn't read it")
    expect(mark.className).toContain('shrink-0')
    expect(mark.className).not.toContain('truncate')
  })
})
