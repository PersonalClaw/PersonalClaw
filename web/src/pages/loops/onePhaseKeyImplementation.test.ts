import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { PHASE_KEY_FIELDS, phaseKey, type PhaseKeyRow } from './loopPhases'

// ── There is ONE phase-status key resolution in this app, and every surface calls it ───────────
//
// `phase_status` is a map the backend writes under `kind.phase_key(row)`. To render a plan row's
// state, a surface must reconstruct that key from the row — and FOUR surfaces each reconstructed
// it themselves, three of them differently:
//
//   * `runFold.ts` (the shared run fold, behind the in-chat progress card): `stage || title`;
//   * `DesignCockpitPage.tsx`'s phase trail: `step || title`;
//   * `CodeCockpitPage.tsx`'s `stageKey`: `stage || title`, NEVER TRIMMED;
//   * `CodeSection.tsx`'s stage-in-play lookup: the trimmed `stage || title`.
//
// Each was "correct" for the kind its author had in front of them, and none was checked against
// the writer. Design plan rows carried only the second spelling, so the fold's lookup missed on
// every row: every design stage rendered `todo` forever, under a header counter that read
// "3/5 stages" because it counts phase_status VALUES instead of looking keys up (issue 494). The
// untrimmed copy is the same defect latent: a whitespace-only stage id keys on whitespace rather
// than falling through to the title. A behavioural test on any one surface passes the day it lands.
//
// So this is a CENSUS, not a spot check. It walks the whole tree and asserts exactly one file
// implements the resolution, and that the surfaces which need it IMPORT it.
//
// 🔑 THE SIGNATURE DESCRIBES THE RESOLUTION, NOT A VARIABLE NAME. `const skey = phaseKey(s)`
// still exists in runFold — as a call to the shared helper — so keying on that name would flag
// the correct code. What only a local re-implementation contains is the field-name literals
// being read off a phase row and or-ed together.
//
// 🔑 EVERY SIGNATURE CARRIES A VACUITY FLOOR. A rail whose regex rots (someone renames the
// helper) stops matching anything and then reports "exactly one implementation" forever, which
// is the failure mode that makes a source scan worse than no scan. Each pattern is asserted to
// match at least one file in its own right, and the walk is asserted to have found the tree.

const SRC = join(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) walk(p, out)
    // Test files are excluded — this very file quotes the patterns below, and a census that
    // counted its own rail would report two implementations of a resolution it does not contain.
    else if (/\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p)) out.push(p)
  }
  return out
}

const FILES = walk(SRC)
const rel = (f: string) => relative(SRC, f).replace(/\\/g, '/')
const read = (f: string) => readFileSync(f, 'utf8')

/** The one file allowed to implement the resolution. */
const OWNER = 'pages/loops/loopPhases.ts'

/** Surfaces that render a plan row's `phase_status` / `stage_status` state and must therefore
 *  import the helper rather than rebuild it. Every one of these carried its own copy. */
const CONSUMERS = [
  'pages/loops/runFold.ts',
  'pages/loops/DesignCockpitPage.tsx',
  'pages/code/CodeCockpitPage.tsx',
  'pages/code/CodeSection.tsx',
]

/** Shapes only a local re-implementation of the key resolution contains: the declared field
 *  names read off a phase row, or-ed / coalesced into one key. */
const LOCAL_KEY_SIGNATURES: [label: string, pattern: RegExp][] = [
  // `String(p.stage || p.title || '')` / `p.step || p.title` — field names or-ed together.
  ['field names or-ed into a key', /\b\w+\.(?:stage|step)\s*\|\|\s*\w+\.title\b/],
  // `String(x.stage ?? '').trim() || String(x.title ?? '').trim()` — the trimmed two-field form.
  ['a trimmed two-field key expression', /\.(?:stage|step)\s*\?\?\s*''\s*\)\s*\.trim\(\)/],
]

