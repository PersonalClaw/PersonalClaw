import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Boxes } from 'lucide-react'
import { Section } from './settingsUI'

// ── Two settings pages wrote their section titles 2px larger than the other 23 ─────────────
//
// Censused across `pages/settings/`: **23 panels render section titles through `Section` (72
// instances) at `text-[0.9375rem]`**. Two hand-rolled theirs at `text-[1.0625rem]` — 17px beside a
// sibling page's 15px, on pages a user flips between in one sitting. Measured live before and after:
//
//                          BEFORE                      AFTER
//   #/settings/design       H2 17px ×4                  H2 15px  (h1 20px unchanged)
//   #/settings/diagnostics  H2 17px ×2                  H2 15px
//   #/settings/guardrails   H2 15px  (the majority)     unchanged
//
// 🔑 THE SAME TWO PANELS, A THIRD TIME. `DesignPanel` and `DiagnosticsPanel` were also the only two
// without a `PanelHeader` (#1154, which is why they measured `h1s=0`), and they are the pair here.
// **A panel that opted out of one primitive has usually opted out of the others** — when a census
// names an outlier, check what else it skipped.
//
// 🔑 WHY THEY HAD OPTED OUT, and what it cost to bring them in: their headers carry things `Section`
// could not express — a leading icon (the three control sections), a right-hand control (the
// light/dark switcher, the log toolbar) and a hint with live content (a connection dot + counts).
// So `Section` grew `icon`, `right` and a `ReactNode` hint, with four immediate adopters. **A
// primitive that the majority uses and the outliers cannot is missing a slot, not being ignored.**
//
// The rhythm converged too: both panels drove their own spacing (`gap-2xl`, `gap-l`) while the other
// 23 let each `Section`'s `mb-2xl` set it. Their roots are now plain `<div>`s like the rest.

describe('Section carries what the outliers had opted out for', () => {
  it('renders the title at the panel-section scale, once', () => {
    const { container } = render(<Section title="Backdrop & motion">x</Section>)
    const h = container.querySelector('h2')!
    expect(h.className).toContain('text-[0.9375rem]')
    expect(h.textContent).toBe('Backdrop & motion')
  })

  it('takes a leading icon without changing the heading level', () => {
    const { container } = render(<Section title="Typography & scale" icon={Boxes}>x</Section>)
    expect(container.querySelector('h2 svg'), 'the glyph belongs inside the heading row').not.toBeNull()
    expect(container.querySelectorAll('h3').length, 'still an h2 — the panel title is the h1').toBe(0)
  })

  it('takes a right-hand control beside the title', () => {
    render(<Section title="Color scheme" right={<button type="button">Dark</button>}>x</Section>)
    expect(screen.getByRole('button', { name: 'Dark' })).toBeTruthy()
  })

  it('takes a hint with live content, not just a string', () => {
    // This is the one that kept DiagnosticsPanel out: its hint is a connection dot plus counts.
    render(<Section title="Live logs" hint={<span>Streaming · <b>12</b> shown</span>}>x</Section>)
    expect(screen.getByText('12')).toBeTruthy()
  })

  it('still renders a bare section with no header at all', () => {
    const { container } = render(<Section>only children</Section>)
    expect(container.querySelector('h2')).toBeNull()
    expect(container.textContent).toBe('only children')
  })
})

describe('no settings panel hand-rolls a section title any more', () => {
  const DIR = join(process.cwd(), 'src/pages/settings')
  /** A hand-rolled section title: an `h2` with an explicit type size. `h3` is a NESTED group
   *  heading (`ProvidersPanel`'s provider groups sit inside a section, so a level-3 there is
   *  correct — converting it would skip a level the other way), and the uppercase micro-labels at
   *  `text-[0.75rem]` are widget labels, not page sections. */
  const offenders = readdirSync(DIR)
    .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
    .flatMap((n) => {
      const src = readFileSync(join(DIR, n), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      return [...src.matchAll(/<h2[^>]*className="([^"]*)"/g)]
        .filter((m) => /text-\[(0\.9375|1\.0625|1(\.\d+)?)rem\]/.test(m[1]))
        .map(() => n)
    })

  it('leaves none', () => {
    // `settingsUI.tsx` itself is where the one `h2` lives — that is the primitive, not a panel.
    expect([...new Set(offenders)].filter((n) => n !== 'settingsUI.tsx'), 'a panel writing its own section title drifts from the other 23').toEqual([])
  })

  it('and Section is genuinely the shared owner (not vacuously green)', () => {
    const uses = readdirSync(DIR)
      .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
      .reduce((sum, n) => sum + (readFileSync(join(DIR, n), 'utf8').match(/<Section\b/g) ?? []).length, 0)
    // 72 before this change, 76 after.
    expect(uses, 'the primitive must actually be in use').toBeGreaterThanOrEqual(76)
  })
})
