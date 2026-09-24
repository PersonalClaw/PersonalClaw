import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── "0 proposal(s) filed" is the most recognisable tell of unfinished product copy ────────────
//
// `#/learning` carried ELEVEN parenthetical plurals across three files — `pass(es)`, `error(s)`,
// `proposal(s)`, `render(s)`, `run(s)` ×2, `verdict(s)` ×2, `evidence ref(s)`, `gate scenario(s)`.
// Every one of them had the count in hand one token earlier, so nothing was preventing the real
// sentence; the shortcut was just never spent.
//
// 🔑 THIS IS DRIFT, NOT HOUSE STYLE, AND THE COUNT SETTLES IT. Measured across `web/src`: the
// conditional form (`${n} thing${n === 1 ? '' : 's'}`) ships at **156 sites**, the parenthetical at
// roughly 40. The majority is the canonical form, so this surface converged onto what already exists
// rather than onto a new helper — there are already two page-local `plural()` helpers
// (`settings/PortabilityPanel`, `settings/MemoryGraph`) and adding a third page-local copy is how a
// shared idiom becomes three implementations.
//
// 🪤 ONE RECORDED RULING GOES THE OTHER WAY AND IS CORRECT. `ui/ListScaffold`'s `LoadError` states
// its reason for staying noun-free: *"Nothing on this component reads the count, so the noun cannot
// be pluralized reliably"* — a caller passes `what="project"` and the component never sees a number.
// That distinction holds precisely because the count is absent. It is the opposite of these eleven,
// where the count is the adjacent token, and it is why this rail is scoped to a surface rather than
// asserting a global ban.
//
// 🪤 AND WHY THIS RAIL IS SCOPED TO THIS DIRECTORY. The obvious tree-wide ceiling is not sound yet:
// distinguishing a plural from a call needs to know it is inside a string, and a regex that keys on
// the following character admits `new Set(s).add(id)` and `open(s)` — a tree-wide run reports 80
// where a hand count of user-visible copy is about half that. A ceiling on a population known to be
// wrong is the failure this program has already paid for twice, so the remaining per-directory
// worklist lives in `.validation/ux/PRODUCT-POLISH.md` and gets converged a surface at a time.

const HERE = join(process.cwd(), 'src/pages/learning')

/** A plural reads as TEXT: what follows is prose punctuation or a template boundary. A CALL closes
 *  an expression — `open(s)}` and `Set(s).add` are code, and inside this directory the follower test
 *  is enough to tell them apart (verified against every hit here). */
const PLURAL = /[A-Za-z]\((?:s|es)\)(?=[ ,·\n]|\)`)/

const stripComments = (t: string) =>
  t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sources(p, out)
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

function offenders(): string[] {
  const out: string[] = []
  for (const abs of sources(HERE)) {
    const lines = stripComments(readFileSync(abs, 'utf8')).split('\n')
    lines.forEach((line, i) => {
      const m = PLURAL.exec(line)
      if (m && !/http\(s\)/.test(line)) {
        out.push(`${abs.slice(abs.indexOf('src/') + 4)}:${i + 1}  ${line.trim().slice(0, 80)}`)
      }
    })
  }
  return out
}

describe('#/learning writes real plurals, not "(s)"', () => {
  it('the scan reads the surface AND the pattern still matches one (double vacuity floor)', () => {
    // Half one: a scan over zero files reports a clean surface.
    expect(sources(HERE).length, 'no sources found under pages/learning').toBeGreaterThan(8)
    // Half two: an assertion of "zero matches" is also satisfied by a pattern that cannot match.
    // A positive control is the only thing that separates the two, and this rail's whole claim is a
    // zero — so it carries one.
    expect(PLURAL.test('{week.produced_total} proposal(s) filed'), 'the detector is broken').toBe(true)
    expect(PLURAL.test('over ${n} pass(es), and'), 'it must catch (es) too').toBe(true)
    // …and must NOT fire on the code shapes that live in this directory.
    expect(PLURAL.test('onClick={() => open(s)}'), 'a call is not a plural').toBe(false)
    expect(PLURAL.test('setBusy((s) => new Set(s).add(id))'), 'nor is Set(s).add').toBe(false)
  })

  it('no file on this surface ships a parenthetical plural', () => {
    expect(
      offenders(),
      'The count is in hand one token earlier at every one of these, so write the sentence: ' +
        '`${n} thing${n === 1 ? \'\' : \'s\'}` — the form 156 other sites in web/src already use.',
    ).toEqual([])
  })

  // 🪤 THIS ASSERTION'S FIRST VERSION BORROWED ITS NUMBER FROM CODE THE CHANGE NEVER TOUCHED. It
  // counted conditionals across the whole directory and required >= 11 — but `AblationPanel` (4),
  // `BenchmarkPanel` (4), `StudiesPanel` (2), `JudgeBenchPanel` (1) and `IdentityReportPanel` (1)
  // already shipped twelve between them. So deleting a count outright — turning
  // "3 evidence refs" into "evidence" — satisfied both checks and the mutation ESCAPED. The floor was
  // measuring the surface's pre-existing health, not this conversion.
  //
  // PER FILE fixes it, because these three files carry no conditionals other than the eleven that
  // replaced a parenthetical, 1:1. Still floors rather than pins: growth is fine, removal is not.
  const CONVERTED: [string, number][] = [
    ['LearningPage.tsx', 3],
    ['HealthPanel.tsx', 6],
    ['learningMeta.ts', 2],
  ]

  it.each(CONVERTED)('%s still branches on all %d of its counts', (file, n) => {
    const src = readFileSync(join(HERE, file), 'utf8')
    const conditionals = [...src.matchAll(/=== 1 \? '' : '(s|es)'/g)]
    expect(
      conditionals.length,
      `${file}'s parentheticals were CONVERTED, not removed — dropping the count instead of ` +
        'pluralising it satisfies "no (s) here" while saying less than before',
    ).toBeGreaterThanOrEqual(n)
  })
})
