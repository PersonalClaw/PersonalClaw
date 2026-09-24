import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The app's checkbox is 16px, and WCAG 2.2 SC 2.5.8 wants 24 ────────────────────────────────
//
// `ui/forms`'s `Checkbox` draws `size-4`. Measured on the seeded `demo-home` fixture at
// `#/knowledge` (1440×900), all five visible instances were **16×16 reachable** — 8px short in
// BOTH axes, on the primitive rather than at one call site, so every consumer inherited it.
// There are fifteen production call sites across ten files.
//
// 🔑 WHY THE POINTER IDIOM AND NOT A BIGGER BOX. The app's usual answer is to grow the control and
// pull the growth back with a negative margin (`Toggle`'s `min-h-6 -my-px`, the task ticks'
// `size-6 -mx-1`). Here that means changing the drawn tick in fifteen places — a visual-language
// decision, not a bug fix — and the tightest of those rows has no slack to reclaim. `.hit-24`
// expands only what the POINTER can reach: measured **16×16 → 24×24 with zero layout shift**,
// position and box byte-identical on all five instances.
//
// 🔑 AND THE 8px COMES FROM THE ROW, NOT FROM A NEIGHBOUR. Probed on all four sides: what sits
// just outside the band is the enclosing list row, not a distinct control. This program has a
// measured case of the opposite (a 24px band on `SidePanel`'s handle stole `Mark step done`,
// `Mark step incomplete`, `Mark criterion incomplete` and `Edit`), which is why the check is
// per-side and not an assumption. It is also a second improvement rather than a cost: a near-miss
// on the tick used to land on the row and NAVIGATE, and now it toggles, because the input already
// calls `stopPropagation`.
//
// ⚠️ axe WILL KEEP REPORTING `target-size` HERE. Every `getBoundingClientRect` check reads the
// element's own box, which this deliberately leaves alone. The tempting "fix" for that report is
// to inflate the input, which moves layout in fifteen places — so the caveat is asserted in
// source rather than left to be rediscovered.

const SRC = join(process.cwd(), 'src')

/** Comments stripped BEFORE any matching. Load-bearing rather than tidy: `forms.tsx`'s own
 *  comment argues against `size-6`, and `tokens.css`'s argues against `--hit-size` — matching raw
 *  text would let a rail read its own documentation as code. This program has four measured
 *  instances of exactly that, including one where the mutation ESCAPED. */
function strip(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '')
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')
}

const forms = strip(readFileSync(join(SRC, 'ui/forms.tsx'), 'utf8'))
const tokens = readFileSync(join(SRC, 'design/tokens.css'), 'utf8')

/** The `Checkbox` component body, sliced so a sibling's classes cannot satisfy these. */
function checkboxBody(): string {
  const at = forms.indexOf('export function Checkbox')
  expect(at, '`Checkbox` is not exported from ui/forms').toBeGreaterThan(-1)
  const next = forms.indexOf('export function ', at + 10)
  return forms.slice(at, next === -1 ? undefined : next)
}

function tsxFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) tsxFiles(p, acc)
    else if (entry.endsWith('.tsx')) acc.push(p)
  }
  return acc
}

const CONSUMERS = tsxFiles(SRC)
  .map((path) => ({ rel: path.slice(SRC.length + 1), code: strip(readFileSync(path, 'utf8')) }))
  .filter((f) => /<Checkbox\b/.test(f.code) && !f.rel.endsWith('.test.tsx'))

