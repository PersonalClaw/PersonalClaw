import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── The orchestrated entrances actually exist on the surfaces (atom FM-6) ────────────────
//
// `ui/motion/Entrance*.test.tsx` proves the primitive; `discover/entranceReplay.test.tsx`
// drives one real surface. Neither can notice a surface QUIETLY LOSING its entrance — a
// refactor that replaces `<EntranceGroup>` with a plain `<div>` leaves both of those green
// while the feature this atom shipped is gone from the product. So the adopting surfaces are
// named here and the invariant is asserted against their source, which is the only place the
// adoption is visible without rendering three heavy pages.
//
// This is a SOURCE scan for the reason `headerActionsAdoption.test.ts` records: the property
// is about construction. It is deliberately narrow — three surfaces, chosen because each has a
// real BAND STACK (2+ top-level regions) rather than a single list. It is NOT an
// every-page ratchet: most surfaces are one list in one column, and cascading a single region
// is motion for its own sake, which the plan's soul guardrail rules out.
//
// Not adopted, and why (so a later pass does not re-derive it):
//   · `settings/SettingsHome.tsx` — its masonry re-packs on every commit and re-parents blocks
//     between columns, so a staggered block would REMOUNT and replay its entrance on any
//     resize or async card load. It needs a stable layout before it can have an entrance.
//   · `tasks/TaskBoard.tsx` — a `LayoutGroup` + `AnimatePresence` drag surface; an entrance
//     would compete with the shared-layout animation a dragged card already rides.
//   · `dashboard/PinnedTiles.tsx` — renders `null` on an empty registry, so a region wrapper
//     would spend a `gap-2xl` of blank space on every install with no pinned tiles.

const PAGES = join(process.cwd(), 'src/pages')

/** Surfaces that MUST orchestrate their regions, with the minimum region count each one's
 *  layout actually has. The count is a floor, not an exact number: Discover's areas are
 *  server-authored, so its `map` yields one region per area at runtime. */
const ADOPTERS: { file: string; label: string; minRegions: number }[] = [
  { file: 'dashboard/DashboardPage.tsx', label: 'the dashboard home column', minRegions: 8 },
  { file: 'inbox/InboxPage.tsx', label: 'the inbox body', minRegions: 2 },
  { file: 'discover/DiscoverPage.tsx', label: 'the Discover hub column', minRegions: 2 },
]

const sourceOf = (file: string) => readFileSync(join(PAGES, file), 'utf8')

describe('orchestrated surface entrances', () => {
  it('finds the pages tree it scans', () => {
    // The vacuity floor. A scan whose fixtures moved reads as a clean pass, so make a
    // missing tree throw here rather than let every assertion below become trivially true.
    expect(readdirSync(PAGES).length).toBeGreaterThan(10)
  })

  it.each(ADOPTERS)('$label stages its regions through one EntranceGroup', ({ file, minRegions }) => {
    const src = sourceOf(file)
    const groups = src.match(/<EntranceGroup\b/g) ?? []
    // Exactly one: two groups on one surface would each start their own cascade, and the
    // nested one would stagger inside an already-staggering parent — the "busy" the atom's
    // done-when rules out.
    expect(groups, `${file} must declare exactly one <EntranceGroup>`).toHaveLength(1)
    const regions = src.match(/<EntranceRegion\b/g) ?? []
    expect(regions.length, `${file} must stage at least ${minRegions} regions`).toBeGreaterThanOrEqual(minRegions)
  })

  it.each(ADOPTERS)('$label reaches the shared primitive, never a private stagger', ({ file }) => {
    const src = sourceOf(file)
    // Through `ui/motion`'s barrel, like every other shared motion primitive.
    expect(src).toMatch(/from '\.\.\/\.\.\/ui\/motion'/)
    // And no surface may call the stagger machinery directly: the moment a page composes
    // its own `stagger()`/`regionStagger()` into a `motion.div`, the app has two entrance
    // vocabularies and retuning one stops retuning the other.
    expect(src, `${file} must not hand-roll a stagger`).not.toMatch(/\b(stagger|regionStagger)\(/)
  })

  it('regionStagger has exactly one consumer, and it is the shared primitive', () => {
    // The one-mechanism claim, measured across the whole frontend rather than asserted in a
    // comment. `design/motion.ts` declares it; `ui/motion/Entrance.tsx` consumes it; the two
    // test files read it. Anything else is a second entrance vocabulary.
    const SRC = join(process.cwd(), 'src')
    const hits: string[] = []
    const walk = (dir: string) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = join(dir, e.name)
        if (e.isDirectory()) { walk(p); continue }
        if (!/\.tsx?$/.test(e.name)) continue
        if (/\bregionStagger\b/.test(readFileSync(p, 'utf8'))) hits.push(p.slice(SRC.length + 1))
      }
    }
    walk(SRC)
    expect(hits.sort()).toEqual([
      'design/motion.test.ts',
      'design/motion.ts',
      'pages/surfaceEntranceAdoption.test.ts', // this file, naming the identifier
      'ui/motion/Entrance.tsx',
    ])
  })
})
