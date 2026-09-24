import { describe, it, expect } from 'vitest'
import { findSegments } from './findSegments'
import { matchingIndices } from '../../ui/findText'
import type { ChatTurn } from './chatTypes'

// The chat half of find-in-conversation. The matcher itself (case folding, offsets,
// which items match and in what order) is `ui/findText.test.ts` — it moved there with
// `ui/FindBar` when the bar was promoted to a shared primitive (KL-16), because none of
// it was ever about turns. What is asserted here is the composition chat actually ships:
// `matchingIndices(turns, findSegments, query)`.

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

describe('the chat binding: matchingIndices over findSegments', () => {
  it('empty / whitespace query matches no turn', () => {
    const turns = [userTurn('hello world')]
    expect(matchingIndices(turns, findSegments, '')).toEqual([])
    expect(matchingIndices(turns, findSegments, '   ')).toEqual([])
  })

  it('scans turns in reading order and reports each matching turn ONCE', () => {
    // Was `findMatches`' "reading order" case. The bar cycles turns, not occurrences,
    // so turn 1 — which matches in two of its segments — is one stop, not two.
    const turns = [userTurn('cat'), asstTurn('a cat', 'cat again'), userTurn('dog'), toolTurn('Read', 'cat.txt')]
    expect(matchingIndices(turns, findSegments, 'cat')).toEqual([0, 1, 3])
  })

  it('is case-insensitive through the composition, not just in the matcher', () => {
    expect(matchingIndices([userTurn('Docker Compose')], findSegments, 'docker')).toEqual([0])
  })

  it('a query straddling two segments matches nothing — the seam is real', () => {
    // Why findSegments returns a LIST and not a joined string: 'before' and 'after' are
    // separate segments, so "before after" is not findable, and must not be counted as a
    // stop the user could scroll to.
    const t = asstTurn('before', 'after')
    expect(matchingIndices([t], findSegments, 'before')).toEqual([0])
    expect(matchingIndices([t], findSegments, 'before after')).toEqual([])
  })

  it('is fast on a 500-turn fixture', () => {
    const turns: ChatTurn[] = Array.from({ length: 500 }, (_, i) =>
      asstTurn(`turn ${i} — the quick brown fox jumps over the lazy dog repeatedly `.repeat(6)))
    const t0 = performance.now()
    const idx = matchingIndices(turns, findSegments, 'fox')
    const dt = performance.now() - t0
    // Correctness is the real assertion — every turn is found (this fixture's text puts
    // "fox" in all 500). The pre-promotion version asserted 3000 occurrence coordinates;
    // the production question is now "which turns can I jump to", so it asserts 500 stops
    // over the identical fixture.
    expect(idx.length).toBe(500)
    // Regression guard, not a microbenchmark: the op is typically <10ms, so a real
    // O(n²) blow-up on 500 turns would land in the seconds. The ceiling is a loaded-
    // CI-realistic bound (a 50ms bound flaked at ~50.5ms on contended shared runners —
    // a false red with no regression signal); 250ms keeps ~25× headroom while still
    // catching an algorithmic regression.
    expect(dt).toBeLessThan(250)
  })
})
