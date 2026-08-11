import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The surfaces the e2e a11y gate never reaches ─────────────────────────────────────
//
// `web/e2e/a11y.spec.ts` runs axe over 18 routes × 2 themes, but analyses each one
// immediately after navigation. Two whole classes of surface are therefore invisible to it:
//
//   1. THE 30 SETTINGS PANELS. `#/settings` renders the bento home; each panel lives at
//      `#/settings/<id>` and only mounts when you go there. The gate visits the parent
//      route, so 29 of the 30 never render under axe at all.
//   2. ANYTHING BEHIND A CLICK — the knowledge/inbox peek docks, the command palette, the
//      chat slash menu.
//
// Censused both (30 panels + 6 click-opened surfaces, element counts logged so a 0 means
// "measured clean" rather than "failed to mount"). Five blocking violations, all invisible
// to the route-level gate:
//
//     settings/design    color-contrast(1)                 4.47:1
//     settings/security  scrollable-region-focusable(1)
//     settings/audit     button-name(1)
//     knowledge peek     nested-interactive(1)
//     inbox peek         scrollable-region-focusable(1)
//
// This rail pins the four SOURCE-CHECKABLE fixes. The contrast one is a computed-colour
// property, so it is verified in the browser (light 15:1, dark 14:1) and not asserted here —
// a source scan cannot evaluate `color-mix()`.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('a scrollable region with no focusable content owns a tab stop', () => {
  // WCAG 2.1.1: if it scrolls, the keyboard must be able to scroll it. Both of these hold
  // read-only content (bare <code> lines, rendered markdown), so neither has a focusable
  // descendant to inherit a tab stop from. Measured before the fix: the inbox procedure hid
  // 1285px of 1541px below the fold with no way to reach it. Same resolution the kanban
  // columns took (see pages/tasks/scrollRegionKeyboard.test.tsx).
  const REGIONS: Array<[string, RegExp, string]> = [
    ['pages/settings/SecurityPanel.tsx', /max-h-44 overflow-y-auto/, 'built-in shell denylist'],
    ['pages/inbox/InboxDetail.tsx', /max-h-64 overflow-auto/, 'inbox procedure'],
  ]

  for (const [file, marker, what] of REGIONS) {
    it(`${what} is keyboard-scrollable and named`, () => {
      const src = read(file)
      const at = src.search(marker)
      expect(at, `the ${what} scroll container moved — re-measure before editing this rail`).toBeGreaterThan(-1)
      // Read the whole JSX tag, which wraps across lines.
      const tag = src.slice(at, src.indexOf('>', at))
      expect(tag, 'needs a tab stop, or the region cannot be scrolled by keyboard').toContain('tabIndex={0}')
      expect(tag, 'role=group keeps it announced as a container, not an unnamed widget').toContain('role="group"')
      expect(tag, 'an unnamed region announces nothing useful').toMatch(/aria-label=/)
    })
  }
})

describe('the insights dock header is two siblings, not nested controls', () => {
  // Regenerate used to be a `span role="button" tabIndex={0}` INSIDE the disclosure
  // <button> — `nested-interactive` (axe, serious). Same shape as the Combobox Clear fixed
  // in cycle 46, but this one WAS keyboard reachable, so it was a semantics defect rather
  // than a broken control.
  const src = read('pages/knowledge/KnowledgeDetail.tsx')

  it('has no role=button span left in the dock header', () => {
    const header = src.slice(src.indexOf('group/dock'), src.indexOf('{open && ('))
    expect(header, 'a role=button inside the disclosure is the nested-interactive shape')
      .not.toMatch(/role="button"/)
  })

  it('the disclosure announces its state', () => {
    // It toggles a region, so aria-expanded is the contract — verified live flipping
    // false → true on click.
    expect(src).toMatch(/aria-expanded=\{hasMore \? open : undefined\}/)
  })

  it('the chevron is decorative, not a duplicate tab stop', () => {
    // It is the affordance for the disclosure's OWN action, so making it a second button
    // would be two tab stops for one toggle. An aria-hidden <span> keeps it out of the a11y
    // tree entirely.
    const chevronBlock = src.slice(src.indexOf('group-hover/dock:bg-surface-high') - 240,
      src.indexOf('group-hover/dock:bg-surface-high') + 60)
    expect(chevronBlock).toContain('aria-hidden')
    expect(chevronBlock, 'a <button> here would duplicate the disclosure').not.toMatch(/<button/)
  })

  it('Regenerate uses the shared Button primitive', () => {
    // It was a hand-rolled span; pulling it out of the disclosure was the moment to adopt
    // the kit. This keeps the primitive-adoption ratchet flat instead of adding bespoke
    // chrome — the raw-<button> count is unchanged from the parent commit.
    expect(src).toMatch(/<Button variant="ghost" size="sm" onClick=\{onGenerate\}/)
    expect(src).toMatch(/^import \{ Button \} from '\.\.\/\.\.\/ui\/Button'$/m)
  })
})

describe('an icon-only button carries its own name', () => {
  it('the audit refresh button is named', () => {
    // Its two neighbours (Verify, Rotate) carry text; this one is a bare glyph, so it had
    // no accessible name at all (axe button-name, critical). `title` is the kit's
    // convention for a bare-glyph control (ruled in cycle 37).
    const src = read('pages/settings/AuditPanel.tsx')
    const line = src.split('\n').find((l) => l.includes('onClick={reload}') && l.includes('<Button'))
    expect(line, 'the audit refresh Button moved — re-measure').toBeTruthy()
    expect(line!, 'an icon-only Button with no title/aria-label has no accessible name').toMatch(/title=/)
  })
})
