// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { countInlineFontWeights, countRawControls, scanDrift, type PrimitiveDrift } from './consistencyAudit.report'

// ── Primitive-adoption ratchet (design-system consistency) ──────────────────
// The third consistency rail (alongside token-lint-strict in tokenLint.test.ts
// and the a11y axe scan in e2e/): flag NEW bespoke chrome. Raw <button>,
// <input>/<textarea>/<select>, and ad-hoc dialogs outside web/src/ui/ should
// migrate to the shell primitives (Button/IconButton, the shared form family
// Field/TextInput/TextArea/Select, Modal).
//
// The raw controls are held PER FILE and EXACTLY, in primitiveAdoption.byFile.jsonl: one JSON
// line per file that still has any, sorted by file, its path relative to web/src as scanDrift()
// names it.
// - A count that rises fails, naming the file and the kind.
// - A count that falls, or a file that is now clean, fails until its line is lowered or deleted
//   in the same commit. The message prints the line to write.
// - A file with no line starts at zero, and a file listed twice fails.
// Exact, not a ceiling. The totals this replaced let one file's fix pay for another file's new
// button, and the notes in primitiveAdoption.baseline.json record each time slack in them was a
// hole. A per-file count also says which file moved.
//
// The inline font-weight count stays a total in primitiveAdoption.baseline.json.
//
// This runs in the existing CI `web` job (vitest) — no browser needed.

type Counts = Record<PrimitiveDrift, number>

const KINDS: readonly PrimitiveDrift[] = ['raw-button', 'raw-input', 'raw-dialog']
const ZERO: Counts = { 'raw-button': 0, 'raw-input': 0, 'raw-dialog': 0 }

const BY_FILE = 'src/design/primitiveAdoption.byFile.jsonl'
const TOTALS = 'src/design/primitiveAdoption.baseline.json'

const USE_INSTEAD: Record<PrimitiveDrift, string> = {
  'raw-button': 'use Button or IconButton from ui/',
  'raw-input': 'use the shared form family from ui/ (Field, TextInput, TextArea, Select)',
  'raw-dialog': 'use Modal from ui/',
}

/** The line a file's counts are written as. */
function lineFor(file: string, c: Counts): string {
  return JSON.stringify({ file, 'raw-button': c['raw-button'], 'raw-input': c['raw-input'], 'raw-dialog': c['raw-dialog'] })
}

/** The per-file baseline, and every way its text breaks the file's shape. */
function parseByFile(text: string): { counts: Map<string, Counts>; problems: string[] } {
  const counts = new Map<string, Counts>()
  const problems: string[] = []
  let previous: string | undefined
  text.split('\n').forEach((raw, i) => {
    if (!raw.trim()) return
    const at = `${BY_FILE} line ${i + 1}`
    let row: unknown
    try {
      row = JSON.parse(raw)
    } catch {
      problems.push(`${at} is not JSON: ${raw}`)
      return
    }
    const r = (row && typeof row === 'object' && !Array.isArray(row) ? row : {}) as Record<string, unknown>
    const keys = Object.keys(r).sort().join(', ')
    const want = ['file', ...KINDS].sort().join(', ')
    const file = r.file
    if (keys !== want || typeof file !== 'string' || !file || KINDS.some((k) => !Number.isInteger(r[k]) || (r[k] as number) < 0)) {
      problems.push(`${at} must hold a file and a whole count for each of ${KINDS.join(', ')}: ${raw}`)
      return
    }
    const c = Object.fromEntries(KINDS.map((k) => [k, r[k]])) as Counts
    if (counts.has(file)) problems.push(`${at} lists ${file} a second time: keep one line per file`)
    else if (previous !== undefined && file < previous) problems.push(`${at} is out of order: ${file} sorts before ${previous}`)
    if (KINDS.every((k) => c[k] === 0)) problems.push(`${at} lists ${file} with no raw controls: delete the line`)
    if (!counts.has(file)) counts.set(file, c)
    previous = file
  })
  return { counts, problems }
}

/** Every difference between the live per-file counts and the baseline, each a sentence. A file
 *  with a rise is told only of the rise: the line to write is printed once nothing in it rose. */
function compareByFile(live: Record<string, Counts>, text: string): string[] {
  const { counts: base, problems } = parseByFile(text)
  const out = [...problems]
  for (const file of [...new Set([...Object.keys(live), ...base.keys()])].sort()) {
    const now = live[file] ?? ZERO
    const was = base.get(file) ?? ZERO
    const rose = KINDS.filter((k) => now[k] > was[k])
    for (const k of rose) out.push(`${file}: ${k} rose from ${was[k]} to ${now[k]}; ${USE_INSTEAD[k]}`)
    if (rose.length || KINDS.every((k) => now[k] === was[k])) continue
    out.push(
      KINDS.every((k) => now[k] === 0)
        ? `${file} has no raw controls left: delete its line from ${BY_FILE}`
        : `${file} has fewer raw controls: change its line in ${BY_FILE} to ${lineFor(file, now)}`,
    )
  }
  return out
}

const byFileText = readFileSync(join(process.cwd(), BY_FILE), 'utf8')
/** The baseline as live counts: a tree that matches it exactly, for the planted controls. */
const asBaselined = () => Object.fromEntries(parseByFile(byFileText).counts)

function loadTotals(): Record<string, unknown> {
  return JSON.parse(readFileSync(join(process.cwd(), TOTALS), 'utf8'))
}

