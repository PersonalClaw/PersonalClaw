import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// ── z-layer scale ratchet (CD-05) ───────────────────────────────────────────
// Layering used to be scattered magic numbers (50 / 55 / 60 / 70 / 80 / 100 /
// 200 / 9999) living only in comments — so the composer menus at z-[9999]
// painted OVER a firing toast and the Cmd-K palette. CD-05 replaced them with
// one named ladder in design/tokens.css (--z-content < --z-overlay < --z-modal
// < --z-menu < --z-toast); overlays reference a rung via z-[var(--z-*)].
//
// Two rails, so the fix cannot silently regress:
//   1. the ladder stays well-ordered, and in particular --z-menu < --z-toast —
//      the whole point of CD-05 (menus below toasts + palette);
//   2. no NEW numeric z-[N] appears in app source. The files that still carry
//      one at introduction are baselined (zLayerScale.baseline.json) and may
//      only shrink — the same ratchet shape as primitiveAdoption.baseline.json.
//
// design/ DEFINES the tokens and *.test files are exempt, mirroring
// tokenLint.test.ts. (CD-07 / FIX-2 will later fold a general z-[…] check into
// the unified arbitrary-value lint; this focused rail is the CD-05 slice.)

const SRC = join(process.cwd(), 'src')
const TOKENS_CSS = join(SRC, 'design/tokens.css')
const EXEMPT_DIRS = ['design/']

// A numeric arbitrary z-index utility: z-[60], z-[9999], -z-[10]. A token
// reference (z-[var(--z-modal)]) or a calc form is NOT a numeric literal and is
// exactly what this rail wants instead, so it must NOT match.
const NUMERIC_Z = /\bz-\[-?\d/

interface Baseline { maxArbitraryZLayers: number; files: string[] }

function loadBaseline(): Baseline {
  const j = JSON.parse(readFileSync(join(SRC, 'design/zLayerScale.baseline.json'), 'utf8'))
  return { maxArbitraryZLayers: j.maxArbitraryZLayers, files: j.files }
}

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    const rel = relative(SRC, p).replace(/\\/g, '/')
    if (EXEMPT_DIRS.some((d) => rel.startsWith(d))) continue
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

function isCommentLine(trimmed: string): boolean {
  // Design rationale routinely cites z-[70] in prose; skip comment lines exactly
  // as tokenLint.test.ts does, so a docstring can't be read as a violation.
  return trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')
}

/** Every non-comment numeric z-[N], grouped by file (relative to web/src). */
function scanNumericZ(): { byFile: Record<string, number>; total: number } {
  const byFile: Record<string, number> = {}
  let total = 0
  for (const f of walk(SRC)) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    const text = readFileSync(f, 'utf8')
    let n = 0
    for (const line of text.split('\n')) {
      if (isCommentLine(line.trim())) continue
      n += (line.match(new RegExp(NUMERIC_Z, 'g')) ?? []).length
    }
    if (n) { byFile[rel] = n; total += n }
  }
  return { byFile, total }
}

/** Resolve the five rungs from tokens.css. --z-content is a literal; the rest are
 *  `calc(var(--z-content) + N)`, so the resolved value is content + N. Parsing
 *  (rather than hardcoding) is what makes this assert the SHIPPED order. */
function resolveScale(): Record<'content' | 'overlay' | 'modal' | 'menu' | 'toast', number> {
  const css = readFileSync(TOKENS_CSS, 'utf8')
  const content = Number(/--z-content:\s*(\d+)/.exec(css)?.[1])
  const rung = (name: string): number => {
    const m = new RegExp(`--z-${name}:\\s*calc\\(\\s*var\\(--z-content\\)\\s*\\+\\s*(\\d+)\\s*\\)`).exec(css)
    return content + Number(m?.[1])
  }
  return { content, overlay: rung('overlay'), modal: rung('modal'), menu: rung('menu'), toast: rung('toast') }
}

