import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { SCHEMES } from '../../design/schemes'
import { weightStroke, weightWidth } from './KnowledgeGraph'

// ── The entity graph's marks must be perceivable (WCAG 2.1 SC 1.4.11) ─────────────────────────
//
// Measured in Chromium on a home seeded with 8 entities and 7 relations — the first time anything
// rendered this view, because a seed fixture cannot carry entities (they are SQLite-only) and the
// surface was not in the capture harness's inventory either:
//
//   node fill   (30% primary over surface)        1.68:1 dark   1.4:1 light
//   node stroke (--color-outline-variant)         2.04:1 dark   1.17:1 light
//   edge stroke (--color-outline-variant @0.5)    1.35:1 dark   1.07:1 light
//
// 3:1 is the bar for "graphical objects required to understand the content", and in a graph view the
// dots and the lines between them ARE the content. Nothing here was close.
//
// 🔑 THE OUTLINE CARRIES IT, NOT THE FILL. Raising the tint reaches 3:1 in dark only at 60%
// (3.21:1) and never in light (2.27:1 at 60%) — and it would restyle the graph. A ≥3:1 boundary is
// the standard remedy for a low-contrast shape.
//
// 🔑 WHY TWO MEASUREMENTS SETTLE TWELVE SCHEMES. `--color-on-surface-low` and `--color-canvas` are
// NEUTRALS: `design/schemes.ts` says so in as many words — "Neutral surfaces stay from tokens.css;
// this drives only the accent identity". A scheme cannot move them, so dark + light is the whole
// matrix. The assertion below pins that property rather than trusting this comment.
//
// 🪤 `--color-primary` measures fine (6.85:1 / 4.37:1) and is still wrong here: the same line uses
// it for `active`, so painting resting nodes with it would erase hover and selection. A token can
// pass the number and fail the meaning.

const SRC = readFileSync(join(process.cwd(), 'src/pages/knowledge/KnowledgeGraph.tsx'), 'utf8')
const TOKENS = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')

/** WCAG 2.1 relative luminance + ratio, over sRGB hex. */
function luminance(hex: string): number {
  const h = hex.replace('#', '')
  const chan = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * chan(0) + 0.7152 * chan(2) + 0.0722 * chan(4)
}
function ratio(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}
/** `fg` at `alpha` composited over `bg` — a stroke-opacity mark against the canvas. */
function over(fg: string, alpha: number, bg: string): string {
  const px = (hex: string, i: number) => parseInt(hex.replace('#', '').slice(i, i + 2), 16)
  const mix = (i: number) => Math.round(px(fg, i) * alpha + px(bg, i) * (1 - alpha))
  return `#${[0, 2, 4].map((i) => mix(i).toString(16).padStart(2, '0')).join('')}`
}

/** Read a token's value out of tokens.css for one mode, so a retint cannot drift this guard. */
function token(name: string, mode: 'dark' | 'light'): string {
  // tokens.css declares dark under `:root` and light under a `[data-mode="light"]`-ish block; take
  // the first declaration for dark and the last for light.
  const all = [...TOKENS.matchAll(new RegExp(`${name}\\s*:\\s*(#[0-9a-fA-F]{6})`, 'g'))].map((m) => m[1])
  expect(all.length, `${name} is declared in tokens.css`).toBeGreaterThanOrEqual(2)
  return mode === 'dark' ? all[0] : all[all.length - 1]
}

const MIN = 3 // SC 1.4.11

