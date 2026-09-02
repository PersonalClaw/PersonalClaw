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

  it('the scanner is not vacuously green — it finds the baselined stragglers', () => {
    // Guard against the regex rotting into a no-op: if it stopped matching, both
    // assertions above would pass on an empty set. The baseline names real files,
    // so the scan must still see at least one of them.
    expect(offenders.length, 'the scan must still find numeric z-[N] somewhere').toBeGreaterThan(0)
  })
})
