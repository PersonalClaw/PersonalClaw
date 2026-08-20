import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { DocumentOutline } from './DocumentOutline'
import { parseOutline, type OutlineEntry } from './readingOutline'

// ── The outline panel (KL-16) ────────────────────────────────────────────────────────────
//
// Four clauses, and each has a way of looking held while being absent:
//
//   THE ROWS ARE CONTROLS. A clickable `<div>` renders identically to a button and is
//   unreachable by keyboard, so the row is driven with a real key press here, not a click.
//   THE ACTIVE ROW IS ANNOUNCED. A background tint satisfies every visual review and tells a
//   screen-reader user nothing; the state attribute is what is asserted.
//   THE SCROLL IS GUARDED. `scrollIntoView` on every render passes any "does it scroll?" test
//   and janks the panel under a spy that re-renders on each scroll frame. So the assertion is
//   about the CALL COUNT across three renders, and it needs the first call to happen at all —
//   otherwise a component that never scrolls passes the "only on change" half trivially.
//   NO HEADINGS, NO CHROME. An empty panel is a promise the body cannot keep. Asserted with a
//   populated render in the same test, so an always-null component cannot pass.
//
// The entries are built with the real `parseOutline` wherever the markdown is the point —
// a hand-built fixture can assert an indent rule the parser never produces.

const ENTRIES: OutlineEntry[] = [
  { offset: 0, depth: 0, text: 'Guide' },
  { offset: 10, depth: 1, text: 'Setup' },
  { offset: 40, depth: 2, text: 'On macOS' },
  { offset: 80, depth: 1, text: 'Setup' }, // the duplicate title, as a distinct row
]

/** jsdom implements no `scrollIntoView`, so the component's optional call is a no-op until one
 *  exists. Installing a spy IS the fixture for the guard assertions. */
let scrollSpy: ReturnType<typeof vi.fn>
beforeEach(() => {
  scrollSpy = vi.fn()
  ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = scrollSpy
})
afterEach(() => {
  delete (Element.prototype as unknown as { scrollIntoView?: unknown }).scrollIntoView
})

describe('an outline with no headings renders no chrome', () => {
  it('returns nothing for an empty list', () => {
    const { container } = render(<DocumentOutline entries={[]} activeOffset={null} onSelect={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('returns nothing for a heading-less body, while a body WITH headings renders rows', () => {
    const prose = render(
      <DocumentOutline entries={parseOutline('Just prose.\n\nTwo paragraphs of it.')} activeOffset={null} onSelect={vi.fn()} />,
    )
    expect(prose.container, 'a heading-less body').toBeEmptyDOMElement()

    // The positive control: the same component, a body that HAS headings.
    render(<DocumentOutline entries={parseOutline('# A\n\n## B')} activeOffset={null} onSelect={vi.fn()} />)
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['A', 'B'])
  })

  it('drops a text-less heading without dropping its neighbours', () => {
    const entries = parseOutline('# Real\n\n##\n\n## After')
    // Vacuity: the parser DID return the empty one — that is what the panel is filtering.
    expect(entries.map((e) => e.text)).toEqual(['Real', '', 'After'])
    render(<DocumentOutline entries={entries} activeOffset={null} onSelect={vi.fn()} />)
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Real', 'After'])
  })
})

describe('every row is a real control with its heading as its name', () => {
  it('names each row, and names the panel', () => {
    render(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={vi.fn()} />)
    expect(screen.getByRole('navigation', { name: 'Document outline' })).toBeInTheDocument()
    const rows = screen.getAllByRole('button')
    expect(rows).toHaveLength(4)
    expect(rows.map((r) => r.getAttribute('title'))).toEqual(['Guide', 'Setup', 'On macOS', 'Setup'])
  })

  it('activates by KEYBOARD, and reports the entry — not just its text', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={onSelect} />)

    // Tab to the second row (the first `Setup`) and press Enter. A clickable div would take
    // no tab stop and would never fire.
    await user.tab()
    await user.tab()
    expect(document.activeElement?.textContent, 'focus reached the second row').toBe('Setup')
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith(ENTRIES[1])

    // Space is the button's other key route.
    await user.keyboard(' ')
    expect(onSelect).toHaveBeenCalledTimes(2)
  })

  it('distinguishes the two identically-named rows by their entry', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={onSelect} />)
    const both = screen.getAllByRole('button', { name: 'Setup' })
    expect(both, 'two rows share a name — that is the case worth testing').toHaveLength(2)

    await user.click(both[1])
    expect(onSelect).toHaveBeenCalledWith(ENTRIES[3])
    expect(onSelect).not.toHaveBeenCalledWith(ENTRIES[1])
  })
})

