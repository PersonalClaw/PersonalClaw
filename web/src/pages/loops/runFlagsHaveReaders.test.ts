/**
 * Issue 558's RECURRENCE GUARD: a flag `foldReducer` can write must have somebody who reads it.
 *
 * `runFlags.deleted` was folded correctly from the day it was written and **no component ever read
 * it**, so a loop deleted in another tab left its cockpit rendering the loop indefinitely. The fix
 * for that is one `useEffect` in `LoopCockpitPage`. This file is the part that stops the NEXT one:
 * the sibling rail in `deletedLoopIsReported.test.tsx` pins the three gone-routes that exist today
 * by hand-written pattern, which is exactly right for the behaviours it names and says nothing
 * about the fifth flag somebody adds next month.
 *
 * So this rail is keyed off the flag's OWN PRODUCER rather than a list of flags:
 *
 *   1. the declared set comes from `RunFlags` — parsed out of `runFold.ts`, and cross-checked
 *      against `Object.keys(emptyRunFlags())` so the interface and the factory cannot drift;
 *   2. the WRITTEN set is discovered by replaying every event `RUN_LIFECYCLE` can deliver through
 *      the real `foldReducer` and keeping whatever moves off its empty value — no hand-list of
 *      which event sets which flag;
 *   3. every written flag must have a non-test reader outside `runFold.ts`.
 *
 * Add a flag to `RunFlags` + `emptyRunFlags` + a `foldReducer` case and forget the reader, and (3)
 * reds naming the flag. Add it to the interface but never write it and (1)/(2) reds as dead weight.
 * Neither is a judgement call a reviewer has to remember to make.
 *
 * 🪤 THE FAKE VERSION of this file asserts `LoopCockpitPage.tsx` contains the string
 * `runFlags.deleted`. That passes forever, it would have passed on the broken code if the page had
 * merely mentioned the field in a comment, and — the part that matters — it says nothing about the
 * next flag, which is the whole failure mode. The rail has to DISCOVER the producer's flags and
 * check each one.
 *
 * 🪤 THE OTHER FAKE VERSION is tautological: derive the flag set by calling `foldReducer`, then
 * "check the reader" by calling `foldReducer` again. Both sides would be the function under test
 * and the rail could not fail. Leg (3) deliberately leaves the module graph entirely and greps
 * source text, so the producer and the consumer are measured by different means.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { foldReducer, emptyRunFlags, type RunFlags } from './runFold'
import { RUN_LIFECYCLE } from './useRunStream'

const SRC = join(process.cwd(), 'src')
/** The producer. Excluded from the reader scan: `foldRun` copying a flag onto the view-model is a
 *  hand-off, not a consumption — counting it would let a flag be "read" by the very file that
 *  writes it, which is the shape this rail exists to catch. */
const PRODUCER = join(SRC, 'pages', 'loops', 'runFold.ts')

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

const SOURCES = walk(SRC).filter((f) => f !== PRODUCER)

/** The flags the interface DECLARES, read off the producer's own source. */
function declaredFlags(): string[] {
  const src = readFileSync(PRODUCER, 'utf8')
  const block = src.match(/export interface RunFlags \{([\s\S]*?)\n\}/)
  if (!block) throw new Error('could not find `export interface RunFlags` in runFold.ts')
  // Field lines only. A doc-comment line starts with `/*` or `*`, so it cannot match.
  return [...block[1].matchAll(/^\s{2}([A-Za-z_$][\w$]*)\s*\??\s*:/gm)].map((m) => m[1])
}

/** The flags `foldReducer` can actually SET, discovered by replay. The payload probes exercise the
 *  data-dependent branches (`gate_check` only records a failure when `ok === false`); they are a
 *  grid over shapes, NOT a map of event → flag, so a new event/flag pair is discovered, not added
 *  here. */
function writtenFlags(): Map<string, string[]> {
  const empty = emptyRunFlags()
  const probes: unknown[] = [undefined, {}, { ok: false }, { stage: 's', title: 't', findings: 1 }]
  const byFlag = new Map<string, string[]>()
  for (const event of RUN_LIFECYCLE) {
    for (const data of probes) {
      const next = foldReducer(empty, event, data)
      for (const k of Object.keys(empty) as (keyof RunFlags)[]) {
        if (JSON.stringify(next[k]) === JSON.stringify(empty[k])) continue
        byFlag.set(k, [...new Set([...(byFlag.get(k) ?? []), event])])
      }
    }
  }
  return byFlag
}

/** Files that READ `<flag>` off a folded flag set. Scoped to the conventional holder names so a
 *  bare `.gate` on an unrelated object cannot count as a reader. */
function readersOf(flag: string): string[] {
  const re = new RegExp(String.raw`\b(?:runFlags|flags|vm|run)\s*\.\s*${flag}\b`)
  return SOURCES.filter((f) => re.test(readFileSync(f, 'utf8'))).map((f) => relative(SRC, f))
}

describe('the RunFlags producer and its readers agree', () => {
  const declared = declaredFlags()
  const written = writtenFlags()

  it('found the tree, the interface and the events it is supposed to be measuring', () => {
    // The vacuity floor. A moved `src/`, a renamed interface, or a broken walk all produce empty
    // sets, and empty sets satisfy every assertion below.
    expect(SOURCES.length).toBeGreaterThan(200)
    expect(RUN_LIFECYCLE.length).toBeGreaterThan(20)
    expect(declared.length).toBeGreaterThan(0)
    // The interface and the factory are two statements of the same contract; a field added to one
    // and not the other is the drift that made `deleted` easy to miss in the first place.
    expect([...declared].sort()).toEqual(Object.keys(emptyRunFlags()).sort())
    // And the specific flag this issue is about must still be in scope, or the rail has quietly
    // stopped covering the regression it was written for.
    expect(declared).toContain('deleted')
  })

  it('the reader scan can actually fail — negative control', () => {
    // Without this, a broken regex or an empty SOURCES list would make every leg below pass by
    // finding a reader for anything.
    expect(readersOf('neverEverReadByAnyone')).toEqual([])
    // ...and it must find the one we know is there.
    expect(readersOf('judgeDegraded').length).toBeGreaterThan(0)
  })

  it('every declared flag is one the reducer can actually write', () => {
    // A flag nobody can set is dead weight, and it is also how a rail like leg 3 gets quietly
    // satisfied: an unwritable flag has nothing to read.
    expect([...written.keys()].sort()).toEqual([...declared].sort())
  })

  it.each([...written.keys()])(
    'runFlags.%s has a reader outside runFold.ts',
    (flag) => {
      const readers = readersOf(flag)
      expect(
        readers,
        `runFlags.${flag} is written by foldReducer (${written.get(flag)?.join(', ')}) and NOTHING `
          + `reads it. That is issue 558's shape exactly: the fold is right, the wire is missing. `
          + `Give it a reader, or delete the flag.`,
      ).not.toEqual([])
    },
  )
})
