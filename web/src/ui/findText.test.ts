import { describe, it, expect } from 'vitest'
import { findInText, hasMatch, matchingIndices } from './findText'

// The matcher behind `ui/FindBar`. These cases arrived from `pages/chat/findMatches.test.ts`
// when the find bar was promoted to a shared primitive (KL-16): they were always about
// TEXT, and only ever wore chat's `{ turnIndex, segIndex, start, end }` coordinates
// because that was the only caller. Offsets are asserted at the same values as before —
// the `{ turnIndex: 0, segIndex: 0 }` wrapper is what went away.

describe('findInText', () => {
  it('empty / whitespace query yields no matches', () => {
    expect(findInText('hello world', '')).toEqual([])
    expect(findInText('hello world', '   ')).toEqual([])
  })

  it('is case-insensitive', () => {
    expect(findInText('Docker Compose', 'docker')).toEqual([{ start: 0, end: 6 }])
  })

  it('finds multiple non-overlapping matches in one string', () => {
    expect(findInText('aXaXa', 'a').map((m) => m.start)).toEqual([0, 2, 4])
  })

  it('non-overlapping: "aa" over "aaaa" → two matches', () => {
    expect(findInText('aaaa', 'aa').map((m) => m.start)).toEqual([0, 2])
  })

  // #546: offsets were computed on a `toLowerCase()` copy and applied to the
  // original. `İ` (U+0130) is the only code point whose lowercase is LONGER
  // (1 → 2 units), so every later offset drifted — and a drifted offset handed to
  // `Range.setEnd` threw, aborting the find bar's whole paint loop (zero highlights).
  describe('case folding that changes length (İ, U+0130)', () => {
    it('İİİİtarget → one match at 4..10, not the folded 8..14', () => {
      expect(findInText('İİİİtarget', 'target')).toEqual([{ start: 4, end: 10 }])
    })

    it('a single İ prefix shifts by one: İtarget → 1..7', () => {
      expect(findInText('İtarget', 'target')).toEqual([{ start: 1, end: 7 }])
    })

    it('İ after the match still works', () => {
      expect(findInText('targetİ', 'target')).toEqual([{ start: 0, end: 6 }])
    })

    it('İ mid-text: real-world "İzmir kiln target 1240C" → 11..17', () => {
      const text = 'İzmir kiln target 1240C'
      expect(findInText(text, 'target')).toEqual([{ start: 11, end: 17 }])
      expect(text.slice(11, 17)).toBe('target')
    })

    it('an İ in the QUERY matches the İ in the text', () => {
      expect(findInText('go to İzmir', 'İzmir')).toEqual([{ start: 6, end: 11 }])
    })

    it('never emits an offset past the end of the text', () => {
      for (const n of [1, 2, 3, 5, 10]) {
        const text = 'İ'.repeat(n) + 'target'
        const m = findInText(text, 'target')
        expect(m).toHaveLength(1)
        expect(m[0].start).toBe(n)
        expect(m[0].end).toBe(text.length)
        expect(m[0].end).toBeLessThanOrEqual(text.length)
      }
    })

    // The invariant that was violated: the reported span, sliced out of the
    // ORIGINAL text, is the searched term.
    it('invariant: original.slice(start, end) is the term, case-insensitively', () => {
      const cases: Array<[string, string]> = [
        ['İİİİtarget', 'target'],
        ['İtarget', 'TARGET'],
        ['targetİ', 'target'],
        ['İzmir kiln target 1240C', 'Target'],
        ['İstanbul', 'stan'],
        ['Docker Compose', 'docker'],
        ['ﬀ İ ﬀ target', 'target'],
      ]
      for (const [text, query] of cases) {
        const m = findInText(text, query)
        expect(m.length).toBeGreaterThan(0)
        for (const { start, end } of m) {
          expect(end).toBeLessThanOrEqual(text.length)
          expect(text.slice(start, end).toLowerCase()).toBe(query.toLowerCase())
        }
      }
    })

    it('uppercasing is not a fix either: ß stays one character (ß → SS)', () => {
      const text = 'Straße heizt'
      expect(findInText(text, 'straße')).toEqual([{ start: 0, end: 6 }])
      expect(text.slice(0, 6)).toBe('Straße')
    })

    it('keeps surrogate pairs intact (no offset inside an astral char)', () => {
      const text = '😀target'
      expect(findInText(text, 'target')).toEqual([{ start: 2, end: 8 }])
      expect(text.slice(2, 8)).toBe('target')
    })
  })
})