describe('the checkbox hit target', () => {
  it('reads its subjects (a rail over nothing asserts nothing)', () => {
    expect(tokens.length, 'tokens.css did not read').toBeGreaterThan(10_000)
    expect(forms).toContain('export function Checkbox')
    expect(CONSUMERS.length, 'no production consumers found — the census is broken').toBeGreaterThan(5)
    // And the stripper must strip, or every negative assertion below reads prose as code.
    expect(strip('/* size-6 */ x'), 'block comments').not.toContain('size-6')
    expect(strip('  // size-6\ny'), 'line comments').not.toContain('size-6')
  })

  it('the primitive carries the pointer idiom rather than a bigger box', () => {
    const body = checkboxBody()
    expect(body, 'still the 16px drawn tick — this change is pointer-only').toMatch(/\bsize-4\b/)
    expect(body, 'and it must reach the 24px floor via the overlay idiom').toMatch(/\bhit-24\b(?!-x)/)
    // `.hit-24-x` is for a THIN VERTICAL control and sets no `position`; a checkbox is square
    // and statically positioned, so the symmetric variant is the correct one.
    expect(body, 'the thin-handle variant is the wrong shape for a square control').not.toMatch(
      /\bhit-24-x\b/,
    )
  })

  it('no consumer restates the size — that is how a primitive fix fragments', () => {
    const offenders = CONSUMERS.filter((f) => {
      // A `size-*`/`h-*`/`w-*` inside a `<Checkbox …/>` element's own attributes.
      for (const m of f.code.matchAll(/<Checkbox\b[\s\S]{0,400}?\/>/g)) {
        if (/\b(size|[hw])-\d/.test(m[0])) return true
      }
      return false
    }).map((f) => f.rel)
    expect(
      offenders,
      'a call site that sets its own checkbox size drifts from the primitive the moment the ' +
        'primitive changes — pass visibility classes through `className`, never geometry',
    ).toEqual([])
  })

  it('🔴 a `.hit-24` adopter must not already be positioned', () => {
    // THE PRECONDITION, INVERTED INTO A RAIL. `.hit-24` sets `position: relative`; an adopter
    // that is already `absolute` is dropped out of its placement by it. That is not theory —
    // measured on `main` with nothing but `position: relative` injected, `NavRail`'s handle moved
    // from `left:192,top:0` to `left:0,top:900` and its reachable band went 4px → 1px, which is
    // why `.hit-24-x` exists as a sibling that sets no `position`.
    expect(
      /\.hit-24\s*\{[^}]*position:\s*relative/.test(tokens.replace(/\/\*[\s\S]*?\*\//g, '')),
      '`.hit-24` must still establish its own containing block, or the assertion below is moot',
    ).toBe(true)

    // 🪤 THE FIRST DRAFT OF THIS SCAN MISSED ITS OWN SUBJECT. It required `className=` to sit
    // immediately before the quote, which never matches the shape this repo actually uses —
    // `className={cx('hit-24 size-4 …', …)}`. Injecting `absolute` beside `hit-24` in `forms.tsx`
    // left all 27 assertions green, so the mutation ESCAPED. The scan now walks outward from each
    // `hit-24` to the enclosing `className={…}` and reads the WHOLE attribute, however it is
    // composed — plain string, `cx()` with any number of arguments, or a template literal.
    const bad: string[] = []
    // 🪤 AND TESTS ARE EXCLUDED, WHICH IS THE FIFTH INSTANCE OF A SCANNER READING ITS OWN SOURCE
    // in this program — the first four were comments, this one is a REGEX LITERAL and an
    // assertion message. `/\.hit-24\s*\{[^}]*position:\s*relative/` above contains `hit-24`, and
    // the word `absolute` appears in this very test's failure message, so the scan reported
    // itself twice on a clean tree. A test file's `hit-24` occurrences are assertions ABOUT the
    // utility, never adopters OF it, so excluding them is the correct scope rather than a dodge —
    // and the M6 mutation (injecting `absolute` into `forms.tsx`) is still caught, which is what
    // proves the narrowing did not cost the rail its teeth.
    for (const f of tsxFiles(SRC)
      .filter((p) => !/\.test\.tsx?$/.test(p))
      .map((p) => ({ rel: p.slice(SRC.length + 1), code: strip(readFileSync(p, 'utf8')) }))) {
      for (const m of f.code.matchAll(/\bhit-24\b(?!-x)/g)) {
        const at = f.code.lastIndexOf('className', m.index!)
        if (at < 0) continue
        // Balance from the attribute's opening brace so a nested `cx(…)` cannot end the window
        // early; fall back to a bounded slice for the plain `className="…"` form.
        const open = f.code.indexOf('{', at)
        let attr: string
        if (open > -1 && open < m.index!) {
          let depth = 0, i = open
          for (; i < f.code.length; i++) {
            if (f.code[i] === '{') depth++
            else if (f.code[i] === '}' && --depth === 0) break
          }
          attr = f.code.slice(open, i + 1)
        } else {
          attr = f.code.slice(at, at + 400)
        }
        if (!/\bhit-24\b(?!-x)/.test(attr)) continue
        if (/\b(absolute|fixed)\b/.test(attr)) bad.push(`${f.rel}: ${attr.replace(/\s+/g, ' ').slice(0, 100)}`)
      }
    }
    expect(
      bad,
      '`.hit-24` forces `position: relative`, so an already-absolute call site loses its ' +
        'placement. Use `.hit-24-x` (sets no position) or make the call site static.',
    ).toEqual([])
  })

  it('the axe caveat is recorded where the change is, not only in the utility', () => {
    // A future pass reading a `target-size` report on a checkbox must find the reason here
    // rather than "fixing" it by inflating the input across fifteen call sites.
    const raw = readFileSync(join(SRC, 'ui/forms.tsx'), 'utf8')
    expect(raw, 'the caveat must travel with the call site').toMatch(/axe[\s\S]{0,400}target-size/)
  })
})
