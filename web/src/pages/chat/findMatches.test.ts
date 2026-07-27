import { describe, it, expect } from 'vitest'
import { findMatches, findSegments } from './findMatches'
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