describe('the active row is marked and kept visible', () => {
  it('exactly the active row carries the state, and the others carry its opposite', () => {
    render(<DocumentOutline entries={ENTRIES} activeOffset={80} onSelect={vi.fn()} />)
    const on = screen.getAllByRole('button', { pressed: true })
    expect(on).toHaveLength(1)
    // Offset 80 is the SECOND `Setup`. A slug-keyed panel would have marked the first.
    expect(on[0]).toBe(screen.getAllByRole('button', { name: 'Setup' })[1])
    // Vacuity: the other three announce the off state rather than announcing nothing.
    expect(screen.getAllByRole('button', { pressed: false })).toHaveLength(3)
  })

  it('marks nothing when no section is active', () => {
    render(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={vi.fn()} />)
    expect(screen.queryAllByRole('button', { pressed: true })).toHaveLength(0)
    expect(screen.getAllByRole('button'), 'the rows still rendered').toHaveLength(4)
  })

  it('scrolls the active row into view ONLY when it changes', () => {
    const { rerender } = render(<DocumentOutline entries={ENTRIES} activeOffset={10} onSelect={vi.fn()} />)
    // The first call must happen — without this the "no extra call" assertions below would
    // also pass for a component that never scrolls at all.
    expect(scrollSpy).toHaveBeenCalledTimes(1)
    expect(scrollSpy.mock.calls[0][0]).toEqual({ block: 'nearest' })

    // A re-render with the SAME active row (what a scroll-frame re-render looks like).
    rerender(<DocumentOutline entries={ENTRIES} activeOffset={10} onSelect={vi.fn()} />)
    expect(scrollSpy).toHaveBeenCalledTimes(1)

    // A real move to another section.
    rerender(<DocumentOutline entries={ENTRIES} activeOffset={40} onSelect={vi.fn()} />)
    expect(scrollSpy).toHaveBeenCalledTimes(2)

    // And leaving every section scrolls nothing — there is no row to show.
    rerender(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={vi.fn()} />)
    expect(scrollSpy).toHaveBeenCalledTimes(2)
  })

  it('scrolls the row that OWNS the offset, not whichever row shares its text', () => {
    render(<DocumentOutline entries={ENTRIES} activeOffset={80} onSelect={vi.fn()} />)
    expect(scrollSpy).toHaveBeenCalledTimes(1)
    const scrolled = scrollSpy.mock.instances[0] as HTMLElement
    expect(scrolled.textContent).toBe('Setup')
    expect(scrolled).toBe(screen.getAllByRole('button', { name: 'Setup' })[1].closest('li'))
  })
})