describe('primitive-adoption ratchet: raw controls per file, exactly', () => {
  it('each file has exactly the raw controls its baseline line says', () => {
    expect(compareByFile(scanDrift().primitivesByFile, byFileText)).toEqual([])
  })

  it('a planted raw <button> in a new file fails, naming the file and the kind', () => {
    const planted = countRawControls('export const Planted = () => <button type="button">Go</button>\n')
    expect(planted).toEqual({ 'raw-button': 1, 'raw-input': 0, 'raw-dialog': 0 })
    expect(compareByFile({ ...asBaselined(), 'pages/planted/Planted.tsx': planted }, byFileText)).toEqual([
      'pages/planted/Planted.tsx: raw-button rose from 0 to 1; use Button or IconButton from ui/',
    ])
  })

  it('raw controls added to a baselined file fail, even when another file drops as many', () => {
    // The swap a total could not see: the sum is unchanged.
    const live = asBaselined()
    const [first] = Object.keys(live)
    const second = Object.keys(live).find((f) => f > first && live[f]['raw-input'] >= 2)
    expect(second, 'the baseline has no second file with two raw inputs to drop').toBeDefined()
    const added = countRawControls('<label>Name <input name="n" /></label>\n<select name="s"></select>\n')
    expect(added).toEqual({ 'raw-button': 0, 'raw-input': 2, 'raw-dialog': 0 })
    const was = live[first]['raw-input']
    live[first] = { ...live[first], 'raw-input': was + added['raw-input'] }
    live[second!] = { ...live[second!], 'raw-input': live[second!]['raw-input'] - added['raw-input'] }
    const failures = compareByFile(live, byFileText)
    expect(failures).toHaveLength(2)
    expect(failures[0]).toBe(
      `${first}: raw-input rose from ${was} to ${was + 2}; ` +
        'use the shared form family from ui/ (Field, TextInput, TextArea, Select)',
    )
    expect(failures[1]).toMatch(/ has (fewer|no) raw controls/)
    expect(failures[1].startsWith(`${second} has `)).toBe(true)
  })

  it('a planted ad-hoc dialog fails and points at Modal', () => {
    const planted = countRawControls('<div role="dialog" aria-modal="true">x</div>\n')
    expect(planted['raw-dialog']).toBe(1)
    expect(compareByFile({ ...asBaselined(), 'pages/planted/Sheet.tsx': planted }, byFileText)).toEqual([
      'pages/planted/Sheet.tsx: raw-dialog rose from 0 to 1; use Modal from ui/',
    ])
  })

  it('a dropped raw control fails until its line is lowered, and prints the line to write', () => {
    const live = asBaselined()
    const file = Object.keys(live).find((f) => live[f]['raw-button'] > 1)
    expect(file, 'the baseline has no file with two raw buttons to drop one of').toBeDefined()
    const lowered = { ...live[file!], 'raw-button': live[file!]['raw-button'] - 1 }
    live[file!] = lowered
    expect(compareByFile(live, byFileText)).toEqual([
      `${file} has fewer raw controls: change its line in ${BY_FILE} to ${lineFor(file!, lowered)}`,
    ])
    // Once the line says so, it passes.
    const relined = byFileText.split('\n').map((l) => (l && JSON.parse(l).file === file ? lineFor(file!, lowered) : l))
    expect(compareByFile(live, relined.join('\n'))).toEqual([])
  })

  it('a file that is clean, or gone, fails until its line is deleted', () => {
    const live = asBaselined()
    const [file] = Object.keys(live)
    delete live[file]
    expect(compareByFile(live, byFileText)).toEqual([`${file} has no raw controls left: delete its line from ${BY_FILE}`])
  })

  it('a file listed twice, out of order, empty or malformed fails', () => {
    const lines = byFileText.trimEnd().split('\n')
    const [a, b] = lines
    const { file: fileA } = JSON.parse(a)
    const { file: fileB } = JSON.parse(b)
    expect(compareByFile(asBaselined(), `${lines.join('\n')}\n${a}\n`)).toEqual([
      `${BY_FILE} line ${lines.length + 1} lists ${fileA} a second time: keep one line per file`,
    ])
    expect(parseByFile(`${b}\n${a}\n`).problems).toEqual([`${BY_FILE} line 2 is out of order: ${fileA} sorts before ${fileB}`])
    expect(parseByFile(lineFor('pages/x.tsx', ZERO)).problems).toEqual([
      `${BY_FILE} line 1 lists pages/x.tsx with no raw controls: delete the line`,
    ])
    expect(parseByFile('{"file":"pages/x.tsx","raw-button":1}\nnot json\n').problems).toHaveLength(2)
  })

  it('counts code, not comments that name a control', () => {
    const prose = [
      '// a design note quoting the <button> it says not to write',
      '/* or an <input> and a role="dialog" in a block comment */',
      'export const Row = () => (<div>{/* nor a <select> here */}</div>)',
    ].join('\n')
    expect(countRawControls(prose)).toEqual(ZERO)
    expect(countRawControls(`${prose}\nexport const B = () => <button>b</button>\n`)['raw-button']).toBe(1)
  })
})

describe('primitive-adoption ratchet: inline font weights', () => {
  it('the totals file holds only the inline font-weight count', () => {
    // The raw-control totals moved to the per-file baseline; a total left here would be a
    // number no test reads.
    expect(Object.keys(loadTotals()).filter((k) => !k.startsWith('_'))).toEqual(['inlineFontWeight'])
  })

  const base = loadTotals().inlineFontWeight as number

  it(`inline font-weight count must not exceed the baseline (${base})`, () => {
    const live = countInlineFontWeights().total
    expect(
      live,
      `New inline font-variation-settings "wght" detected (${live} > ${base}). ` +
        `Use fvs()/withWeight() (design/fontWeight.ts) or a .fw-* class, or lower inlineFontWeight ` +
        `in ${TOTALS} if migrating down.`,
    ).toBeLessThanOrEqual(base)
  })
})
