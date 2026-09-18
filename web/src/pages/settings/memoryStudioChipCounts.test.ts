import { describe, expect, it } from 'vitest'
import { studioScope } from './MemoryPanel'

// ── A CHIP AND THE LIST BESIDE IT MUST NOT DISAGREE (issue #520) ──────────────────────────────────
//
// Settings › Memory › Studio has two filter dimensions: a "Search memories" box and a row of kind
// chips carrying counts. The list obeyed both; the counts obeyed NEITHER, because they were memoized
// on `[items]` alone. Measured on a real library: typing `bell_times` narrowed the list from 138 rows
// to 25 while the chips kept reading `All 238 · Facts 138 · Episodes 85 · Lessons 12 · Documents 3`,
// and a query matching nothing left them saying exactly the same thing over "No matching memories".
//
// A count is only worth putting on a chip if it answers "where do my hits live" — otherwise
// `Lessons 12` is an invitation to click into an empty pane. So the SEARCH belongs in the counts'
// scope and the KIND does not: a chip that counted its own filter would report its own total on every
// other chip. That is the rule Inbox's `filterCount` already states, and this suite pins it here.

type Item = Parameters<typeof studioScope>[0][number]

const item = (kind: Item['kind'], title: string, preview = ''): Item =>
  ({ uid: `${kind}:${title}`, kind, title, preview, ref: null })

// 3 facts + 2 episodes match "bell", 1 lesson and 1 doc do not.
const LIBRARY: Item[] = [
  item('fact', 'bell_times.monday'),
  item('fact', 'bell_times.tuesday'),
  item('fact', 'unrelated.key', 'mentions bell in the preview'),
  item('fact', 'nothing.here', 'nor here'),
  item('episodic', 'the bell rang'),
  item('episodic', 'a quiet morning', 'someone rang the bell'),
  item('episodic', 'no mention at all'),
  item('lesson', 'prefer ruff over flake8'),
  item('doc', 'Preferences'),
]

describe('kind-chip counts respect the search box', () => {
  it('counts only the SEARCH hits, per kind', () => {
    const { counts } = studioScope(LIBRARY, 'bell', 'all')
    expect(counts).toMatchObject({ all: 5, fact: 3, episodic: 2, lesson: 0, doc: 0 })
  })

  it('reads ZERO everywhere when the search matches nothing', () => {
    // The worst case and the one that shipped: chips claiming the full library above "No matching
    // memories", so every chip was a dead control that looked live.
    const { counts, shown } = studioScope(LIBRARY, 'zzzznope99', 'all')
    expect(shown).toHaveLength(0)
    expect(Object.values(counts).every((n) => n === 0)).toBe(true)
  })

  it('keeps `all` equal to the rendered row count while no kind is picked', () => {
    for (const q of ['', 'bell', 'bell_times', 'zzzznope99']) {
      const { counts, shown } = studioScope(LIBRARY, q, 'all')
      expect(counts.all).toBe(shown.length)
    }
  })

  it('keeps the ACTIVE chip equal to the rendered row count', () => {
    // The chip the user clicked is the one they can check against the list, so it is the one that
    // must match exactly.
    const { counts, shown } = studioScope(LIBRARY, 'bell', 'fact')
    expect(counts.fact).toBe(shown.length)
    expect(shown.every((it) => it.kind === 'fact')).toBe(true)
  })

  it('leaves the other chips’ counts unchanged by the picked kind', () => {
    // Kind is deliberately outside the counting scope: picking Facts must not zero Episodes, or the
    // chips stop being a map of where the hits are and become a report on the current selection.
    const all = studioScope(LIBRARY, 'bell', 'all').counts
    const scoped = studioScope(LIBRARY, 'bell', 'fact').counts
    expect(scoped).toEqual(all)
  })

  it('counts the whole library when nothing is typed', () => {
    const { counts, shown } = studioScope(LIBRARY, '   ', 'all')
    expect(counts.all).toBe(LIBRARY.length)
    expect(shown).toHaveLength(LIBRARY.length)
  })

  it('matches the preview as well as the title, in both dimensions', () => {
    // The list already searched both fields; counts derived from a title-only match would disagree
    // with it in the other direction.
    const { counts, shown } = studioScope(LIBRARY, 'in the preview', 'all')
    expect(counts.all).toBe(1)
    expect(shown[0].title).toBe('unrelated.key')
  })
})
