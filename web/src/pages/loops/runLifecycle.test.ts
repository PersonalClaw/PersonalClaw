import { describe, it, expect } from 'vitest'
import { RUN_LIFECYCLE } from './useRunStream'

// EventSource silently DROPS event types with no registered listener, and `useRunStream` builds
// its listeners by iterating THIS const. So a member missing from the union is not a bug you can
// see — it is a live update that never arrives. This test pins the membership the plan-review
// surface depends on, so a refactor of the array can't silently drop one.
describe('RUN_LIFECYCLE union membership', () => {
  it('carries the UNIVERSAL-PLANNING plan-review events (WF2UNI-10)', () => {
    for (const ev of ['plan_streaming', 'revision', 'confirmation', 'demotion'] as const) {
      expect(RUN_LIFECYCLE).toContain(ev)
    }
  })

  it('has no duplicate members (a dup double-registers a listener)', () => {
    expect(new Set(RUN_LIFECYCLE).size).toBe(RUN_LIFECYCLE.length)
  })
})
