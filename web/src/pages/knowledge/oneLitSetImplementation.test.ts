import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// ── There is ONE lit-set BFS in this repo, and both graph canvases call it ─────────────────────
//
// KL-17's clause is explicit that the ego view must reuse "the memory graph's existing lit-set BFS
// … rather than inventing a second one". A copy-pasted traversal passes every behavioural test in
// the suite on the day it lands and then drifts: tune the frontier in one canvas (weighted edges,
// a degree cap, a direction rule) and the other silently keeps answering the old question. So this
// is a CENSUS, not a spot check — it walks the whole tree and asserts exactly one file defines the
// traversal, and that the files which need it IMPORT it.
//
// 🔑 THE SIGNATURES DESCRIBE THE ALGORITHM, NOT A VARIABLE NAME. `const litSet = useMemo(…)` still
// exists in MemoryGraph — as a memo around the shared call — so keying on that name would flag the
// correct code. What only the implementation can contain is the hop-indexed loop, the mutable
// frontier, and the get-or-create adjacency build.
//
// 🔑 EVERY SIGNATURE CARRIES A VACUITY FLOOR. A rail whose regex rots (someone renames `frontier`)
// stops matching anything and then reports "exactly one implementation" forever, which is the
// failure mode that makes a source scan worse than no scan. Each pattern is therefore asserted to
// match at least one file in its own right, and the walk is asserted to have found the tree at all.

const SRC = join(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) walk(p, out)
    // Test files are excluded — this very file quotes the patterns below, and a census that
    // counted its own rail would report two implementations of a traversal it does not contain.
    else if (/\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p)) out.push(p)
  }
  return out
}

const FILES = walk(SRC)
const rel = (f: string) => relative(SRC, f).replace(/\\/g, '/')
const read = (f: string) => readFileSync(f, 'utf8')

/** Shapes only a hop-depth neighbourhood traversal contains. */
const BFS_SIGNATURES: [label: string, pattern: RegExp][] = [
  ['a hop-indexed expansion loop', /for\s*\(\s*let\s+hop\s*=\s*0\s*;/],
  ['a mutable BFS frontier', /\blet\s+frontier\b/],
  ['the get-or-create adjacency build', /adj\.get\([^)]*\)\s*\?\?\s*adj\.set\(/],
]

const THE_MODULE = 'lib/litSet.ts'
/** The canvases that need a hop-depth neighbourhood. Both must IMPORT the module — asserting the
 *  module merely EXISTS would pass while a canvas quietly kept its own copy. */
const CALLERS = ['pages/settings/MemoryGraph.tsx', 'pages/knowledge/KnowledgeEgoGraph.tsx']

describe('the lit-set BFS has exactly one implementation', () => {
  it('found the source tree at all (vacuity floor for every assertion below)', () => {
    // 543 non-test sources at the time of writing; a growing-tree floor, deliberately slack.
    expect(FILES.length).toBeGreaterThan(300)
  })

  it('every signature still matches real code (vacuity floor per pattern)', () => {
    for (const [label, pattern] of BFS_SIGNATURES) {
      const hits = FILES.filter((f) => pattern.test(read(f))).map(rel)
      expect(hits, `${label} matched nothing — the pattern rotted, so this rail proves nothing`)
        .not.toHaveLength(0)
    }
  })

  it('and exactly one file defines it', () => {
    const definers = FILES.filter((f) => {
      const src = read(f)
      return BFS_SIGNATURES.some(([, pattern]) => pattern.test(src))
    }).map(rel)
    expect(definers).toEqual([THE_MODULE])
  })

  it('both graph canvases import it instead of carrying their own', () => {
    for (const caller of CALLERS) {
      const src = read(join(SRC, caller))
      expect(src, `${caller} must call the shared traversal`).toMatch(/litNeighbourhood/)
      expect(src, `${caller} must import it from lib/litSet`).toMatch(/from '\.\.\/\.\.\/lib\/litSet'/)
      for (const [label, pattern] of BFS_SIGNATURES) {
        expect(pattern.test(src), `${caller} must not re-declare ${label}`).toBe(false)
      }
    }
  })
})
