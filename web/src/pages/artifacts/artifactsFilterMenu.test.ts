import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The last list surface lining up its own filter widgets ────────────────────────────────────────
//
// `ui/FilterMenu` declares itself the canonical control, and says why it exists:
//
//   "This replaces the older pattern of lining up a filter Segmented + a sort <select> + a scope
//    dropdown across the header — collapsing N competing widgets into one, consistent across every
//    page."
//
// Eight list surfaces render it (apps, code, inbox, loops, notifications, prompts, tasks, triggers).
// Artifacts shipped the pattern it replaced: a hand-rolled source dropdown + a hand-rolled collection
// dropdown + a sort `Segmented` pushed to the right edge, on a third toolbar row — and its own local
// component was *also* called `FilterMenu`, so the shadow was invisible to anyone grepping for the
// primitive's call sites. What artifacts therefore did not have, and every sibling did: the
// **active-count badge** on the trigger and the **inline Clear**. A user could not tell at a glance
// that a filter was on.
//
// 🪤 CONVERGE THE FORM, NOT THE SURFACE. The 17-tab kind strip stays a `Segmented`: it is this page's
// primary axis, it carries a deliberate, documented `collapse="scroll"` treatment, and `InboxPage`
// already ships exactly this "primary strip + one pill" pairing — so keeping it IS the canonical form,
// not an exception to it. Folding kind into the pill would have been a redesign wearing a consistency
// costume. `DeployedAppsMenu` likewise stays its own control: it lists served apps, it is not a filter.
//
// 🪤 AND THE NAME `FilterMenu` IS NOT THE FAMILY. The duplicate-name scan also paired
// `pages/dashboard/widgets/SystemHealth.tsx:Spark` with `ui/Spark.tsx:Spark` — which are a CPU
// sparkline and the brand claw mark. Same name, unrelated concepts, nothing to converge. Read both
// bodies before calling two components a duplicate; this rail asserts only what was measured.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
  const p = join(d, n)
  if (statSync(p).isDirectory()) return walk(p)
  return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
})
const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const ARTIFACTS = 'pages/artifacts/ArtifactsSection.tsx'

describe('the artifacts toolbar renders the canonical Filter & sort pill', () => {
  it('imports the primitive and no longer defines a shadow of it', () => {
    const code = codeOf(ARTIFACTS)
    expect(code, 'the canonical control').toMatch(/import \{ FilterMenu, type FilterSectionDef \} from '\.\.\/\.\.\/ui\/FilterMenu'/)
    expect(code, 'the page-local shadow is gone, not left beside it').not.toMatch(/function FilterMenu\(/)
    // The shadow's parts must go with it — a clean break, not a dead import.
    expect(code, 'the hand-rolled trigger/menu imports are gone').not.toMatch(/from '\.\.\/\.\.\/ui\/Popover'/)
  })

  it('renders exactly one pill, and the sort strip is gone from the toolbar', () => {
    const code = codeOf(ARTIFACTS)
    expect((code.match(/<FilterMenu /g) ?? []).length, 'one control, not two dropdowns').toBe(1)
    expect(code).toMatch(/<FilterMenu sections=\{filterSections\} \/>/)
    expect(code, 'sort moved INTO the pill').not.toMatch(/ariaLabel="Sort artifacts"/)
  })

  it('the three criteria are sections with the defaults the URL already used', () => {
    const code = codeOf(ARTIFACTS)
    const at = code.indexOf('const filterSections =')
    expect(at, 'the sections must be built here').toBeGreaterThan(-1)
    const fn = code.slice(at, code.indexOf('}, [artifacts, collections', at))
    // 🔑 `defaultKey` is what the active-count badge measures. Getting it wrong makes the badge
    // claim a filter is on at rest — worse than having no badge, which is what this page had.
    expect(fn).toMatch(/title: 'Source', value: src, defaultKey: '',/)
    expect(fn).toMatch(/title: 'Collection', value: col, defaultKey: '',/)
    expect(fn).toMatch(/title: 'Sort by', value: sort, defaultKey: 'updated',/)
    // The collection section only exists once a collection does — the old dropdown's own condition.
    expect(fn, 'a section for an empty set would be a dead row').toMatch(/if \(collections\.length > 0\) list\.push/)
  })

  it('the URL stays the filter state — the reason this toolbar is shareable', () => {
    const code = codeOf(ARTIFACTS)
    for (const key of ['src', 'col', 'sort']) {
      expect(code, `?${key} must still back its control`).toMatch(
        new RegExp(`useQueryParam\\(routeQuery, setQuery, '${key}'`))
    }
  })

  it('the kind strip stays a Segmented, with its collapse strategy intact', () => {
    // Recorded DISTINCTION. If a later pass folds this into the pill, it should have to delete this
    // assertion and say why.
    const code = codeOf(ARTIFACTS)
    expect(code).toMatch(/<Segmented ariaLabel="Artifact kind"[\s\S]{0,120}?collapse="scroll"/)
  })
})

describe('the family, so the tenth surface does not hand-roll its own', () => {
  it('nothing outside ui/ defines a component called FilterMenu', () => {
    const offenders = walk(SRC)
      .filter((abs) => !abs.includes(join('src', 'ui')))
      .filter((abs) => /function FilterMenu\(|const FilterMenu = /.test(codeOf(abs.slice(SRC.length + 1))))
      .map((abs) => abs.slice(SRC.length + 1))
    expect(offenders, `these shadow the canonical control:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the adopter count only grows', () => {
    const adopters = walk(SRC)
      .filter((abs) => /from '.*ui\/FilterMenu'/.test(codeOf(abs.slice(SRC.length + 1))))
      .map((abs) => abs.slice(SRC.length + 1))
      .sort()
    // Eight before this change, nine after. A surface that drops the primitive to line its own
    // widgets back up fails here.
    expect(adopters.length, `adopters:\n${adopters.join('\n')}`).toBeGreaterThanOrEqual(9)
    expect(adopters, 'artifacts is one of them now').toContain(ARTIFACTS)
  })
})
