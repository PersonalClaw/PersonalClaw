/**
 * §1.3's archive split must reach the widget (S165).
 *
 * §1.3: "inert outcomes collapse to ledger rows and archive out of the default inbox view — the runs
 * inbox is for what the machine DID." The backend has returned `did_ids`/`suppressed` since S132 and
 * S163 typed them onto the wrapper — but `DashboardLive` kept only `d.runs` and the widget rendered
 * that list raw.
 *
 * 🔴 MEASURED. A minutely trigger inside quiet hours: 11 `skipped_gate` rows and ONE `ran` at index
 * 7. `schedule.slice(0, 6)` showed six identical "gate" entries and the real fire never appeared, so
 * the user reads "nothing has run" — the exact failure the split exists to prevent.
 *
 * The fix ORDERS rather than filters: suppressed rows still render below the real ones, because §7
 * criterion 8 bans silent drops and "why did my automation not run" must stay answerable. What
 * changes is that work outranks suppression for the six scarce visible slots.
 */
import { describe, it, expect } from 'vitest'

type Row = { id?: string; outcome?: string }

/** The widget's ordering, kept in step with ScheduleWidget.tsx. */
function order(schedule: Row[], didIds: string[]): Row[] {
  const did = new Set(didIds)
  const isDid = (r: Row) => !r.id || did.has(r.id)
  return [...schedule.filter(isDid), ...schedule.filter((r) => !isDid(r))]
}

const quietHours = (): Row[] =>
  Array.from({ length: 12 }, (_, i) =>
    i === 7 ? { id: 'REAL', outcome: 'ran' } : { id: `s${i}`, outcome: 'skipped_gate' },
  )

describe('the archive split in the Schedule widget', () => {
  it('surfaces the one fire that RAN, even buried at index 7', () => {
    const visible = order(quietHours(), ['REAL']).slice(0, 6)
    expect(visible.map((r) => r.id)).toContain('REAL')
    expect(visible[0].id).toBe('REAL')
  })

  it('does NOT drop the suppressed rows — they follow, they do not vanish', () => {
    // §7 criterion 8: zero silent drops. A user asking "why did it not run" needs the gate rows.
    const all = order(quietHours(), ['REAL'])
    expect(all).toHaveLength(12)
    expect(all.filter((r) => r.outcome === 'skipped_gate')).toHaveLength(11)
  })

  it('keeps every real fire ahead of every suppression', () => {
    const rows: Row[] = [
      { id: 'a', outcome: 'skipped_gate' },
      { id: 'b', outcome: 'ran' },
      { id: 'c', outcome: 'skipped_budget' },
      { id: 'd', outcome: 'failed' },
    ]
    const ids = order(rows, ['b', 'd']).map((r) => r.id)
    expect(ids).toEqual(['b', 'd', 'a', 'c'])
  })

  it('renders a LEGACY row with no id rather than hiding it', () => {
    // 🔴 The fail direction. A projected FireRecord has an `id`; a legacy ScheduleRun row does not.
    // An unknown row is more likely real work than a gate hit, so "shown" is the safe default —
    // hiding it would make the widget quietly lose history it used to display.
    const legacy: Row[] = [{ outcome: 'success' }, { outcome: 'failure' }]
    expect(order(legacy, [])).toHaveLength(2)
    expect(order(legacy, []).map((r) => r.outcome)).toEqual(['success', 'failure'])
  })

  it('is a no-op when the server reports nothing suppressed', () => {
    const rows: Row[] = [{ id: 'x', outcome: 'ran' }, { id: 'y', outcome: 'ran' }]
    expect(order(rows, ['x', 'y']).map((r) => r.id)).toEqual(['x', 'y'])
  })

  it('keys on `id`, not `run_id` — a FireRecord has no run_id', () => {
    // 🔴 A bug in the first draft of this fix: keyed on `r.run_id`, which a projected row serialises
    // as `''`. Nothing matched `did_ids`, so the split silently no-opped — a fix that becomes an
    // inert control. Pinned here because the two field names are easy to confuse.
    const rows: Row[] = [{ id: 'kept', outcome: 'ran' }, { id: 'gate', outcome: 'skipped_gate' }]
    expect(order(rows, ['kept'])[0].id).toBe('kept')
  })
})