describe('z-layer scale is a well-ordered ladder (CD-05)', () => {
  const z = resolveScale()

  it('defines all five rungs as finite numbers', () => {
    for (const [name, v] of Object.entries(z)) {
      expect(Number.isFinite(v), `--z-${name} must resolve to a number (found ${v})`).toBe(true)
    }
  })

  it('orders content < overlay < modal < menu < toast', () => {
    expect(z.content).toBeLessThan(z.overlay)
    expect(z.overlay).toBeLessThan(z.modal)
    expect(z.modal).toBeLessThan(z.menu)
    expect(z.menu).toBeLessThan(z.toast)
  })

  it('keeps control-anchored menus BELOW toasts and the command palette — the CD-05 payload', () => {
    // The reported bug: composer menus at z-[9999] painted over a firing toast
    // and Cmd-K. Both the palette and the Toaster ride --z-toast, so this single
    // inequality is the whole fix.
    expect(
      z.menu,
      `--z-menu (${z.menu}) must sit below --z-toast (${z.toast}) or menus paint over toasts/palette again`,
    ).toBeLessThan(z.toast)
  })
})

describe('no bespoke numeric z-[N] outside the shrinking baseline', () => {
  const baseline = loadBaseline()
  const { byFile, total } = scanNumericZ()
  const offenders = Object.keys(byFile).sort()

  it('every file carrying a numeric z-[N] is on the baseline (a NEW one turns CI red)', () => {
    const unlisted = offenders.filter((f) => !baseline.files.includes(f))
    expect(
      unlisted,
      `Bespoke numeric z-[N] found in file(s) not on the baseline:\n  ${unlisted.join('\n  ')}\n` +
        `Add a rung to the --z-* scale (design/tokens.css) and use z-[var(--z-*)] instead. ` +
        `If this is a deliberate exception, add the file to zLayerScale.baseline.json.`,
    ).toEqual([])
  })

  it(`total numeric z-[N] count must not exceed the baseline (${baseline.maxArbitraryZLayers})`, () => {
    expect(
      total,
      `Numeric z-[N] count rose to ${total} (baseline ${baseline.maxArbitraryZLayers}). ` +
        `Route the new layer through the --z-* scale, or — if migrating DOWN — lower ` +
        `maxArbitraryZLayers in zLayerScale.baseline.json in the same commit.`,
    ).toBeLessThanOrEqual(baseline.maxArbitraryZLayers)
  })

  it('the scanner is not vacuously green — the regex still recognizes the violation shapes', () => {
    // Guard against the regex rotting into a no-op. With the migration complete the
    // tree holds no live offender to find, so the anti-rot probe is synthetic: the
    // pattern must still match the shapes the rail exists to catch, and must NOT
    // match the token form the rail prescribes — a rot in either direction fails.
    for (const bad of ['z-[200]', 'z-[9999]', '-z-[10]', 'z-[60]']) {
      expect(NUMERIC_Z.test(bad), `NUMERIC_Z must match "${bad}"`).toBe(true)
    }
    for (const good of ['z-[var(--z-toast)]', 'z-[var(--z-modal)]', 'z-[calc(var(--z-content)+1)]']) {
      expect(NUMERIC_Z.test(good), `NUMERIC_Z must NOT match "${good}"`).toBe(false)
    }
  })
})

// ── THE SECOND SPELLING, which this rail could not see ──────────────────────
//
// 🔴 `NUMERIC_Z` above is `/\bz-\[-?\d/` — the ARBITRARY-value form only. Tailwind's STANDARD scale
// (`z-50`, `z-40`, `z-30`) expresses exactly the same thing and never matches it. So CD-05 migrated one
// spelling of two, and both this file and the baseline went on to state the stronger claim:
// *"With the migration complete the tree holds no live offender to find"* and *"Every fixed/portaled
// overlay rides the --z-* scale"*. Measured: **15 `fixed` overlays were on the standard scale, every
// one below --z-modal (60)**, and a NEW `fixed z-50` overlay passed CI.
//
// 🔑 TWO OF THEM WERE WRONG IN VALUE, NOT JUST IN SPELLING — and those are fixed rather than
// baselined. `tokens.css` defines `--z-menu: 100` as *"control-anchored menus / popovers: ABOVE
// dialogs"*, yet `ui/Popover` (portaled branch) and `ui/motion/ContextMenu` both sat at `z-50`
// (= --z-content), BELOW every dialog. They now ride `--z-menu`.
//
// 🪤 LATENT, NOT LIVE — stated precisely so nobody re-derives it as a crash. No `Popover` or
// `ContextMenu` is currently rendered inside a `<Modal>` region, so nothing paints behind a dialog
// today; the defect is that the primitives contradicted the rung their own component class is
// assigned. Checked before claiming otherwise.
//
// The remaining 15 are mostly right in VALUE and wrong only in expression: `--z-content: 50` is
// documented as *"content ceiling — full-screen content panels (side / detail / file) top out here"*,
// which is exactly where `SidePanel` and the widget frames belong. They are baselined, shrink-only,
// rather than migrated in a rail-fixing change.
//
// 🪤 MEASURED FROM THE CLASS LITERAL, NOT A BYTE WINDOW. A first pass took 260 characters around each
// `z-<n>` and asked whether `fixed` appeared nearby — which reported 21, because `Popover`'s two
// branches sit in one ternary and the inline `absolute` branch saw its sibling's `fixed`. Reading the
// individual class-list string gives 15. Same over-reporting shape as the `[^>]{0,N}`-inside-a-JSX-tag
// family this repo has hit before.

