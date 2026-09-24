/**
 * #558 — a deleted loop must not leave its cockpit rendering forever.
 *
 * `runFold` has set `runFlags.deleted` on the `deleted` lifecycle event since it was written, and
 * **no component ever read it** — the flag was asserted only in its own unit tests. So deleting a
 * loop in one tab left an open cockpit rendering a loop that no longer exists, indefinitely:
 *
 *   - the 30s fallback poll collapsed a 404 and a dropped connection into the same `null`, and so
 *     could only treat a post-load null as a transient blip — it had no way to tell them apart;
 *   - every subsequent action hit `.catch(() => null)`, so the buttons stayed live and did nothing.
 *
 * A written-and-never-read control flag is #370's shape. Three signals can each say "gone", and all
 * three were discarded; they now land on the one `notFound` state the component already rendered.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { foldReducer, emptyRunFlags } from './runFold'

const read = (rel: string) => readFileSync(join(process.cwd(), rel), 'utf8')
const LOOP = 'src/pages/loops/LoopCockpitPage.tsx'

describe('the deleted flag has a reader at last', () => {
  it('foldReducer still sets it — the positive control for the wiring below', () => {
    expect(foldReducer(emptyRunFlags(), 'deleted').deleted).toBe(true)
  })

  it('LoopCockpitPage reads runFlags.deleted and reaches notFound', () => {
    const src = read(LOOP)
    // 🪤 The bug WAS the absence of any reader, so the rail has to assert a reader exists. Scope
    // to an effect that both reads the flag and sets the state — matching only the identifier
    // would pass on a comment mentioning it.
    expect(src).toMatch(/if\s*\(runFlags\.deleted\)\s*setNotFound\(true\)/)
    expect(src, 'and it must depend on the flag, or it fires once and never again')
      .toMatch(/\}, \[runFlags\.deleted\]\)/)
  })
})

describe('a 404 is authoritative; a dropped connection is not', () => {
  const src = read(LOOP)

  it('the loop fetch distinguishes a 404 from any other failure', () => {
    // Before: `.catch(() => null)` — one null for both, which is what forced the poll to treat
    // every post-load null as a blip.
    expect(src).toMatch(/e instanceof ApiError && e\.status === 404/)
    expect(src, 'the collapsing catch is gone').not.toMatch(/api\.uLoop\(id\)\.catch\(\(\) => null\)/)
  })

  it('the not-found branch fires on a 404 even after a successful load', () => {
    // The whole defect: the only route to notFound was "never loaded at all".
    expect(src).toMatch(/else if \(gone \|\| !everLoaded\.current\)/)
  })

  it('a null with no 404 is still treated as a blip', () => {
    // Vacuity guard the other way: this must NOT become "any null means gone", or a one-second
    // network hiccup would evict a live cockpit the SSE stream is still feeding.
    expect(src, 'goneness is set only inside the 404 branch')
      .toMatch(/if \(e instanceof ApiError && e\.status === 404\) gone = true/)
  })
})

describe('an action on a gone loop says so instead of doing nothing', () => {
  const src = read(LOOP)

  it('act() no longer swallows its failure', () => {
    expect(src, 'the silent catch is gone')
      .not.toMatch(/api\.uLoopAction\(id, a\)\.catch\(\(\) => null\)/)
    expect(src, 'and the failure is reported through the shared helper')
      .toMatch(/reportActionFailure\(`\$\{a\} this loop`\)/)
  })

  it('and a 404 from an action flips the cockpit to gone', () => {
    const act = src.slice(src.indexOf('async function act('))
    expect(act.slice(0, 700)).toMatch(/status === 404\) setNotFound\(true\)/)
  })
})

describe('every cockpit reaches a gone state — the family, by behaviour not mechanism', () => {
  // The three cockpits solve this THREE ways, and that is acceptable: what must hold is that each
  // one has a route from "deleted" to a terminal gone state. CodeCockpitPage reads the lifecycle
  // event directly into a `'missing'` state; DesignCockpitPage refetches on every event and traps
  // the 404; LoopCockpitPage now folds the flag AND traps the 404. This rail pins the OUTCOME so a
  // future edit to any one of them cannot quietly drop it.
  const cases: [string, string, RegExp][] = [
    ['LoopCockpitPage', LOOP, /if \(runFlags\.deleted\) setNotFound\(true\)/],
    ['DesignCockpitPage', 'src/pages/loops/DesignCockpitPage.tsx', /status === 404\) setNotFound\(true\)/],
    ['CodeCockpitPage', 'src/pages/code/CodeCockpitPage.tsx', /event === 'deleted'.*setProject\('missing'\)/],
  ]

  it.each(cases)('%s has a deleted -> gone route', (_name, rel, pattern) => {
    expect(read(rel)).toMatch(pattern)
  })

  it.each(cases)('%s closes its stream once gone, so EventSource stops retrying a 404', (_n, rel) => {
    // A stale tab whose loop is gone must not reconnect forever.
    expect(read(rel)).toMatch(/useRunStream\(id, [^)]*(notFound|!== 'missing')/)
  })
})
