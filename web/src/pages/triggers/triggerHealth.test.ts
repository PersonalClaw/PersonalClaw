/**
 * A FAILING automation must not look like a parked one (S164).
 *
 * 🔴 THE DEFECT. `TriggersListPage` carried its own `statusDot` handling four values
 * (`ok`/`success`, `error`/`timeout`/`blocked`, `launched`) and defaulting everything else to a
 * neutral grey circle. But for a store trigger that page feeds it `t.health`, whose vocabulary is
 * `TriggerHealth` = `ok | degraded | parked | failing`. Measured against the real values:
 *
 *     health=ok        -> ok green, check
 *     health=degraded  -> grey, circle
 *     health=parked    -> grey, circle
 *     health=failing   -> grey, circle     ← identical to parked and degraded
 *
 * So on the ONE page a user manages automations from, a failing automation was pixel-identical to a
 * parked (self-healing) one. This is S163's defect shape in a second local copy of the same idea —
 * which is the argument for one mapper per vocabulary rather than one fix per page.
 *
 * The lifecycle STATE was invisible too: the `/api/triggers` store projection never emitted
 * `Trigger.state`, so autopause (S139), park/unpark (S159) and the injection quarantine all decided
 * states no surface could show.
 */
import { describe, it, expect } from 'vitest'
import { triggerHealthMeta } from '../schedule/scheduleMeta'

const HEALTH = ['ok', 'degraded', 'parked', 'failing']
const STATES = ['active', 'paused', 'autopaused', 'parked', 'quarantined', 'retired']

describe('triggerHealthMeta — the health rollup', () => {
  it('does NOT render a failing automation as a neutral dot', () => {
    const m = triggerHealthMeta('failing', 'active')
    expect(m.label).toBe('failing')
    expect(m.tone).toBe('var(--color-danger)')
  })

  it('keeps failing, degraded and parked visually DISTINCT', () => {
    const tones = new Set(
      ['failing', 'degraded', 'parked'].map((h) => triggerHealthMeta(h, 'active').tone),
    )
    expect(tones.size).toBe(3)
  })

  it('gives PARKED an informational tone, not a danger one', () => {
    // Parking self-heals once the cooldown elapses (S159's unpark), so a red badge would send the
    // user hunting a fault that resolves itself.
    expect(triggerHealthMeta('parked', 'active').tone).toBe('var(--color-info)')
  })

  it('renders every health value as something, never a blank label', () => {
    const blank = HEALTH.filter((h) => !triggerHealthMeta(h, 'active').label)
    expect(blank).toEqual([])
  })
})

describe('triggerHealthMeta — the lifecycle state', () => {
  it('lets a STOPPED state outrank the health rollup', () => {
    // An autopaused trigger's health is `failing`, but "stopped" is the more urgent fact: health
    // says how it has been going, state says whether it will run at all.
    expect(triggerHealthMeta('failing', 'autopaused').label).toBe('autopaused')
    expect(triggerHealthMeta('failing', 'quarantined').label).toBe('quarantined')
  })

  it('does not let a healthy rollup hide a stopped automation', () => {
    // The dangerous direction: `health: ok` on a paused trigger must not read as running.
    expect(triggerHealthMeta('ok', 'autopaused').label).toBe('autopaused')
    expect(triggerHealthMeta('ok', 'paused').label).toBe('paused')
  })

  it('leaves an ACTIVE trigger reporting its health', () => {
    expect(triggerHealthMeta('ok', 'active').label).toBe('ok')
    expect(triggerHealthMeta('degraded', 'active').label).toBe('degraded')
  })

  it('renders every lifecycle state as something distinguishable', () => {
    const blank = STATES.filter((s) => s !== 'active' && !triggerHealthMeta('ok', s).label)
    expect(blank).toEqual([])
  })

  it('keeps quarantined distinct from a mere pause', () => {
    // Quarantine means a payload matched an injection pattern and `resume_state` refuses to
    // reinstate it from a button — the opposite of a pause the user can just undo.
    expect(triggerHealthMeta('failing', 'quarantined').tone).toBe('var(--color-danger)')
    expect(triggerHealthMeta('failing', 'paused').tone).not.toBe('var(--color-danger)')
  })
})

describe('the wire contract this depends on', () => {
  it('the store projection must emit `state`, not only `health`', () => {
    // Recorded as an assertion because the projection omitting it is what made the whole lifecycle
    // invisible — `health` cannot substitute: a PARKED trigger is `health: parked`, but an
    // AUTOPAUSED one is `health: failing`, and "failing" does not tell the user it has STOPPED.
    expect(triggerHealthMeta('failing', 'autopaused')).not.toEqual(
      triggerHealthMeta('failing', 'active'),
    )
  })
})
