import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The week-grid empty state must not blame a working feature (#686) ────────────────────────────
//
// The hint said "Only enabled interval schedules are plotted. A cron-expression trigger is not
// projected here yet" — pre-S103 copy. S103's cron stepper (`calendar.next_after`) closed that
// gap, and on the measured home 15 of 15 plotted fires were CRON: the hint had it precisely
// backwards, telling a user staring at an empty week that the cause was "you used cron" and the
// fix was "use an interval". One-shots (`kind: "at"`) genuinely are not projected (#561) — that
// is the omission the hint may keep naming until #561 ships.
//
// A source rail, not a render test: the defect is a STRING asserting false capability, and the
// grid's data path is already covered elsewhere. This pins the two claims a user acts on.

const FILE = join(process.cwd(), 'src/pages/triggers/WeekGridView.tsx')
const src = () => readFileSync(FILE, 'utf8')

describe('week-grid empty-state copy (#686)', () => {
  it('reads the real file (not vacuously green)', () => {
    expect(src()).toContain('No fires this week')
  })

  it('does not claim cron triggers are unprojected — S103 plots them', () => {
    const s = src()
    expect(s).not.toContain('A cron-expression trigger is not projected')
    expect(s).not.toContain('Only enabled interval schedules are plotted')
  })

  it('names only the true causes: enabled-with-a-fire, disabled, and the one-shot omission (#561)', () => {
    const s = src()
    expect(s).toContain('interval and cron alike')
    expect(s).toContain('A disabled trigger has no fires')
    // The honest remaining omission. When one-shot projection ships (#561), update
    // the hint AND this line together.
    expect(s).toContain('a one-shot is not projected here yet')
  })
})
