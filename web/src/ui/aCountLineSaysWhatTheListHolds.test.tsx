import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ListStatus } from './ListControls'

// ── A list's count line says what the list holds ─────────────────────────────────────────────────
//
// The Projects list draws "6 projects" under its title, and while you search, "2 projects and
// 4 chats match". `ListStatus` drew nothing visible while the rows were fresh, so the line is an
// opt-in `line`: visible text and no control, every control being in the header. While the list is
// narrowed its sr-only announcement says the line's own words, so a screen reader hears what a
// sighted reader sees; at rest the announcement stays empty, as before, and a page that passes no
// `line` draws what it always drew.

describe("a list's count line", () => {
  it('draws its text, visible, and holds no control', () => {
    const { container } = render(<ListStatus results={{ count: 6, noun: 'projects', active: false }} line="6 projects" />)
    const line = container.querySelector('[data-list-line]')
    expect(line?.textContent).toBe('6 projects')
    expect(line?.closest('.sr-only')).toBeNull()
    expect(container.querySelectorAll('button, a, input, [role="button"]')).toHaveLength(0)
    expect(screen.getByRole('status').textContent, 'at rest the announcement stays empty').toBe('')
  })

  it('without a line draws nothing visible while the rows are fresh, as before', () => {
    const { container } = render(<ListStatus results={{ count: 6, noun: 'projects', active: false }} />)
    expect(container.querySelector('[data-list-line]')).toBeNull()
    for (const el of container.querySelectorAll('*')) expect(el.closest('.sr-only')).not.toBeNull()
  })

  it("while the list is narrowed, the announcement says the line's own words", () => {
    render(<ListStatus results={{ count: 6, noun: 'rows', active: true }} line="2 projects and 4 chats match" />)
    const status = screen.getByRole('status')
    expect(status.className).toContain('sr-only')
    expect(status.textContent).toBe('2 projects and 4 chats match')
  })

  it('without a line, a narrowed list still announces its count', () => {
    render(<ListStatus results={{ count: 2, noun: 'projects', active: true }} />)
    expect(screen.getByRole('status').textContent).toBe('2 projects')
  })

  // ── The line's `lead` and its `actions` ──────────────────────────────────
  //
  // The library's frames draw what a search or filter left first, in the label ink ("23 matches",
  // "34 items in Homelab", "Trips"), then the line, then the actions that act only on what the line
  // names (Clear, Open project, Rename). A space follows a part that ends in a full stop and " · "
  // any other; the line wraps between its parts, never inside one.

  const parts = (c: HTMLElement) => [...c.querySelectorAll('[data-list-line] [data-line-part]')].map((el) => el.textContent)

  it('draws the lead first, in the label ink at weight 540, then the line', () => {
    const { container } = render(<ListStatus results={{ count: 23, noun: 'matches', active: true }} lead="23 matches" line="Matching words only." />)
    const lead = container.querySelector('[data-list-line] [data-line-part="lead"]') as HTMLElement
    expect(lead.className).toContain('text-on-surface-var')
    expect(lead.style.fontWeight, 'weight 540').toBe('540')
    expect(parts(container)).toEqual(['23 matches · ', 'Matching words only.'])
  })

  it('may be its lead alone', () => {
    const { container } = render(<ListStatus results={{ count: 3, noun: 'items', active: true }} lead="Trips" />)
    expect(container.querySelector('[data-list-line]')?.textContent).toBe('Trips')
  })

  it('puts a space after a part that ends in a full stop, and " · " after any other', () => {
    const select = vi.fn()
    const { container } = render(
      <ListStatus results={{ count: 100, noun: 'matches', active: true }} lead="Showing the best 100 matches." line="Add a word or a filter to narrow it."
        actions={[{ label: 'Clear', onSelect: select }]} />,
    )
    expect(container.querySelector('[data-list-line]')?.textContent).toBe('Showing the best 100 matches. Add a word or a filter to narrow it. Clear')
    const { container: c2 } = render(
      <ListStatus results={{ count: 34, noun: 'items', active: true }} lead="Trips" line="34 items"
        actions={[{ label: 'Rename', onSelect: select }, { label: 'Delete collection', onSelect: select }, { label: 'Clear', onSelect: select }]} />,
    )
    expect(c2.querySelector('[data-list-line]')?.textContent).toBe('Trips · 34 items · Rename · Delete collection · Clear')
  })

  it('wraps between its parts, never inside one', () => {
    const { container } = render(<ListStatus results={{ count: 34, noun: 'items', active: true }} lead="Trips" line="34 items" actions={[{ label: 'Clear', onSelect: () => {} }]} />)
    const els = [...container.querySelectorAll('[data-list-line] [data-line-part]')] as HTMLElement[]
    expect(els.length).toBe(3)
    for (const el of els) expect(el.className, `${el.textContent} is one unit`).toContain('inline-block')
  })

  it("draws each action as a 44 px box at 320 to 768 px, and acts on press", () => {
    const clear = vi.fn()
    render(<ListStatus results={{ count: 34, noun: 'items', active: true }} lead="34 items in Homelab" actions={[{ label: 'Clear', onSelect: clear }, { label: 'Open project', onSelect: () => {} }]} />)
    const button = screen.getByRole('button', { name: 'Clear' })
    expect(button.className).toContain('max-lg:min-h-11')
    expect(button.className).toContain('max-lg:min-w-11')
    fireEvent.click(button)
    expect(clear).toHaveBeenCalledOnce()
  })

  it('while narrowed, announces the lead and then the line, joined by a comma, and leaves the actions out', () => {
    render(<ListStatus results={{ count: 23, noun: 'matches', active: true }} lead="23 matches" line="Matching words only."
      actions={[{ label: 'Set up search by meaning', onSelect: () => {} }]} />)
    expect(screen.getByRole('status').textContent).toBe('23 matches, Matching words only.')
  })

  it('a ListStatus passed neither lead nor actions draws as it did before them', () => {
    const { container } = render(<ListStatus results={{ count: 6, noun: 'projects', active: false }} line="6 projects" />)
    expect(container.querySelector('[data-list-line]')?.textContent).toBe('6 projects')
    expect(container.querySelectorAll('[data-line-part="lead"], button')).toHaveLength(0)
  })
})
