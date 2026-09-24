import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { UnreadRail } from './UnreadRail'

// ── An inline box-shadow was eating both notification rows' focus ring ────────────────────────
//
// Cycle 164 gave the `#/notifications` row and the bell's dropdown row a keyboard route
// (`ui/RowHitTarget`), and the ring that route draws — `has-[>button:focus-visible]:ring-*`, the
// idiom the tasks / projects / apps / loops rows all use — painted NOTHING on either of them.
//
// 🪤 The cause is a property collision that only a computed read shows. Tailwind's ring is a
// five-layer `box-shadow`; the rail was an INLINE `box-shadow`, and an inline value replaces the
// whole composite rather than adding to it. Measured on a focused row:
//
//   this row      `box-shadow: rgb(255,107,91) 2px 0 0 0 inset`   ← one layer: the rail, no ring
//   a tasks row   `… , oklab(0.708 0.161 0.088 / .5) 0 0 0 2px inset, …`   ← ring, 4th of five
//
// `--tw-ring-shadow` was set correctly the whole time, which is exactly why the source looked
// right. A row you can focus but cannot SEE focused is not a fixed row (WCAG 2.4.7), so the rail
// moved to a property nothing else on the row contends.
//
// It stays ONE component because the helper it replaces (`unreadRail()`, S2/T2.2) was itself a
// de-duplication: the rail had drifted across these two files once already. Same two consumers,
// same single home, no box-shadow.
//
// Verified after: coral ring resolves as the 4th layer on both rows in both themes, the rail is
// still 2px of the kind's tone at the left edge (dark #ff6b5b / light #c8452e), and the
// `#/notifications` captures are 0.00% changed at both themes — the rail lands on the same pixels.

describe('UnreadRail', () => {
  it('renders the rail for an UNREAD row, in the kind tone', () => {
    const { container } = render(<UnreadRail tone="var(--color-warn)" acked={false} />)
    const rail = container.firstElementChild as HTMLElement
    expect(rail.tagName).toBe('SPAN')
    expect(rail.style.background).toBe('var(--color-warn)')
  })

  it('renders NOTHING for a read row', () => {
    // Not an invisible span: a read row has no rail, and an empty element would still be a
    // layout participant and an AX node.
    const { container } = render(<UnreadRail tone="var(--color-primary)" acked />)
    expect(container.firstElementChild).toBeNull()
  })

  it('never sets box-shadow — the property the focus ring needs', () => {
    // THE regression this file exists to prevent. If the rail ever goes back to a box-shadow,
    // both rows silently lose their focus indicator again while the source still reads correctly.
    const { container } = render(<UnreadRail tone="var(--color-ok)" acked={false} />)
    const rail = container.firstElementChild as HTMLElement
    expect(rail.style.boxShadow, 'the rail must not use box-shadow').toBe('')
    // 🪤 Comments stripped first: this file's own doc comment EXPLAINS the box-shadow collision, and
    // the first version of this assertion failed on correct source because of it. Fourth occurrence of
    // "a scan counts its own prose" in this session.
    const code = readFileSync(join(process.cwd(), 'src/pages/notifications/UnreadRail.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).not.toMatch(/boxShadow|box-shadow:/)
  })

  it('is decorative, so it is hidden from assistive tech', () => {
    // The read-state is already carried by the row's own text and controls ("Mark read" vs
    // "Mark unread"); a nameless coloured bar would only add noise.
    const { container } = render(<UnreadRail tone="var(--color-primary)" acked={false} />)
    expect((container.firstElementChild as HTMLElement).getAttribute('aria-hidden')).toBe('true')
  })

  it('follows the row radius it is given', () => {
    const lg = render(<UnreadRail tone="t" acked={false} />).container.firstElementChild as HTMLElement
    const md = render(<UnreadRail tone="t" acked={false} radius="md" />).container.firstElementChild as HTMLElement
    expect(lg.className).toContain('rounded-l-lg')
    expect(md.className).toContain('rounded-l-md')
  })
})

describe('both notification rows render through it, and neither carries an inline shadow', () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')
  const ROWS = ['pages/notifications/NotificationsPage.tsx', 'ui/NotificationBell.tsx']

  it('the rail has exactly one implementation', () => {
    for (const rel of ROWS) {
      expect(read(rel), `${rel} must render the shared rail`).toMatch(/<UnreadRail /)
    }
    // And the retired helper is gone rather than left behind for a future call site to find.
    expect(read('pages/notifications/notificationMeta.ts')).not.toMatch(/export function unreadRail/)
  })

  it('neither row sets a style that would replace the ring composite', () => {
    for (const rel of ROWS) {
      const code = read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      // The row wrapper's own `style=` is what collided. `UnreadRail`'s internal one is fine —
      // it is a sibling span, not the element the ring is drawn on.
      const wrappers = [...code.matchAll(/<motion\.div[\s\S]{0,1200}?onClick=\{onOpen\}/g)]
      expect(wrappers.length, `${rel}: the row wrapper`).toBeGreaterThanOrEqual(1)
      for (const w of wrappers) expect(w[0], `${rel} must not restore an inline box-shadow`).not.toMatch(/style=\{unreadRail/)
    }
  })
})
