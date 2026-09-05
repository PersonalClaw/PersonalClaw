import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The week-grid empty state must not blame a working feature (#686, then #561) ──────────────────
//
// The hint said "Only enabled interval schedules are plotted. A cron-expression trigger is not
// projected here yet" — pre-S103 copy. S103's cron stepper (`calendar.next_after`) closed that
// gap, and on the measured home 15 of 15 plotted fires were CRON: the hint had it precisely
// backwards, telling a user staring at an empty week that the cause was "you used cron" and the
// fix was "use an interval".
//
// It then named ONE-SHOTS as the remaining omission, which was true and no longer is: issue 561
// routed `kind: "at"` through the same `cadence_next_fire` stepper the cron branch uses, so a
// one-shot set for Thursday now plots. This file is why that copy could not be left behind — the
// note below used to read "when one-shot projection ships (#561), update the hint AND this line
// together", and shipping the projection alone would have left the grid plotting a fire its own
// empty state denies.
//
// 🪤 What is STILL true, and what the hint now says instead: a one-shot that ALREADY FIRED, or is
// set beyond this week, has nothing left to plot. That is the elapsed case the old backend guard
// was right about — it just applied it to every one-shot rather than only the elapsed ones.
//
// A source rail, not a render test: the defect is a STRING asserting false capability, and the
// grid's data path is covered by `tests/test_week_grid_plots_a_one_shot.py`. This pins the claims a
// user acts on.

const FILE = join(process.cwd(), 'src/pages/triggers/WeekGridView.tsx')
const src = () => readFileSync(FILE, 'utf8')

describe('week-grid empty-state copy (#686, #561)', () => {
  it('reads the real file (not vacuously green)', () => {
    expect(src()).toContain('No fires this week')
  })

  it('does not claim cron triggers are unprojected — S103 plots them', () => {
    const s = src()
    expect(s).not.toContain('A cron-expression trigger is not projected')
    expect(s).not.toContain('Only enabled interval schedules are plotted')
  })

  it('does not claim one-shots are unprojected — issue 561 plots them', () => {
    // The exact string the previous version REQUIRED. Inverted rather than deleted, so the copy
    // cannot drift back to denying a capability that now ships.
    expect(src()).not.toContain('a one-shot is not projected here yet')
  })

  it('names every kind that plots, so no user reads the empty week as their kind being unsupported', () => {
    const s = src()
    expect(s).toContain('interval, cron and one-shot alike')
    expect(s).toContain('A disabled trigger has no fires')
  })

  it('still names the one-shot cases that genuinely have nothing to plot', () => {
    // An empty week with a one-shot in the store is now either "it already fired" or "it is set
    // beyond this week". Saying neither would send the user hunting for a bug that is not there.
    const s = src()
    expect(s).toContain('already fired')
    expect(s).toContain('beyond this week')
  })
})