// ── The census: every file that renders graph marks, DERIVED (KL-17) ──────────────────────────
//
// This rail used to scan ONE hardcoded path, so it certified the entity graph and said nothing
// about the other graphs in the app. Widening it to a derived census found the same defect
// sitting unguarded in two more places — see VIOLATION_BASELINE below.
//
// 🔑 THE SIGNAL IS NODES ∧ EDGES, NOT THE FILE NAME. `*Graph*` is fragile in BOTH directions:
// it misses a renamed renderer and it enrols helpers that render nothing. Measured on this tree:
//   · `tasks/TaskGraph.tsx` is named like a graph and emits ZERO mark tags — it computes a layout
//     and hands it to `DagView`, which is where a contrast defect would actually live.
//   · `loops/planGraph.ts` is likewise pure layout math (and cannot hold JSX at all).
// So a file renders graph marks iff it emits at least one NODE mark tag AND at least one EDGE
// mark tag. A graph is nodes plus the connections between them; that conjunction is what
// separates a graph from a gauge or an icon. Measured precision on this tree: 3 files, 3 true
// positives, 0 false. `ui/ProgressRing.tsx` and `ui/composer/controls.tsx` both emit node marks
// (a gauge arc, a 16px ring) and no edge mark, so they fall out on the rule, not on an exception.
// `ui/content/InfographicView.tsx` hands a DSL string to the AntV engine — its marks are produced
// by a third-party renderer at runtime and do not exist in source, so there is nothing here to
// measure and no source rail can cover it.
//
// A geometry condition ("mark coordinates come from data, not literals") was also measured: it
// classifies all 3 the same way, so shipping it would add an unexercised knob that could silently
// EXCLUDE a real graph. Left out deliberately. If a future icon-bearing file false-positives, the
// failure names the file and the condition can be added then, against a real case.
//
// 🪤 SCOPE, so this cannot rot into a self-scan: only `.tsx` (JSX is the only way to emit a mark
// in source, which is also why `planGraph.ts` is out) and never `*.test.tsx`. This file is `.ts`,
// so the census cannot see its own vocabulary.
//
// 🪤 COMMENTS DO NOT COUNT. Comments are blanked before scanning — in-place, preserving length and
// newlines, so byte offsets stay true for the brace parse below. Measured cost of getting this
// wrong elsewhere in this repo: a docstring that spelled a raw tag turned `primitiveAdoption` red,
// and prose naming a retired utility kept a dead rule in the shipped CSS. Prose about a graph must
// not enrol a file, and prose naming a token must not indict one.

const NODE_MARKS = ['circle', 'rect', 'ellipse', 'polygon']
const EDGE_MARKS = ['line', 'path', 'polyline']

/** Blank comments in place — same length, same newlines, so offsets survive. String bodies stay
 *  (a token literal lives inside one), and a `//` inside a string is not a comment. */
function blankComments(src: string): string {
  const out = src.split('')
  const wipe = (a: number, b: number) => {
    for (let k = a; k < b && k < src.length; k++) if (out[k] !== '\n') out[k] = ' '
  }
  let i = 0
  while (i < src.length) {
    const c = src[i], d = src[i + 1]
    if (c === '/' && d === '/') { let j = src.indexOf('\n', i); if (j < 0) j = src.length; wipe(i, j); i = j; continue }
    if (c === '/' && d === '*') { let j = src.indexOf('*/', i); j = j < 0 ? src.length : j + 2; wipe(i, j); i = j; continue }
    if (c === '"' || c === "'" || c === '`') {
      let j = i + 1
      while (j < src.length && src[j] !== c) { if (src[j] === '\\') j++; j++ }
      i = j + 1; continue
    }
    i++
  }
  return out.join('')
}

/** Every `.tsx` under `root` that emits both a node mark and an edge mark, comments blanked. */
function graphMarkCensus(root: string): string[] {
  const found: string[] = []
  const walk = (dir: string) => {
    let entries
    try { entries = readdirSync(dir, { withFileTypes: true }) } catch { return }
    for (const e of entries) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (!e.name.endsWith('.tsx') || e.name.includes('.test.')) continue
      const src = blankComments(readFileSync(p, 'utf8'))
      const emits = (tags: string[]) => tags.some((t) => new RegExp(`<(?:motion\\.)?${t}[\\s>]`).test(src))
      if (emits(NODE_MARKS) && emits(EDGE_MARKS)) found.push(relative(process.cwd(), p))
    }
  }
  walk(root)
  return found.sort()
}

/** 🔴 The vacuity floor the clause demands, as a callable so the suite can PROVE it fires.
 *  A derived census that matches nothing satisfies every "for each file, assert X" loop
 *  perfectly — this is the assertion that turns that silent pass into a red. */
function assertCensusIsReal(census: string[]): void {
  expect(census.length, 'the graph-mark census matched NOTHING — the scan is broken, not the app').toBeGreaterThan(0)
  for (const f of HAND_VERIFIED) {
    expect(census, `${f} renders graph marks and MUST be in the derived census`).toContain(f)
  }
}

/** The files read by hand while writing this rail. The census must recover at least these. */
const HAND_VERIFIED = [
  'src/pages/knowledge/KnowledgeGraph.tsx',
  'src/pages/settings/MemoryGraph.tsx',
  'src/pages/tasks/DagView.tsx',
]

const OPENERS: Record<string, string> = { '{': '}', '(': ')', '[': ']' }

/** The balanced text inside the bracket at `start`. Exact, so a multi-line attribute value is read
 *  whole — a fixed character window would truncate DagView's three-line ternary. */
