import { describe, expect, it } from 'vitest'
import { queueLabel, queueSentence } from './queueStanding'

// Measured: an audio upload sat behind a watched folder's backlog for about three hours, and
// the page said "Queued…" the whole time — no place in line, no pace, nothing to decide on.

const NOW = 1_000_000

describe('queueLabel', () => {
  it('says where a waiting row stands, not that it is enriching', () => {
    expect(queueLabel({ processing_status: 'queued', queue: { state: 'waiting', lane: 'background', ahead: 12, running_since: NOW, typical_secs: null } })).toBe('Queued · 12 ahead')
    expect(queueLabel({ processing_status: 'queued', queue: { state: 'waiting', lane: 'yours', ahead: 0, running_since: NOW, typical_secs: null } })).toBe('Queued · next')
    expect(queueLabel({ processing_status: 'processing', queue: { state: 'running', since: NOW, typical_secs: null } })).toBe('Enriching')
  })

  it('falls back to the status alone when no running queue holds the item', () => {
    expect(queueLabel({ processing_status: 'queued', queue: null })).toBe('Queued')
    expect(queueLabel({ processing_status: 'processing' })).toBe('Enriching')
  })
})

describe('queueSentence', () => {
  it('states the place in line and the arithmetic of the estimate', () => {
    // 2 ahead at 60s an item, and the one being read started 20s ago: 40 + 3 × 60 = 220s.
    expect(queueSentence({ state: 'waiting', lane: 'yours', ahead: 2, running_since: NOW - 20, typical_secs: 60 }, NOW))
      .toBe('2 items ahead of it. At the recent pace (about 1 min an item), done in roughly 4 min.')
  })

  it('says background work waits behind what she adds herself', () => {
    expect(queueSentence({ state: 'waiting', lane: 'background', ahead: 1, running_since: null, typical_secs: null }, NOW))
      .toBe('1 item ahead of it. It came in as background work, so anything you add yourself is read first. '
        + 'No timing yet: too few items have finished to say how long each takes.')
  })

  it('never invents a pace it has not measured', () => {
    const s = queueSentence({ state: 'waiting', lane: 'yours', ahead: 0, running_since: NOW - 5, typical_secs: null }, NOW)
    expect(s).toBe('Next in line, after the item being read now. No timing yet: too few items have finished to say how long each takes.')
    expect(s).not.toMatch(/roughly/)
  })

  it('tells a running item how long it has been going against the recent pace', () => {
    expect(queueSentence({ state: 'running', since: NOW - 30, typical_secs: 90 }, NOW))
      .toBe('Being read now, started under a minute ago. Recent items took about 2 min, so roughly 1 min to go.')
    expect(queueSentence({ state: 'running', since: NOW - 600, typical_secs: 90 }, NOW))
      .toBe('Being read now, started 10 min ago. Recent items took about 2 min; this one is taking longer.')
  })

  it('says nothing for an item no queue holds', () => {
    expect(queueSentence(null, NOW)).toBe('')
    expect(queueSentence(undefined, NOW)).toBe('')
  })
})
