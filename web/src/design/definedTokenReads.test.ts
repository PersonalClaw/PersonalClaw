import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Defined-token-read rail ─────────────────────────────────────────────────
// A `var(--token)` with NO fallback, naming a token that is DEFINED NOWHERE, is
// invalid at computed-value time — so the browser throws the whole declaration
// away. The element then renders as if the line had never been written, and
// nothing in this repo could see it:
//
//   · `tokenLint` inspects VALUES (hex/px), not variable names.
//   · `inertUtilities` inspects `className` strings; these live in `style={{}}`.
//   · TypeScript never looks inside a CSS string.
//   · axe and the contrast sweep see the RESULT, which is a plausible-looking
//     surface — the defect is that a tint is absent, not that it is wrong.
//
// Four such reads shipped, measured in a real browser before this rail existed:
//
//   authored                                          resolved to
//   background: var(--color-error-container)           rgba(0, 0, 0, 0)
//   color: var(--color-on-error-container)             inherited body ink
//   border: 1px solid var(--color-outline-var)         0px / none  ← whole shorthand
//   padding-left: calc(var(--space-s) + 2 * 1rem)      0px         ← whole calc
//
// The first two were `IncidentBanner` and `BrowseMirror`'s auth-expired alert:
// the app's loudest surfaces rendered with no alert affordance at all. The third
// dropped the hairline on an empty week-grid cell (`--color-outline-variant`
// misspelt). The fourth erased every depth indent in the workflow-run node tree
// (`--spacing-s` misspelt), so a nested run read as flat.
//
// The population is DERIVED, not enumerated, and needs no allowlist because the
// property is sharp: a read either resolves or it does not. Three shapes are
// correctly NOT violations and the guard test below proves the scanner knows it —
// a fallback (`var(--x, 0deg)`) is valid by construction, a dynamically built
// name (`var(--color-${tone})`) cannot be resolved statically, and prose in a
// comment is not a declaration.
//
// Runs in the existing CI `web` job (vitest) — no browser and no build needed.

// vitest runs from the web/ package dir; source lives in web/src.
const SRC = join(process.cwd(), 'src')

const SOURCE_EXT = /\.(tsx?|css)$/
const TEST_FILE = /\.(test|spec)\.tsx?$/

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (SOURCE_EXT.test(entry) && !TEST_FILE.test(entry)) out.push(p)
  }
  return out
}

/** Comments carry design rationale that names tokens in prose — including one
 *  that explains why a token is deliberately NOT referenced (`exporters.ts` and
 *  `--prose-measure`). Reading those as declarations would flag the explanation
 *  for the fix. */
export function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/[^\n]*/g, '$1')
}

/** Every custom property this file DEFINES. Three shapes, all real in this repo:
 *  a CSS declaration (`--color-danger: #f66c66`), a style-object or srcdoc-map key
 *  (`'--bg': '--color-canvas'`), and an imperative write
 *  (`setProperty('--shell-corner-r', …)`) for the vars measured at runtime. */
export function definitionsIn(text: string): string[] {
  const out: string[] = []
  for (const m of text.matchAll(/(?:^|[\s{;,'"([])(--[a-zA-Z0-9_-]+)['"]?\s*:/gm)) out.push(m[1])
  for (const m of text.matchAll(/setProperty\(\s*['"`](--[a-zA-Z0-9_-]+)['"`]/g)) out.push(m[1])
  return out
}

/** Every custom property this file READS with no fallback. The trailing `)` is
 *  load-bearing twice over: `var(--x, 12px)` has a fallback and so is valid
 *  whatever `--x` does, and `var(--color-${tone})` stops the match at `$`, which
 *  is the only honest verdict on a name assembled at runtime. */
export function noFallbackReadsIn(text: string): string[] {
  return [...stripComments(text).matchAll(/var\(\s*(--[a-zA-Z0-9_-]+)\s*\)/g)].map((m) => m[1])
}

interface Offender { file: string; line: number; token: string }

function scan(files: string[]): { offenders: Offender[]; defined: Set<string>; reads: number } {
  const defined = new Set<string>()
  for (const f of files) for (const t of definitionsIn(readFileSync(f, 'utf8'))) defined.add(t)

  const offenders: Offender[] = []
  let reads = 0
  for (const f of files) {
    const text = stripComments(readFileSync(f, 'utf8'))
    for (const m of text.matchAll(/var\(\s*(--[a-zA-Z0-9_-]+)\s*\)/g)) {
      reads += 1
      if (defined.has(m[1])) continue
      offenders.push({ file: f.slice(SRC.length + 1), line: text.slice(0, m.index).split('\n').length, token: m[1] })
    }
  }
  return { offenders, defined, reads }
}

describe('defined-token reads (a var(--token) with no fallback must resolve)', () => {
  const files = walk(SRC)
  const { offenders, defined, reads } = scan(files)

  // ── Vacuity floor ────────────────────────────────────────────────────────
  // A scanner that matches nothing passes every assertion it makes. Prove the
  // population was found BEFORE asserting the offender list is empty.
  it('finds the source files, the token definitions and the reads', () => {
    expect(files.length, 'source files walked').toBeGreaterThan(100)
    expect(defined.size, 'custom properties defined').toBeGreaterThan(80)
    expect(reads, 'no-fallback var() reads').toBeGreaterThan(200)
  })

  // ── Guards the guard ─────────────────────────────────────────────────────
  // Mutation-test the SCANNER, not only the code: an over-eager matcher would
  // red on the three legitimate shapes and get itself weakened, and a too-lax
  // one would be green on the exact defects this rail exists to catch.
  it('can tell an unresolvable read from the three legitimate shapes', () => {
    const flagged = (src: string) => {
      const defs = new Set(definitionsIn(src))
      return noFallbackReadsIn(src).filter((t) => !defs.has(t))
    }
    // The four real defects, verbatim from the tree before the fix.
    expect(flagged(`style={{ background: 'var(--color-error-container)' }}`)).toEqual(['--color-error-container'])
    expect(flagged(`border: 1px solid var(--color-outline-var);`)).toEqual(['--color-outline-var'])
    expect(flagged('`calc(var(--space-s) + 2 * 1rem)`')).toEqual(['--space-s'])
    // 1. A fallback makes the declaration valid whatever the token does.
    expect(flagged(`padding-right: var(--shell-corner-r, 140px);`)).toEqual([])
    // 2. A runtime-assembled name is not statically resolvable — not a finding.
    expect(flagged('`var(--color-${tone})`')).toEqual([])
    // 3. Prose that names a token is not a declaration, in either comment style.
    expect(flagged(`// the measure is NOT var(--prose-measure) on purpose`)).toEqual([])
    expect(flagged(`/* never reference var(--prose-measure) here */`)).toEqual([])
    // And each of the three definition shapes must satisfy a read.
    expect(flagged(`--color-danger: #f66c66; color: var(--color-danger);`)).toEqual([])
    expect(flagged(`const M = { '--bg': '--color-canvas' }; body{background:var(--bg)}`)).toEqual([])
    expect(flagged(`el.style.setProperty('--angle', a); 'conic-gradient(from var(--angle))'`)).toEqual([])
  })

  it('every no-fallback var() read names a token that is defined somewhere', () => {
    const detail = offenders.map((o) => `${o.file}:${o.line} reads ${o.token}`)
    expect(
      detail,
      'An unresolvable var() invalidates the whole declaration, so the style is silently ABSENT.\n' +
        'Define the token in design/tokens.css, spell it the way it is defined, or give the read a\n' +
        'fallback. Never satisfy this rail by deleting the read that was trying to paint something.',
    ).toEqual([])
  })
})
