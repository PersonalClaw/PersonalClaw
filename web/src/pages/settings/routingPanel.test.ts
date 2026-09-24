import { describe, it, expect } from 'vitest'
import { fmtPct, fmtFeedback, fmtMs, fmtCost, sortByFrontier } from './RoutingPanel'
import type { TelemetryRow } from '../../lib/api'

const R = (ref: string, on_frontier: boolean, extra: Partial<TelemetryRow> = {}): TelemetryRow =>
  ({ ref, n: 1, success: 1, feedback: 0, avg_cost_usd: 0, p50_ms: 0, p95_ms: 0, on_frontier, ...extra })

describe('fmtPct', () => {
  it('renders a 0..1 fraction as a whole percent', () => {
    expect(fmtPct(0.93)).toBe('93%')
    expect(fmtPct(1)).toBe('100%')
    expect(fmtPct(0)).toBe('0%')
  })
  it('rounds to the nearest whole percent', () => {
    expect(fmtPct(0.716)).toBe('72%')
  })
})

describe('fmtFeedback', () => {
  it('renders present feedback as a percent', () => {
    expect(fmtFeedback(0.71)).toBe('71%')
  })
  it('renders an em-dash when there is no feedback signal (0/absent), never a real "0%"', () => {
    expect(fmtFeedback(0)).toBe('—')
  })
})

describe('fmtMs', () => {
  it('rounds and thousands-groups a latency sample', () => {
    expect(fmtMs(6800)).toBe('6,800')
    expect(fmtMs(2100.4)).toBe('2,100')
  })
  it('renders an em-dash when there are no samples (backend reports 0)', () => {
    expect(fmtMs(0)).toBe('—')
  })
})

describe('fmtCost', () => {
  it('renders a local/zero-cost model as "free", never $0.00', () => {
    expect(fmtCost(0)).toBe('free')
  })
  it('renders sub-dollar cost at 4dp and dollar+ at 2dp', () => {
    expect(fmtCost(0.0021)).toBe('$0.0021')
    expect(fmtCost(1.5)).toBe('$1.50')
  })
})

describe('sortByFrontier', () => {
  it('floats frontier rows to the top, keeping the backend order within each group', () => {
    const rows = [R('a', false), R('b', true), R('c', false), R('d', true)]
    expect(sortByFrontier(rows).map((r) => r.ref)).toEqual(['b', 'd', 'a', 'c'])
  })
  it('does not mutate the input array', () => {
    const rows = [R('a', false), R('b', true)]
    sortByFrontier(rows)
    expect(rows.map((r) => r.ref)).toEqual(['a', 'b'])
  })
  it('handles an empty bucket', () => {
    expect(sortByFrontier([])).toEqual([])
  })
})
