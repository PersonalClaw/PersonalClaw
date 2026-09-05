/** The "Bundled" source filter finds the bundled prompts (#299).
 *
 *  Companion to `bundledProvenanceLabel.test.ts`, which fixed what the source PILL says. The list
 *  page kept filtering and sorting on the RAW `source` field while rendering the resolved label two
 *  columns over — so every row's badge read "bundled" and the "Bundled" filter returned nothing.
 *  One screen, two answers, again.
 *
 *  Measured on `origin/main` by running the real seeder against a temp home:
 *
 *      42 prompts  -> {'user': 42}
 *      44 snippets -> {'user': 44}
 *      a Bundled filter would show: NOTHING
 *
 *  🪤 THE TEMPTING FIX IS THE SAME REGRESSION the sibling test guards. Making the backend report
 *  `source: 'bundled'` would make the filter work AND flip all 86 shipped records to read-only,
 *  because `isReadOnly` is `source !== 'user'`. The `'user'` stamp is load-bearing: it is what keeps
 *  a seeded prompt editable, which is the whole point of seeding it to disk. Provenance lives in the
 *  tags — so the FILTER has to ask the same question the LABEL already answers.
 */
import { describe, it, expect } from 'vitest'
import { applyView } from './PromptsListPage'
import { isReadOnly, sourceLabel } from './promptMeta'

type Row = Parameters<typeof applyView>[0][number]

/** A shipped prompt exactly as the seeder writes it: `source` absent from the file (the payload
 *  strips it when it equals 'user'), provenance carried in the tags. */
const shipped = (name: string, over: Partial<Row> = {}): Row =>
  ({ name, title: name, tags: ['system', 'bundled'], variables: [], ...over }) as Row

/** One the user actually wrote. */
const mine = (name: string, over: Partial<Row> = {}): Row =>
  ({ name, title: name, tags: ['mine'], variables: [], ...over }) as Row

const names = (rows: Row[]) => rows.map((r) => r.name)

describe('the Bundled filter', () => {
  it('🔑 finds a shipped prompt whose stored source is "user"', () => {
    const rows = [shipped('inbox_classify'), mine('my-draft')]
    expect(names(applyView(rows, '', 'name', 'bundled'))).toEqual(['inbox_classify'])
  })

  it('does not sweep up a prompt the user wrote', () => {
    const rows = [shipped('inbox_classify'), mine('my-draft')]
    expect(names(applyView(rows, '', 'name', 'user'))).toEqual(['my-draft'])
  })

  it('a marketplace prompt is neither user nor bundled', () => {
    const rows = [shipped('a'), mine('b'), { ...mine('c'), source: 'marketplace' } as Row]
    expect(names(applyView(rows, '', 'name', 'marketplace'))).toEqual(['c'])
    expect(names(applyView(rows, '', 'name', 'bundled'))).toEqual(['a'])
    expect(names(applyView(rows, '', 'name', 'user'))).toEqual(['b'])
  })

  it('"all" still returns everything', () => {
    const rows = [shipped('a'), mine('b')]
    expect(names(applyView(rows, '', 'name', 'all'))).toEqual(['a', 'b'])
  })

  it('🪤 vacuity floor — a filter that ignored its argument would pass the first case alone', () => {
    // Every partition is disjoint and together they account for every row: a fix that returned
    // everything, or that hard-coded 'bundled', breaks one of these.
    const rows = [shipped('a'), mine('b'), { ...mine('c'), source: 'marketplace' } as Row]
    const parts = ['user', 'bundled', 'marketplace'].map((k) => names(applyView(rows, '', 'name', k)))
    expect(parts).toEqual([['b'], ['a'], ['c']])
    expect(parts.flat().sort()).toEqual(['a', 'b', 'c'])
  })
})

describe('sorting by source', () => {
  it('groups by the resolved provenance, not the raw field', () => {
    // On the raw field every shipped record compares equal to every user one, so the "Source" sort
    // was a no-op that fell through to the name tiebreak — indistinguishable from sorting by name.
    const rows = [mine('z-user'), shipped('a-shipped')]
    expect(names(applyView(rows, '', 'source', 'all'))).toEqual(['a-shipped', 'z-user'])
  })

  it('the sort is genuinely by source, not name', () => {
    // 🪤 The floor for the case above: with names chosen so name-order and source-order DISAGREE,
    // a raw-field sort (or a name sort) puts 'a-user' first; the resolved sort puts bundled first.
    const rows = [mine('a-user'), shipped('z-shipped')]
    expect(names(applyView(rows, '', 'source', 'all'))).toEqual(['z-shipped', 'a-user'])
  })
})

describe('the line this must not cross', () => {
  it('a shipped prompt stays editable', () => {
    // Restated here, not just in the sibling test: this change is one edit away from the regression
    // that test guards, and a reader of THIS file needs to know why the filter resolves instead of
    // the backend relabelling.
    expect(isReadOnly(shipped('x').source)).toBe(false)
    expect(sourceLabel(shipped('x').source, shipped('x').tags)).toBe('bundled')
  })

  it('the filter and the badge cannot disagree', () => {
    // The actual invariant: for every row, the value the filter matches on IS the value the badge
    // shows. Asserted over a mixed set so a future change to either one reds here.
    const rows = [shipped('a'), mine('b'), { ...mine('c'), source: 'marketplace' } as Row]
    for (const r of rows) {
      const badge = sourceLabel(r.source, r.tags)
      expect(names(applyView(rows, '', 'name', badge))).toContain(r.name)
    }
  })
})