function balanced(src: string, start: number): string {
  const close = OPENERS[src[start]]
  let depth = 0
  for (let i = start; i < src.length; i++) {
    const c = src[i]
    if (c === '"' || c === "'" || c === '`') {
      const q = c; i++
      while (i < src.length && src[i] !== q) { if (src[i] === '\\') i++; i++ }
      continue
    }
    if (OPENERS[c]) depth++
    else if (c === close || c === '}' || c === ')' || c === ']') { depth--; if (depth === 0) return src.slice(start + 1, i) }
  }
  return src.slice(start + 1)
}

/** The right-hand side of a `const x = …`, to the end of the statement. Ends at a depth-0 newline
 *  whose next glyph cannot continue an expression — which is what keeps a wrapped ternary intact. */
function statementRHS(src: string, from: number): string {
  let depth = 0, i = from
  for (; i < src.length; i++) {
    const c = src[i]
    if (c === '"' || c === "'" || c === '`') {
      const q = c; i++
      while (i < src.length && src[i] !== q) { if (src[i] === '\\') i++; i++ }
      continue
    }
    if (OPENERS[c]) depth++
    else if (c === '}' || c === ')' || c === ']') { if (depth === 0) break; depth-- }
    else if (c === '\n' && depth === 0) {
      const m = /^\s*(\S)/.exec(src.slice(i + 1))
      if (!m || !':?.,+&|='.includes(m[1])) break
    }
  }
  return src.slice(from, i)
}

const TOKEN_REF = /var\((--color-[a-z0-9-]+)\)/g
const RESERVED = new Set(['true', 'false', 'null', 'undefined', 'var'])

/** Tokens named by `expr`, following identifiers into their declarations. `stroke={stroke}` and
 *  `stroke={ringTone}` name no token at the mark; the colour is two hops away in a const and then
 *  a lookup table, and a rail that stopped at the attribute would score DagView as token-free. */
function tokensIn(src: string, expr: string, seen = new Set<string>(), depth = 0): Set<string> {
  const out = new Set([...expr.matchAll(TOKEN_REF)].map((m) => m[1]))
  if (depth > 3) return out
  for (const m of expr.matchAll(/\b([A-Za-z_$][A-Za-z0-9_$]*)\b/g)) {
    const id = m[1]
    if (seen.has(id) || RESERVED.has(id)) continue
    seen.add(id)
    const decl = new RegExp(`\\b(?:const|let)\\s+${id}\\b[^=\\n]*=`).exec(src)
    if (!decl) continue
    for (const t of tokensIn(src, statementRHS(src, decl.index + decl[0].length), seen, depth + 1)) out.add(t)
  }
  return out
}

/** Every token painted as a mark BOUNDARY in `file`.
 *
 *  Boundaries only, on purpose: SC 1.4.11 is satisfied by the outline, and a node's interior is a
 *  surface that legitimately sits near the canvas (see the fill/outline note at the top). Asserting
 *  on fills too would indict `--color-surface` here and `--color-surface-container` in DagView for
 *  being what they are. */
function strokeTokens(file: string): string[] {
  const raw = readFileSync(join(process.cwd(), file), 'utf8')
  const scan = blankComments(raw)
  const out = new Set<string>()
  for (const m of scan.matchAll(/\bstroke\s*=\s*/g)) {
    const j = m.index + m[0].length
    if (raw[j] === '{') { for (const t of tokensIn(raw, balanced(raw, j))) out.add(t) }
    else if (raw[j] === '"' || raw[j] === "'") {
      const end = raw.indexOf(raw[j], j + 1)
      for (const t of raw.slice(j, end).matchAll(TOKEN_REF)) out.add(t[1])
    }
  }
  return [...out].sort()
}

/** A token clears the bar iff it clears it in BOTH modes — a mark cannot pick its theme. */
function clearsInBothModes(name: string): boolean {
  return (['dark', 'light'] as const).every((m) => ratio(token(name, m), token('--color-canvas', m)) >= MIN)
}

// 🔴 The two graphs this rail did NOT cover until KL-17 widened it, with the ratio measured from
// tokens.css at the time: `--color-outline-variant` is 2.04:1 dark / 1.17:1 light against the
// canvas — the SAME token, and the same shortfall, that the entity graph was fixed for above.
//
//   · settings/MemoryGraph.tsx  resting edge + resting node outline
//   · tasks/DagView.tsx         resting edge + resting node outline (so every DAG surface built on
//                               it inherits the defect: the task graph, plan review, workflow runs)
//
// This is a SHRINK-ONLY baseline, compared exactly. A new violating token, or a NEW graph file that
// ships one, fails here. Fixing one also fails here until its entry is deleted — which is the point:
// the ratchet cannot drain by itself. Both entries are owned by their components, not by this rail.
const VIOLATION_BASELINE: Record<string, string[]> = {
  'src/pages/settings/MemoryGraph.tsx': ['--color-outline-variant'],
  'src/pages/tasks/DagView.tsx': ['--color-outline-variant'],
}