describe('one phase-status key resolution, imported by every surface that renders a stage', () => {
  it('found the source tree at all', () => {
    // The floor under the whole census: an empty walk makes every assertion below vacuous.
    expect(FILES.length, 'the walk found no .ts/.tsx files under src/').toBeGreaterThan(200)
    expect(FILES.map(rel)).toContain(OWNER)
  })

  it('the owner exports the helper AND the field vocabulary', () => {
    // Both are load-bearing: the helper is what surfaces call, and the field list is what
    // `tests/test_loop_phase_key_one_owner.py` compares against the backend's own tuple. A
    // rename that drops either breaks the chain that keeps the two sides of the wire agreeing.
    const src = read(join(SRC, OWNER))
    expect(src).toMatch(/export function phaseKey\b/)
    expect(src).toMatch(/export const PHASE_KEY_FIELDS = \[[^\]]*\] as const/)
  })

  it.each(LOCAL_KEY_SIGNATURES)('%s appears in no file but the owner', (_label, pattern) => {
    const hits = FILES.filter((f) => pattern.test(read(f))).map(rel)
    expect(hits.filter((h) => h !== OWNER)).toEqual([])
  })

  // The vacuity floor for the patterns above, stated as its own case so a rotted regex is a
  // NAMED failure rather than a silently-passing census. It cannot be floored against a real
  // file — after the fix NO file contains a local copy, which is the whole point — so each
  // pattern is matched against the exact code it was written to catch. These samples are the
  // four copies that existed before the fix, verbatim.
  const SAMPLES: Record<string, string> = {
    'field names or-ed into a key': "const stageKey = (s: CodeStage): string => (s.stage || s.title || '')",
    'a trimmed two-field key expression': "ss[(String(s.stage ?? '').trim() || String(s.title ?? '').trim())]",
  }

  it.each(LOCAL_KEY_SIGNATURES)('%s still matches the code it was written to catch', (label, pattern) => {
    const sample = SAMPLES[label]
    expect(sample, `no sample registered for the "${label}" signature`).toBeTruthy()
    expect(pattern.test(sample), `the "${label}" pattern has rotted — it no longer matches the `
      + 'local copy it exists to forbid, so the census above is asserting nothing').toBe(true)
  })

  it.each(CONSUMERS)('%s imports the shared helper', (consumer) => {
    expect(FILES.map(rel), `${consumer} is gone — move this rail with it`).toContain(consumer)
    const src = read(join(SRC, consumer))
    expect(src, `${consumer} must import phaseKey from loopPhases, not rebuild it`)
      .toMatch(/import \{[^}]*\bphaseKey\b[^}]*\} from '(?:\.|\.\.\/loops)\/loopPhases'/)
  })

  it('no phase-rendering surface reads the retired phase-id field off a plan row', () => {
    // The one-name assertion. `step` is a common word this app legitimately owns elsewhere
    // (slider increments, the plan walkthrough's steps, onboarding state, the self-update
    // progress payload), so the sweep is scoped to the files that actually render a phase's
    // status — the ones where the word would mean the retired phase id.
    const renders = FILES.filter((f) => /phase_status|stage_status|LoopPhase|CodeStage/.test(read(f)))
    expect(renders.length, 'the scope filter matched no phase-rendering file — it has rotted')
      .toBeGreaterThan(2)
    const hits = renders.filter((f) => /\b(?:p|ph|s|phase|row|stage)\.step\b/.test(read(f))).map(rel)
    expect(hits, 'a plan row is being read by the retired phase-id field').toEqual([])
  })
})

describe('phaseKey — the resolution itself', () => {
  it('resolves each declared field in isolation, in the declared priority', () => {
    for (const field of PHASE_KEY_FIELDS) expect(phaseKey({ [field]: 'resolved' })).toBe('resolved')
    const all = Object.fromEntries(PHASE_KEY_FIELDS.map((f) => [f, `v-${f}`]))
    expect(phaseKey(all)).toBe(`v-${PHASE_KEY_FIELDS[0]}`)
  })

  it('trims, so an empty leading field falls through instead of keying on empty', () => {
    // A titled-but-stageless row deliberately carries an EMPTY-string `stage`. Coalescing on
    // null (`stage ?? title`) keeps that '' as the key → an unconditional phase_status miss →
    // the stage sticks on 'todo'. This is a real fix being held, not a hypothetical.
    expect(phaseKey({ stage: '', title: 'Kickoff' })).toBe('Kickoff')
    expect(phaseKey({ stage: '   ', title: 'Kickoff' })).toBe('Kickoff')
    expect(phaseKey({ stage: '  build  ' })).toBe('build')
  })

  it('resolves a row carrying only the retired field to nothing', () => {
    // The cast is the point: `PhaseKeyRow` is derived from PHASE_KEY_FIELDS, so passing the
    // retired field does not even typecheck. The runtime assertion holds the same line for a
    // row that arrives off the wire, where no type is enforced.
    expect(PHASE_KEY_FIELDS as readonly string[]).not.toContain('step')
    expect(phaseKey({ step: 'foundations' } as unknown as PhaseKeyRow)).toBe('')
    expect(phaseKey({})).toBe('')
    expect(phaseKey(undefined)).toBe('')
  })
})
