/**
 * §1.3's archive split, now a FOLD (WF2AUT-10 — builds on S165).
 *
 * §1.3: "inert outcomes collapse to ledger rows and archive out of the default inbox view — the runs
 * inbox is for what the machine DID." S165 ordered work-first, but ordering only helps until the
 * real fires run out: a minutely trigger inside quiet hours (11 `skipped_gate` rows + ONE `ran`)
 * still buried the real fire once the six visible slots filled. WF2AUT-10 archives the suppressed
 * rows behind a disclosure — the default view is work; the gate hits are one click away, never
 * dropped (§7 criterion 8).
 *
 * These tests exercise the SHARED `partitionRuns` helper the widget uses — not an inline copy. The
 * earlier `order()` in this file WAS that second copy, and the widget's own comments record that a
 * second copy of the split's rule drifts the moment a new `skipped_*` outcome lands.
 */
import { describe, it, expect } from 'vitest'
import { partitionRuns } from './scheduleFold'

type Row = { id?: string; outcome?: string }

/** The widget's default (collapsed) view: work only. */
function defaultView(schedule: Row[], didIds: string[]): Row[] {
  return partitionRuns(schedule, didIds).did
}

/** The widget's expanded view: work first, then the revealed suppressions. */
function expandedView(schedule: Row[], didIds: string[]): Row[] {
  const { did, suppressed } = partitionRuns(schedule, didIds)
  return [...did, ...suppressed]
}

const quietHours = (): Row[] =>
  Array.from({ length: 12 }, (_, i) =>
    i === 7 ? { id: 'REAL', outcome: 'ran' } : { id: `s${i}`, outcome: 'skipped_gate' },
  )

describe('the archive fold in the Schedule widget', () => {
  it('surfaces the one fire that RAN, even buried at index 7', () => {
    // The default view is work only, so the real fire is first — not competing with 11 gate rows.
    const visible = defaultView(quietHours(), ['REAL']).slice(0, 6)
    expect(visible.map((r) => r.id)).toEqual(['REAL'])
  })

  it('archives the suppressed rows out of the default view', () => {
    // §1.3: the runs inbox is for what the machine DID. The gate rows do not crowd the default.
    expect(defaultView(quietHours(), ['REAL'])).toHaveLength(1)
  })

  it('does NOT drop the suppressed rows — they reveal on demand', () => {
    // §7 criterion 8: zero silent drops. Expanding shows every gate row.
    const all = expandedView(quietHours(), ['REAL'])
    expect(all).toHaveLength(12)
    expect(all.filter((r) => r.outcome === 'skipped_gate')).toHaveLength(11)
  })

  it('keeps every real fire ahead of every suppression when expanded', () => {
    const rows: Row[] = [
      { id: 'a', outcome: 'skipped_gate' },
      { id: 'b', outcome: 'ran' },
      { id: 'c', outcome: 'skipped_budget' },
      { id: 'd', outcome: 'failed' },
    ]
    const ids = expandedView(rows, ['b', 'd']).map((r) => r.id)
    expect(ids).toEqual(['b', 'd', 'a', 'c'])
  })

  it('renders a LEGACY row with no id as work rather than hiding it', () => {
    // 🔴 The fail direction. A projected FireRecord has an `id`; a legacy ScheduleRun row does not.
    // An unknown row is more likely real work than a gate hit, so it stays in the default view —
    // hiding it would make the widget quietly lose history it used to display.
    const legacy: Row[] = [{ outcome: 'success' }, { outcome: 'failure' }]
    expect(defaultView(legacy, [])).toHaveLength(2)
    expect(defaultView(legacy, []).map((r) => r.outcome)).toEqual(['success', 'failure'])
  })

  it('shows everything in the default view when the server reports nothing suppressed', () => {
    const rows: Row[] = [{ id: 'x', outcome: 'ran' }, { id: 'y', outcome: 'ran' }]
    expect(defaultView(rows, ['x', 'y']).map((r) => r.id)).toEqual(['x', 'y'])
  })

  it('keys on `id`, not `run_id` — a FireRecord has no run_id', () => {
    // 🔴 A bug in the first draft of this fix: keyed on `r.run_id`, which a projected row serialises
    // as `''`. Nothing matched `did_ids`, so the split silently no-opped — a fix that becomes an
    // inert control. Pinned here because the two field names are easy to confuse.
    const rows = [{ id: 'kept', run_id: '', outcome: 'ran' }, { id: 'gate', run_id: '', outcome: 'skipped_gate' }]
    expect(partitionRuns(rows, ['kept']).did.map((r) => r.id)).toEqual(['kept'])
    expect(partitionRuns(rows, ['kept']).suppressed.map((r) => r.id)).toEqual(['gate'])
  })
})
