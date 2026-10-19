import { describe, expect, it } from 'vitest'
import { modeQueryPatch, taskModeFromQuery } from './taskModeInUrl'

// ── A chat's task mode is in its address ───────────────────────────────────────────────────────
//
// `?mode=<agent|ask|plan|build>` records the chat's task mode, so a reload, a copied address and a
// `personalclaw://threads/new?mode=plan` link open a new thread in that mode, while an existing
// thread keeps its own. Only what the person can see is written: a new thread starts in the
// address's mode; an existing thread keeps the mode it has, and the address is corrected to it
// once the thread's first read is in. Nothing here writes a mode to the gateway, so a link never
// changes how a chat runs.

describe('taskModeFromQuery', () => {
  it('reads each task mode the chat has', () => {
    for (const mode of ['agent', 'ask', 'plan', 'build'] as const) expect(taskModeFromQuery({ mode })).toBe(mode)
  })

  it('reads nothing from an address without one, or with one the chat does not have', () => {
    const queries: Record<string, string>[] = [{}, { mode: '' }, { mode: 'code' }, { mode: 'Plan' }, { mode: 'yolo' }, { mode: 'plan ' }]
    // Names every object has are not modes either.
    for (const mode of ['__proto__', 'constructor', 'toString', 'hasOwnProperty', 'valueOf']) queries.push({ mode })
    for (const query of queries) {
      expect(taskModeFromQuery(query), JSON.stringify(query)).toBeNull()
    }
  })
})

describe('modeQueryPatch', () => {
  it('writes the mode once the chat knows it, and drops the key for the default', () => {
    expect(modeQueryPatch({ query: {}, taskMode: 'plan', postureKnown: true })).toEqual({ mode: 'plan' })
    expect(modeQueryPatch({ query: { mode: 'plan' }, taskMode: 'agent', postureKnown: true })).toEqual({ mode: null })
  })

  it('writes nothing when the address already says it', () => {
    expect(modeQueryPatch({ query: { mode: 'ask' }, taskMode: 'ask', postureKnown: true })).toBeNull()
    expect(modeQueryPatch({ query: {}, taskMode: 'agent', postureKnown: true })).toBeNull()
    expect(modeQueryPatch({ query: { mode: '' }, taskMode: 'agent', postureKnown: true }), 'an empty mode= is no key').toBeNull()
  })

  it('corrects a mode the chat does not have', () => {
    expect(modeQueryPatch({ query: { mode: 'code' }, taskMode: 'agent', postureKnown: true })).toEqual({ mode: null })
  })

  it('writes nothing before a thread\'s first read is in: its mode is not known yet', () => {
    expect(modeQueryPatch({ query: { mode: 'plan' }, taskMode: 'agent', postureKnown: false })).toBeNull()
    expect(modeQueryPatch({ query: {}, taskMode: 'build', postureKnown: false })).toBeNull()
  })
})
