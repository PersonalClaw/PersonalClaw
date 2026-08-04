import { describe, it, expect } from 'vitest'
import { findInText, findMatches, findSegments } from './findMatches'
import type { ChatTurn } from './chatTypes'

const userTurn = (text: string): ChatTurn => ({ role: 'user', segments: [{ kind: 'text', text }] })
const asstTurn = (...texts: string[]): ChatTurn => ({ role: 'assistant', segments: texts.map((t) => ({ kind: 'text' as const, text: t })) })
const toolTurn = (tool: string, detail?: string): ChatTurn => ({ role: 'assistant', segments: [{ kind: 'tool', id: 't', tool, detail, done: true }] })

describe('findSegments', () => {
  it('extracts text segments and tool titles in render order', () => {
    const t: ChatTurn = { role: 'assistant', segments: [
      { kind: 'text', text: 'before' },
      { kind: 'tool', id: 't1', tool: 'Terminal', detail: 'docker ps', done: true },
      { kind: 'text', text: 'after' },
    ] }
    expect(findSegments(t)).toEqual(['before', 'Terminal docker ps', 'after'])
  })

  it('keeps index alignment for non-searchable segments (approval → empty string)', () => {
    const t: ChatTurn = { role: 'assistant', segments: [
      { kind: 'approval', id: 'a', tool: 'Write' },
      { kind: 'text', text: 'hi' },
    ] }
    expect(findSegments(t)).toEqual(['', 'hi'])
  })
})

describe('findMatches', () => {
  it('empty / whitespace query yields no matches', () => {
    const turns = [userTurn('hello world')]
    expect(findMatches(turns, '')).toEqual([])
    expect(findMatches(turns, '   ')).toEqual([])
  })

  it('is case-insensitive', () => {
    const turns = [userTurn('Docker Compose')]
    const m = findMatches(turns, 'docker')
    expect(m).toHaveLength(1)
    expect(m[0]).toEqual({ turnIndex: 0, segIndex: 0, start: 0, end: 6 })
  })

  it('finds multiple non-overlapping matches within one segment', () => {
    const turns = [userTurn('aXaXa')]
    const m = findMatches(turns, 'a')
    expect(m.map((x) => x.start)).toEqual([0, 2, 4])
  })

  it('non-overlapping: "aa" over "aaaa" → two matches', () => {
    const turns = [userTurn('aaaa')]
    const m = findMatches(turns, 'aa')
    expect(m.map((x) => x.start)).toEqual([0, 2])
  })

  it('scans across turns and segments in reading order', () => {
    const turns = [userTurn('cat'), asstTurn('a cat', 'no'), toolTurn('Read', 'cat.txt')]
    const m = findMatches(turns, 'cat')
    expect(m).toEqual([
      { turnIndex: 0, segIndex: 0, start: 0, end: 3 },
      { turnIndex: 1, segIndex: 0, start: 2, end: 5 },
      { turnIndex: 2, segIndex: 0, start: 5, end: 8 }, // "Read cat.txt" → cat at offset 5
    ])
  })

  // #546: offsets were computed on a `toLowerCase()` copy and applied to the
  // original. `İ` (U+0130) is the only code point whose lowercase is LONGER
  // (1 → 2 units), so every later offset drifted — and a drifted offset handed to
  // `Range.setEnd` threw, aborting FindBar's whole paint loop (zero highlights).
  describe('case folding that changes length (İ, U+0130)', () => {
    it('İİİİtarget → one match at 4..10, not the folded 8..14', () => {
      const m = findMatches([userTurn('İİİİtarget')], 'target')
      expect(m).toHaveLength(1)
      expect(m[0]).toEqual({ turnIndex: 0, segIndex: 0, start: 4, end: 10 })
    })

    it('a single İ prefix shifts by one: İtarget → 1..7', () => {
      const m = findMatches([userTurn('İtarget')], 'target')
      expect(m).toEqual([{ turnIndex: 0, segIndex: 0, start: 1, end: 7 }])
    })

    it('İ after the match still works', () => {
      const m = findMatches([userTurn('targetİ')], 'target')
      expect(m).toEqual([{ turnIndex: 0, segIndex: 0, start: 0, end: 6 }])
    })

    it('İ mid-text: real-world "İzmir kiln target 1240C" → 11..17', () => {
      const text = 'İzmir kiln target 1240C'
      const m = findMatches([userTurn(text)], 'target')
      expect(m).toEqual([{ turnIndex: 0, segIndex: 0, start: 11, end: 17 }])
      expect(text.slice(11, 17)).toBe('target')
    })

    it('an İ in the QUERY matches the İ in the text', () => {
      const m = findMatches([userTurn('go to İzmir')], 'İzmir')
      expect(m).toEqual([{ turnIndex: 0, segIndex: 0, start: 6, end: 11 }])
    })

    it('never emits an offset past the end of the text', () => {
      for (const n of [1, 2, 3, 5, 10]) {
        const text = 'İ'.repeat(n) + 'target'
        const m = findMatches([userTurn(text)], 'target')
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
      const m = findInText(text, 'straße')
      expect(m).toEqual([{ start: 0, end: 6 }])
      expect(text.slice(0, 6)).toBe('Straße')
    })

    it('keeps surrogate pairs intact (no offset inside an astral char)', () => {
      const text = '😀target'
      const m = findInText(text, 'target')
      expect(m).toEqual([{ start: 2, end: 8 }])
      expect(text.slice(2, 8)).toBe('target')
    })
  })

  it('is fast on a 500-turn fixture', () => {
    const turns: ChatTurn[] = Array.from({ length: 500 }, (_, i) =>
      asstTurn(`turn ${i} — the quick brown fox jumps over the lazy dog repeatedly `.repeat(6)))
    const t0 = performance.now()
    const m = findMatches(turns, 'fox')
    const dt = performance.now() - t0
    expect(m.length).toBe(500 * 6)
    expect(dt).toBeLessThan(50) // generous ceiling; typically <10ms
  })
})