describe('hasMatch is findInText without the offsets — the SAME folding', () => {
  // The counter asks "does this segment match?" once per segment per keystroke and the
  // painter asks "where?"; two folders is how the two halves drifted apart in #546. So
  // the cheap boolean has to be provably the expensive list's `.length > 0`.
  const CASES: Array<[string, string]> = [
    ['Docker Compose', 'docker'],
    ['İİİİtarget', 'target'],
    ['Straße heizt', 'straße'],
    ['😀target', 'target'],
    ['ΟΔΟΣ Ερμού', 'οδος'],   // whole-string toLowerCase gives final sigma; per-code-point does not
    ['ΟΔΟΣ Ερμού', 'οδοσ'],   // …so exactly one of these two spellings may match — the same one, both ways
    ['hello world', 'zebra'],
    ['hello world', '   '],
    ['', 'x'],
  ]

  it('agrees with findInText on every case', () => {
    for (const [text, query] of CASES) {
      expect(hasMatch(text, query), `hasMatch(${JSON.stringify(text)}, ${JSON.stringify(query)})`)
        .toBe(findInText(text, query).length > 0)
    }
  })

  it('is not vacuously green: the table contains both answers, and the sigma trap', () => {
    // A table of all-true (or all-false) rows would pass the agreement test with a
    // `hasMatch` hard-coded to a constant.
    const answers = CASES.map(([t, q]) => hasMatch(t, q))
    expect(answers.filter(Boolean).length, 'positive controls').toBeGreaterThanOrEqual(4)
    expect(answers.filter((a) => !a).length, 'negative controls').toBeGreaterThanOrEqual(3)
    // And the trap itself, stated: per-code-point folding does NOT produce final sigma,
    // so 'οδοσ' is the spelling that matches and 'οδος' is the one that does not. A
    // `hasMatch` written as `text.toLowerCase().includes(...)` flips both of these.
    expect(hasMatch('ΟΔΟΣ Ερμού', 'οδοσ')).toBe(true)
    expect(hasMatch('ΟΔΟΣ Ερμού', 'οδος')).toBe(false)
  })
})

describe('matchingIndices — the stops the arrows can make', () => {
  // Driven with a shape that is not a chat turn and not an article section: the ordering
  // contract belongs to neither.
  const rows = [
    { title: 'Kiln schedule', body: 'cone 6 target 1240C' },
    { title: 'Glaze notes', body: 'nothing here' },
    { title: 'Target list', body: 'target twice: target' },
  ]
  const segmentsOf = (r: { title: string; body: string }) => [r.title, r.body]

  it('returns matching item indices in order', () => {
    expect(matchingIndices(rows, segmentsOf, 'target')).toEqual([0, 2])
  })

  it('reports an item ONCE however many times it matches', () => {
    // Row 2 matches in its title and twice in its body — one stop, not three.
    expect(matchingIndices([rows[2]], segmentsOf, 'target')).toEqual([0])
  })

  it('empty / whitespace query selects nothing', () => {
    expect(matchingIndices(rows, segmentsOf, '')).toEqual([])
    expect(matchingIndices(rows, segmentsOf, '  ')).toEqual([])
  })

  it('an empty item list is not an error', () => {
    expect(matchingIndices([], segmentsOf, 'target')).toEqual([])
  })
})
