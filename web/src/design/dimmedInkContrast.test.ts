import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Dimming the dimmest ink token drops it below AA ─────────────────────────────
//
// `--color-on-surface-low` is ALREADY the faintest text token in the palette. In dark mode it is
// `#9a9b9c`, which measures **6.67:1** on the modal surface `#131314` — comfortably AA. Adding a
// Tailwind opacity suffix multiplies it down:
//
//     text-on-surface-low         #9a9b9c on #131314   6.67:1   ✓ AA
//     text-on-surface-low/70      #727273 on #131314   3.86:1   ✗ below 4.5
//
// axe agreed independently (`color-contrast`, serious) on the **New project** modal — a surface the
// existing e2e a11y suite never reaches, because it analyses each route immediately after navigation
// and never opens a modal. Dropping the dimmer restores AA with **no new colour**, which is the same
// resolution `countChipContrast.test.ts` reached for the identical shape.
//
// 🔑 SCOPED TO ONE SITE ON PURPOSE. The pattern appears at ~45 places, and most are NOT defects —
// opacity is correct for icons, hover-reveal rest states, disabled controls, and struck/done rows,
// where a lower contrast IS the message. Only text that must be read at AA is in scope, so this rail
// bans the suffix on the LOW ink token in the ONE file where every instance was measured, rather than declaring
// 45 sites broken on a source pattern alone. Widening it means measuring each surface first.
//
// The other two violations axe found behind the click gate are recorded in the ledger, not fixed here:
//   · `ui/Segmented`'s TONED selected state — tone text on a 20% fill of the same tone, 4.23:1. A
//     visual-language decision across 54 call sites and 6+ tone registries → owner taste call.
//   · `nested-interactive` on the project rows and the agent form → a structural change to a
//     row-as-button pattern, its own family.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** Every className string in the tree, with file + line. */
function classAttributes(): Array<{ file: string; line: number; value: string }> {
  const out: Array<{ file: string; line: number; value: string }> = []
  for (const abs of walk(SRC)) {
    readFileSync(abs, 'utf8').split('\n').forEach((ln, i) => {
      for (const m of ln.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
        out.push({ file: abs.slice(SRC.length + 1), line: i + 1, value: m[1] ?? m[2] ?? '' })
      }
    })
  }
  return out
}

// The measured surfaces. Adding a file here is a claim that its dimmed-ink text was checked on the
// live DOM — not a guess from the class name.
const MEASURED = new Set([
  'pages/projects/ProjectsSection.tsx',
  // Added by measuring, per the rule above — not by reading the class name. `#/tasks?view=dag`'s
  // critical-path legend carried `text-on-surface-low/70` on 12px explanatory copy and axe reported
  // `[serious] color-contrast` in ALL THREE configs: **3.84:1** dark (#727374 on #151516) and
  // **3.95:1** light (#797c7c on #f6f8fb). Undimmed it measures 6.88:1 dark / 8.50:1 light, and the
  // surface goes 1 blocking → 0 in every config. Its sibling legend members were already full-alpha
  // `text-warn` / `text-danger`; this was the only dimmed one.
  'pages/tasks/TaskGraph.tsx',
])
const DIMMED_LOW_INK = /\btext-on-surface-low\/\d+\b/
// An ICON is not text: 1.4.3 governs text contrast, and a decorative glyph falls under 1.4.11
// non-text contrast instead. `ProjectsSection` keeps one `text-on-surface-low/40` on a `<Circle>`
// bullet, and axe does not flag it — so the rail skips className strings that carry an icon-sizing
// hint. Written as a RULE in the filter below (not a line-number allowlist) so it stays true as the
// file moves.

describe('the measured surfaces keep their ink undimmed', () => {
  it.each([...MEASURED])('%s has no dimmed low-ink TEXT', (file) => {
    const hits = classAttributes()
      .filter((c) => c.file === file && DIMMED_LOW_INK.test(c.value))
      // Skip the icon case — see ICON_CLASS above. Checked on the live DOM: axe flags the four TEXT
      // spans on the project detail page and not the <Circle> bullet.
      .filter((c) => !/\bsize-\d|<Circle|mt-0\.5 shrink-0/.test(c.value))
      .map((c) => `${c.file}:${c.line}  ${c.value.replace(/\s+/g, ' ').slice(0, 80)}`)
    expect(
      hits,
      `text-on-surface-low is already the faintest token (6.67:1); an opacity suffix takes it to ` +
        `3.86:1, below AA 1.4.3:\n  ${hits.join('\n  ')}`,
    ).toEqual([])
  })

  it('the placeholder still reads as a placeholder', () => {
    // The italic carries "this is not a value" — the fix must not have flattened that distinction
    // into ordinary body text.
    const src = readFileSync(join(SRC, 'pages/projects/ProjectsSection.tsx'), 'utf8')
    expect(src).toMatch(/text-on-surface-low text-\[0\.8125rem\] italic">No workspace bound/)
  })
})

describe('the rail is not vacuously green', () => {
  it('it scans real className strings', () => {
    // A broken extractor would report zero hits forever. Two cycles ago a rail matched nothing and
    // reported a clean sweep, so pin a floor.
    const all = classAttributes()
    expect(all.length, 'the extractor must find the tree\'s className strings').toBeGreaterThan(2000)
    for (const f of MEASURED) {
      expect(all.some((c) => c.file === f), `${f} must be in scope`).toBe(true)
    }
  })

  it('it still FLAGS the shape it guards', () => {
    expect(DIMMED_LOW_INK.test('flex-1 text-on-surface-low/70 text-[0.8125rem] italic')).toBe(true)
    expect(DIMMED_LOW_INK.test('flex-1 text-on-surface-low text-[0.8125rem] italic')).toBe(false)
    // And it must NOT flag a dimmed BORDER or surface — those are not text and 1.4.3 does not apply.
    expect(DIMMED_LOW_INK.test('border-outline-variant/40 bg-surface-container/60')).toBe(false)
  })
})