describe('every file that renders graph marks is under the contrast rail', () => {
  const census = graphMarkCensus(join(process.cwd(), 'src'))

  it('derives a non-empty census that recovers the hand-verified files', () => {
    assertCensusIsReal(census)
  })

  it('the vacuity floor FIRES on an empty census — a broken scan cannot read as clean', () => {
    // Point the same scan at a directory that does not exist. It must come back empty, and the
    // floor must throw on it. Without this, every per-file loop below would pass vacuously.
    const nothing = graphMarkCensus(join(process.cwd(), 'src/__no_such_directory__'))
    expect(nothing, 'a nonexistent root yields no files').toEqual([])
    expect(() => assertCensusIsReal(nothing)).toThrow()
  })

  it('does not enrol files that emit marks without being graphs', () => {
    // The other direction of the signal. A gauge arc and an icon ring are node marks with no edge;
    // a layout module and a name-only "graph" emit nothing. All four must stay OUT on the rule.
    for (const f of [
      'src/ui/ProgressRing.tsx',
      'src/ui/composer/controls.tsx',
      'src/pages/tasks/TaskGraph.tsx',
      'src/ui/content/InfographicView.tsx',
    ]) expect(census, `${f} does not render graph marks`).not.toContain(f)
  })

  it('reads a boundary token out of every censused file — the per-file scan is not vacuous', () => {
    for (const f of census) {
      expect(strokeTokens(f).length, `${f} paints a mark boundary with at least one token`).toBeGreaterThan(0)
    }
  })

  it('every boundary token is declared in tokens.css, so a typo cannot pass unmeasured', () => {
    for (const f of census) {
      for (const name of strokeTokens(f)) {
        const all = [...TOKENS.matchAll(new RegExp(`${name}\\s*:\\s*(#[0-9a-fA-F]{6})`, 'g'))]
        expect(all.length, `${name} (used in ${f}) is declared for both modes in tokens.css`).toBeGreaterThanOrEqual(2)
      }
    }
  })

  it('mark boundaries clearing 3:1 in both modes — exactly the shrink-only baseline', () => {
    const failing: Record<string, string[]> = {}
    for (const f of census) {
      const bad = strokeTokens(f).filter((t) => !clearsInBothModes(t))
      if (bad.length) failing[f] = bad
    }
    expect(failing, 'a graph mark boundary below 3:1 (SC 1.4.11) — fix the file, then delete its baseline entry')
      .toEqual(VIOLATION_BASELINE)
  })

  it('the rule is satisfiable, and the baseline cannot outlive its files', () => {
    // A rule every file breaks is a rule nobody can land against; the entity graph proves it is
    // reachable. And a baseline entry for a file that left the census would freeze a stale
    // exemption in place, so it has to be deleted with the file.
    expect(census.filter((f) => strokeTokens(f).every(clearsInBothModes)).length,
      'at least one graph file already clears the bar').toBeGreaterThan(0)
    for (const f of Object.keys(VIOLATION_BASELINE)) {
      expect(census, `${f} has a baseline entry but is no longer censused — delete the entry`).toContain(f)
    }
  })
})

