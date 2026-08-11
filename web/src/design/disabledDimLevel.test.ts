import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Disabled controls dim at an established level, not an arbitrary one ───────────────
//
// A census of `disabled:opacity-*` across the tree found SEVEN distinct levels:
// 40 (35×) · 50 (26×) · 60 (7×) · 30 (2×) · 45 (1×) · 70 (1×). There is no design token for
// disabled dimming — every site hardcodes a utility class — so nothing stopped a new number
// appearing, and 30/45 are the shape that gets there: nobody deliberately picks 45.
//
// Converged the three CONTROL outliers onto 40, the level the primitives use for controls
// (`ui/Button`, `ui/Toggle`, `ui/Slider`, `ui/HeaderActions`):
//   pages/chat/MessageActions.tsx  30 → 40   (the Prev/Next answer icon buttons)
//   pages/tasks/formControls.tsx   45 → 40   (a selectable task row, disabled when cyclic)
//
// 🔑 WHAT THIS RAIL DOES AND DOES NOT DECIDE. The 40-vs-50 split is NOT drift this rail can
// settle: the primitives themselves disagree, and defensibly so — `Button`/`Toggle`/`Slider`
// (controls) use 40 while `forms`/`TextLink`/`ProjectPicker` (text and text-like) use 50, and
// `Markdown` uses 60. Text needs to stay legible at a level that would look under-dimmed on a
// button. Picking one canonical value across all three is an OWNER TASTE CALL, logged in the
// ledger. This rail only holds the line at the established set so the spread cannot grow while
// that decision is pending.
//
// 🪤 ONE DOCUMENTED EXCEPTION, PINNED SO IT CANNOT SPREAD. `pages/knowledge/KnowledgeListPage`
// dims a source-item link to **70** — the LIGHTEST dimming in the tree — and that is arguably
// right rather than wrong: when the item is gone the label rewrites itself to
// "(removed — insight kept)", so the disabled control is carrying information the user still
// needs to read. Snapping it to 50 would dim an explanatory label. Left as-is and pinned here
// by exact site, so it stays a considered exception instead of becoming precedent for a fourth
// level.

const SRC = join(process.cwd(), 'src')
const ESTABLISHED = new Set(['40', '50', '60'])
/** Site-pinned exception: a disabled control whose label carries information. */
const PINNED_EXCEPTIONS = new Map([['pages/knowledge/KnowledgeListPage.tsx', '70']])

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

function levels(): Array<{ file: string; line: number; level: string }> {
  const out: Array<{ file: string; line: number; level: string }> = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(/(?<!aria-)disabled:opacity-(\d+)/g)) {
      out.push({
        file: abs.slice(SRC.length + 1),
        line: text.slice(0, m.index).split('\n').length,
        level: m[1],
      })
    }
  }
  return out
}

describe('disabled dim level', () => {
  const all = levels()

  it('finds the disabled-opacity sites (not vacuously green)', () => {
    expect(all.length, 'the matcher must find disabled:opacity-* sites').toBeGreaterThan(50)
  })

  it('uses only established levels, or a pinned exception', () => {
    const stray = all.filter(
      (s) => !ESTABLISHED.has(s.level) && PINNED_EXCEPTIONS.get(s.file) !== s.level,
    )
    expect(
      stray.map((s) => `${s.file}:${s.line} → opacity-${s.level}`),
      'a disabled control dims at a level nothing else in the tree uses. Match the level used by ' +
        'comparable elements: 40 for controls (Button/Toggle/Slider), 50 for text and text-like ' +
        '(forms/TextLink), 60 for prose (Markdown). If the site genuinely needs its own level, ' +
        'pin it in PINNED_EXCEPTIONS with the reason:\n  ' +
        stray.map((s) => `${s.file}:${s.line} → opacity-${s.level}`).join('\n  '),
    ).toEqual([])
  })

  it('keeps the pinned exception honest — it must still exist', () => {
    // If the knowledge link stops being an exception (level changed, or file moved), this map is
    // stale and the next reader would trust a comment describing code that is gone.
    for (const [file, level] of PINNED_EXCEPTIONS) {
      const hit = all.some((s) => s.file === file && s.level === level)
      expect(hit, `PINNED_EXCEPTIONS lists ${file} → opacity-${level}, which no longer exists. ` +
        'Delete the entry (and its comment) rather than leaving a stale exemption.').toBe(true)
    }
  })

  it('controls converged: no site dims a control at 30 or 45', () => {
    // The specific divergence this rail was written for — proven red against the parent.
    const bad = all.filter((s) => s.level === '30' || s.level === '45')
    expect(bad.map((s) => `${s.file}:${s.line}`), 'converged to 40 in this change').toEqual([])
  })
})
