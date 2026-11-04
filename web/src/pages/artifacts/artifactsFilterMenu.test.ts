// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../../test/sourceTree'

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
// Then every page-level control moved into the page header (`app/pageControlsAreInTheHeader.test.ts`),
// and with it the rule that a header holds ONE view switch and every other choice set is a section of
// its filter. The 17-kind strip was a choice set, not this page's view, so it is the Kind section now,
// first, so the filter's label reads "Show: All kinds" and names the kind whenever one is picked.
// `DeployedAppsMenu` stays its own control: it lists served apps, it is not a filter.
//
// 🪤 AND THE NAME `FilterMenu` IS NOT THE FAMILY. The duplicate-name scan also paired
// `pages/dashboard/widgets/SystemHealth.tsx:Spark` with `ui/Spark.tsx:Spark` — which are a CPU
// sparkline and the brand claw mark. Same name, unrelated concepts, nothing to converge. Read both
// bodies before calling two components a duplicate; this rail asserts only what was measured.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] => filesUnder(d, (n) => /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n))
const codeOf = (rel: string) => readSource(join(SRC, rel))
  .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const ARTIFACTS = 'pages/artifacts/ArtifactsSection.tsx'
/** The pages drawing the header filter when every list page's filter moved into its header. */
const ADOPTERS = 10

describe('the artifacts header holds one filter, every criterion a section of it', () => {
  it('imports the header filter and defines no shadow of it', () => {
    const code = codeOf(ARTIFACTS)
    expect(code, 'the canonical control').toMatch(/import \{ HeaderFilter \} from '\.\.\/\.\.\/ui\/HeaderFilter'/)
    expect(code, 'no page-local shadow').not.toMatch(/function (FilterMenu|HeaderFilter)\(/)
    expect(code, 'no hand-rolled trigger or menu').not.toMatch(/from '\.\.\/\.\.\/ui\/Popover'/)
  })

  it('renders exactly one filter, and no strip or sort control beside it', () => {
    const code = codeOf(ARTIFACTS)
    expect((code.match(/<HeaderFilter /g) ?? []).length, 'one control, not two dropdowns').toBe(1)
    expect(code).toMatch(/<HeaderFilter sections=\{filterSections\} \/>/)
    expect(code, 'sort is a section').not.toMatch(/ariaLabel="Sort artifacts"/)
    expect(code, 'the kind is a section, not a strip').not.toMatch(/<Segmented /)
  })

  it('the four criteria are sections with the defaults the URL already used', () => {
    const code = codeOf(ARTIFACTS)
    const at = code.indexOf('const filterSections =')
    expect(at, 'the sections must be built here').toBeGreaterThan(-1)
    const fn = code.slice(at, code.indexOf('}, [artifacts, collections', at))
    // 🔑 `defaultKey` is what the filter's set mark measures. Getting it wrong makes it claim a
    // filter is on at rest.
    expect(fn).toMatch(/title: 'Kind', value: kind, defaultKey: '',/)
    expect(fn).toMatch(/title: 'Source', value: src, defaultKey: '',/)
    expect(fn).toMatch(/title: 'Collection', value: col, defaultKey: '',/)
    expect(fn).toMatch(/title: 'Sort by', value: sort, defaultKey: 'updated',/)
    // The collection section only exists once a collection does — the old dropdown's own condition.
    expect(fn, 'a section for an empty set would be a dead row').toMatch(/if \(collections\.length > 0\) list\.push/)
  })

  it('the URL stays the filter state — the reason this library is shareable', () => {
    const code = codeOf(ARTIFACTS)
    for (const key of ['kind', 'src', 'col', 'sort']) {
      expect(code, `?${key} must still back its control`).toMatch(
        new RegExp(`useQueryParam\\(routeQuery, setQuery, '${key}'`))
    }
  })
})

describe('the family, so the tenth surface does not hand-roll its own', () => {
  it('nothing outside ui/ defines a component called FilterMenu or HeaderFilter', () => {
    const offenders = walk(SRC)
      .filter((abs) => !abs.includes(join('src', 'ui')))
      .filter((abs) => /function (FilterMenu|HeaderFilter)\(|const (FilterMenu|HeaderFilter) = /.test(codeOf(abs.slice(SRC.length + 1))))
      .map((abs) => abs.slice(SRC.length + 1))
    expect(offenders, `these shadow the canonical control:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the adopter count only grows', () => {
    const adopters = walk(SRC)
      .filter((abs) => /from '.*ui\/HeaderFilter'/.test(codeOf(abs.slice(SRC.length + 1))))
      .map((abs) => abs.slice(SRC.length + 1))
      .sort()
    // Every list page's filter is the header's now. A surface that drops it to line its own
    // widgets back up fails here.
    expect(adopters.length, `adopters:\n${adopters.join('\n')}`).toBeGreaterThanOrEqual(ADOPTERS)
    expect(adopters, 'artifacts is one of them').toContain(ARTIFACTS)
  })
})