describe('the entity graph marks meet non-text contrast', () => {
  it('tokens.css yields two distinct values per token — the vacuity floor', () => {
    for (const name of ['--color-canvas', '--color-on-surface-low']) {
      const d = token(name, 'dark'), l = token(name, 'light')
      expect(d, `${name} dark`).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(l, `${name} light`).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(d, `${name} differs by mode, so the reads are not the same block twice`).not.toBe(l)
    }
  })

  for (const mode of ['dark', 'light'] as const) {
    it(`the resting node outline clears 3:1 in ${mode}`, () => {
      const r = ratio(token('--color-on-surface-low', mode), token('--color-canvas', mode))
      expect(r, `node outline in ${mode}`).toBeGreaterThanOrEqual(MIN)
    })

    it(`the resting relation clears 3:1 in ${mode} at the opacity it ships`, () => {
      const m = /strokeOpacity=\{hover && !active \? 0\.15 : ([\d.]+)\}/.exec(SRC)
      expect(m, 'the resting edge opacity is readable from source').toBeTruthy()
      const alpha = Number(m![1])
      const painted = over(token('--color-on-surface-low', mode), alpha, token('--color-canvas', mode))
      expect(ratio(painted, token('--color-canvas', mode)), `edge at ${alpha} in ${mode}`)
        .toBeGreaterThanOrEqual(MIN)
    })

    it(`the OLD token would still fail in ${mode} — this guard is not vacuous`, () => {
      expect(ratio(token('--color-outline-variant', mode), token('--color-canvas', mode)))
        .toBeLessThan(MIN)
    })
  }

  it('both marks use the neutral, so no scheme can move them', () => {
    // The claim that two measurements cover twelve schemes, asserted rather than asserted-in-prose.
    expect(SCHEMES.length).toBeGreaterThanOrEqual(11)
    const schemeSrc = readFileSync(join(process.cwd(), 'src/design/schemes.ts'), 'utf8')
    for (const name of ['on-surface-low', 'canvas']) {
      expect(schemeSrc, `${name} is not per-scheme`).not.toMatch(new RegExp(`--color-${name}\\s*:`))
    }
  })

  it('resting marks use the neutral and active marks keep the accent', () => {
    // The distinction the fix must not flatten: hover/selected is what `--color-primary` means here.
    //
    // KL-17 encoded relation weight on colour, so the RELATION's resting stroke is no longer a
    // literal token — it is a mix whose 0-weight end must still BE the neutral. Asserting that by
    // calling the ramp is strictly stronger than the source regex this replaces: a regex proves the
    // text, the call proves the whole ramp's floor. The entity mark keeps the literal form.
    const entityMarks = [...SRC.matchAll(/stroke=\{active \? 'var\(--color-primary\)' : 'var\(--color-on-surface-low\)'\}/g)]
    expect(entityMarks.length, 'the entity mark still names the neutral outright').toBe(1)
    expect(SRC, 'the relation takes its resting colour from the weight ramp')
      .toMatch(/stroke=\{active \? 'var\(--color-primary\)' : weightStroke\(/)
    // The ramp's floor IS the neutral, so the measured ratios above still describe what paints.
    expect(weightStroke(0), 'a zero-weight relation must resolve to the neutral')
      .toContain('var(--color-on-surface-low)')
    expect(weightStroke(0), 'and must not mix any of the brighter end in at zero')
      .toContain('var(--color-on-surface) 0%')
    expect(SRC, 'the faint outline-variant stroke is gone from both marks')
      .not.toMatch(/: 'var\(--color-outline-variant\)'\}/)
  })

  it('the resting relation is at least a whole pixel wide', () => {
    // A sub-pixel stroke lands as partial pixel coverage, so it cannot reach the ratio its colour
    // promises — the measurement above would be a paper number at 0.6.
    // KL-17 made the width a weight ramp, so a source regex can no longer read one number off it.
    // Calling the ramp is the stronger form: it covers EVERY weight, not just the resting literal.
    expect(weightWidth(0, false), 'the lightest resting relation is still a whole pixel')
      .toBeGreaterThanOrEqual(1)
    expect(weightWidth(1, false), 'and the heaviest is wider, not narrower')
      .toBeGreaterThan(weightWidth(0, false))
  })

  it('and that width is what actually PAINTS, at any viewport', () => {
    // 🔴 The assertion above is necessary and was not sufficient. This graph's viewBox is 1000×1000
    // under `xMidYMid meet`, so it is never drawn 1:1 — the CTM scale measured 0.761 at 1440px and
    // 0.358 at 390px, painting a declared `1` as 0.76px and 0.36px. Width alone only shrank the
    // shortfall; `non-scaling-stroke` removes it, which is what makes the ratios above real numbers.
    //
    // Asserted on BOTH marks, because a relation drawn at its declared width beside a node that
    // still thins with the viewport is the same defect half-fixed.
    const marks = [...SRC.matchAll(/vectorEffect="non-scaling-stroke"/g)]
    expect(marks.length, 'one for the relation, one for the entity').toBe(2)
    expect(SRC, 'the relation declares it').toMatch(/<line[^>]*vectorEffect="non-scaling-stroke"/)
    expect(SRC, 'the entity declares it').toMatch(/<circle[^>]*vectorEffect="non-scaling-stroke"/)
  })

  it('the scale that makes it necessary is still what the code assumes', () => {
    // The vacuity guard for the reasoning, not the fix: if the viewBox ever stops being a fixed
    // world space, or `meet` becomes `slice`, the numbers in these comments need re-measuring rather
    // than trusting. Both are read from source so a change has to come past this test.
    expect(SRC, 'a fixed 1000×1000 world space').toMatch(/viewBox=\{`0 0 \$\{W\} \$\{H\}`\}/)
    expect(SRC, 'scaled to fit, which is why the CTM is below 1').toMatch(/preserveAspectRatio="xMidYMid meet"/)
  })
})