/** A single class-list literal that positions with `fixed` AND carries a standard-scale `z-<n>`. */
const FIXED_STANDARD_Z = /(['"`])((?:(?!\1)[\s\S]){0,400}?)\1/g

function scanStandardScaleFixed(): { byFile: Record<string, number>; total: number } {
  const byFile: Record<string, number> = {}
  let total = 0
  for (const f of walk(SRC)) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    // Comments blanked in place: design rationale cites `fixed z-50` in prose, and this rail is
    // about code. Length preserved so any future line report stays accurate.
    const text = readFileSync(f, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
      .replace(/(^|[^:"'`])\/\/[^\n]*/g, (m, p1) => p1 + ' '.repeat(m.length - p1.length))
    let n = 0
    for (const m of text.matchAll(FIXED_STANDARD_Z)) {
      const lit = m[2]
      if (/\bfixed\b/.test(lit) && /\bz-\d+\b/.test(lit)) n++
    }
    if (n) { byFile[rel] = n; total += n }
  }
  return { byFile, total }
}

describe('the standard-scale spelling is ratcheted too', () => {
  // Resolved here rather than reused: the ladder `z` in the first describe is closure-scoped to it.
  const z = resolveScale()
  const baseline = JSON.parse(readFileSync(join(SRC, 'design/zLayerScale.baseline.json'), 'utf8'))
  const { byFile, total } = scanStandardScaleFixed()

  it('the two control-anchored menu primitives ride --z-menu, ABOVE dialogs', () => {
    // The value fix. `--z-menu` exists for exactly this component class; at z-50 they sat below
    // every dialog, contradicting the rung tokens.css assigns them.
    const pop = readFileSync(join(SRC, 'ui/Popover.tsx'), 'utf8')
    expect(pop, 'the portaled popover must ride the menu rung').toMatch(/fixed z-\[var\(--z-menu\)\]/)
    const ctx = readFileSync(join(SRC, 'ui/motion/ContextMenu.tsx'), 'utf8')
    expect(ctx, 'the context menu must ride the menu rung').toMatch(/fixed z-\[var\(--z-menu\)\]/)
    for (const [name, src] of [['Popover', pop], ['ContextMenu', ctx]] as const) {
      expect(src, `${name} must not go back to a bare z-50 while fixed`).not.toMatch(/fixed z-50\b/)
    }
  })

  it('--z-menu really is above --z-modal, which is what makes that fix correct', () => {
    expect(z.menu, 'a menu must out-paint a dialog').toBeGreaterThan(z.modal)
  })

  it('no NEW file puts a fixed overlay on the standard scale', () => {
    const unlisted = Object.keys(byFile).sort().filter((f) => !baseline.standardScaleFixedFiles.includes(f))
    expect(
      unlisted,
      'A `fixed` overlay carrying a standard-scale z-<n> in a file not on the baseline:\n  '
      + unlisted.join('\n  ')
      + '\nRoute it through the --z-* scale (design/tokens.css) with z-[var(--z-*)].',
    ).toEqual([])
  })

  it('the standard-scale count only shrinks', () => {
    expect(
      total,
      `fixed+standard-scale z count rose to ${total} (baseline ${baseline.maxStandardScaleFixed}). `
      + 'Use z-[var(--z-*)], or lower maxStandardScaleFixed in the same commit when migrating DOWN.',
    ).toBeLessThanOrEqual(baseline.maxStandardScaleFixed)
  })

  it('the scanner finds the population it is filtering (not vacuously green)', () => {
    expect(total, 'the class-literal scan must still find the baselined overlays').toBeGreaterThanOrEqual(10)
  })
})