describe('the rows indent from the shallowest heading present', () => {
  /** The computed inline indent of each row, in px. */
  const indents = () =>
    screen.getAllByRole('button').map((b) => {
      const label = b.querySelector('.truncate') as HTMLElement
      return parseFloat(label.style.paddingInlineStart || '0')
    })

  it('a body whose top level is `##` renders its first level FLAT', () => {
    const entries = parseOutline(['## Setup', '', '### On macOS', '', '## Usage'].join('\n'))
    // Vacuity: nothing in this body is an `#` heading, so a raw-level indent would push
    // every row in by one.
    expect(entries.map((e) => e.depth)).toEqual([0, 1, 0])
    render(<DocumentOutline entries={entries} activeOffset={null} onSelect={vi.fn()} />)
    const px = indents()
    expect(px[0], 'the shallowest heading is flush').toBe(0)
    expect(px[1]).toBeGreaterThan(px[0])
    expect(px[2]).toBe(0)
  })

  it('each level in is one step further, and the step is one computed value', () => {
    render(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={vi.fn()} />)
    const px = indents()
    expect(px[0]).toBe(0)
    expect(px[1]).toBeGreaterThan(0)
    // Linear in depth: depth 2 is exactly twice depth 1, so the indent is derived from the
    // data rather than being a per-level class ladder.
    expect(px[2]).toBe(px[1] * 2)
    expect(px[3], 'two rows at the same depth indent the same').toBe(px[1])
  })

  it('names spacing tokens that exist, and overrides the primitive with the important variant', () => {
    const SRC = join(process.cwd(), 'src')
    // 🪤 Comments stripped FIRST, the repo's usual shape for a source rail. Without it this
    // scan read the paragraph in `DocumentOutline.tsx` that EXPLAINS why `gap-2xs` is wrong
    // and failed on the explanation — the same way an earlier draft of that paragraph tripped
    // `primitiveAdoption`'s raw-element count by spelling the tag it was discussing.
    const RAW = readFileSync(join(SRC, 'pages/knowledge/DocumentOutline.tsx'), 'utf8')
    const OUTLINE = RAW.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const TOKENS = readFileSync(join(SRC, 'design/tokens.css'), 'utf8')
    const BUTTON = readFileSync(join(SRC, 'ui/Button.tsx'), 'utf8')

    // The named spacing scale, read from where it is DEFINED. Vacuity: it must have found a
    // scale at all, and `2xs` must genuinely be absent from it — otherwise the assertion
    // below is about nothing. `gap-2xs` is what 7 files under pages/workflows write, and
    // Tailwind emits no rule for it, so their gap is silently zero.
    const scale = [...TOKENS.matchAll(/--spacing-([\w]+):/g)].map((m) => m[1])
    expect(scale, 'tokens.css defines a named spacing scale').toEqual(
      expect.arrayContaining(['xs', 's', 'm', 'l', 'xl']),
    )
    expect(scale).not.toContain('2xs')

    // 🪤 The skip below must be `^[\d.]+$`, not `^\d`. Written as `^\d` first, it skipped
    // `gap-2xs` itself — the one utility the assertion exists to catch — so the rail was
    // green against the exact defect. Only a PURELY numeric name is Tailwind's own scale.
    const gaps = [...OUTLINE.matchAll(/\bgap-([\w.]+)\b/g)].map((m) => m[1]).filter((n) => !/^[\d.]+$/.test(n))
    expect(gaps.length, 'the scan found the named gap utilities at all').toBeGreaterThan(0)
    for (const name of gaps) expect(scale, `gap-${name} must name a token that exists`).toContain(name)

    // The row is left-aligned against a primitive that centres its own content, so the
    // override cannot depend on which rule Tailwind happens to emit last.
    expect(BUTTON, 'the primitive really does centre — that is what is being overridden')
      .toMatch(/justify-center/)
    expect(OUTLINE).toMatch(/className="w-full !justify-start px-2"/)
  })

  it('a long heading truncates rather than widening the panel', () => {
    const long = `## ${'A very long heading that will not fit in a narrow outline panel '.repeat(3)}`
    render(<DocumentOutline entries={parseOutline(long)} activeOffset={null} onSelect={vi.fn()} />)
    const row = screen.getAllByRole('button')[0]
    const label = row.querySelector('.truncate') as HTMLElement
    expect(label, 'the label is the element that truncates').toBeTruthy()
    // …and the full text stays recoverable, since truncation hides it from sighted readers.
    expect(row.getAttribute('title')).toBe(label.textContent)
    expect(row.getAttribute('title')!.length).toBeGreaterThan(60)
  })
})
