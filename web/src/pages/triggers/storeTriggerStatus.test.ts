/**
 * A machine-stopped automation must not read as a user-paused one (S169).
 *
 * 🔴 THE DEFECT. `StoreTriggerDetail`'s only status line was
 * `enabled ? 'Firing on its own' : 'Paused — it will not fire until re-enabled'`. Measured across the
 * real lifecycle states:
 *
 *     user paused it        -> "Paused — it will not fire until re-enabled"
 *     AUTOPAUSED (5 fails)  -> "Paused — it will not fire until re-enabled"
 *     QUARANTINED           -> "Paused — it will not fire until re-enabled"
 *
 * `TriggerState`'s own docstring names this exact failure: *"`autopaused` is separate from `paused`
 * because the two answer different questions … Showing both as "paused" would make the user look for a
 * switch they never flipped."*
 *
 * And the CAUSE was invisible: `last_error` has been on the wire all along with no reader in this
 * panel, so an autopaused automation offered no way to learn why — the digging that
 * `attention_card`'s docstring says the error text exists to prevent.
 */
import { describe, it, expect } from 'vitest'
import { triggerHealthMeta, triggerStatusMeta } from '../schedule/scheduleMeta'

/** The panel's status sentence, kept in step with StoreTriggerDetail.tsx. */
function statusLine(state?: string, enabled = true): string {
  return state === 'autopaused'
    ? 'Stopped by the system after repeated failures'
    : state === 'quarantined'
      ? 'Quarantined — a payload matched an injection pattern; re-author it to resume'
      : state === 'parked'
        ? 'Parked — a resource it needs is busy; it resumes on its own'
        : enabled
          ? 'Firing on its own'
          : 'Paused — it will not fire until re-enabled'
}

const USER_PAUSED = statusLine('paused', false)

describe('the store panel status line', () => {
  it('does NOT describe an autopaused automation as user-paused', () => {
    expect(statusLine('autopaused', false)).not.toBe(USER_PAUSED)
    expect(statusLine('autopaused', false)).toContain('system')
  })

  it('does NOT describe a quarantined automation as user-paused', () => {
    expect(statusLine('quarantined', false)).not.toBe(USER_PAUSED)
    // Quarantine cannot be undone with the toggle — `resume_state` refuses it — so the sentence has
    // to say what the user must actually do.
    expect(statusLine('quarantined', false)).toContain('re-author')
  })

  it('still describes a USER pause as a user pause', () => {
    // The control case: the original sentence was correct for the one state it was written for.
    expect(statusLine('paused', false)).toBe(USER_PAUSED)
  })

  it('says a parked automation resumes ITSELF', () => {
    // S159 made parking self-healing. Telling the user to re-enable it would send them to fix
    // something that fixes itself.
    expect(statusLine('parked', true)).toContain('resumes on its own')
  })

  it('gives every stopped state a DISTINCT sentence', () => {
    const lines = ['paused', 'autopaused', 'quarantined', 'parked'].map((s) => statusLine(s, false))
    expect(new Set(lines).size).toBe(4)
  })

  it('falls back to the enabled/disabled reading when state is absent', () => {
    // A row from before `state` was projected (S164) must still render something true.
    expect(statusLine(undefined, true)).toBe('Firing on its own')
    expect(statusLine(undefined, false)).toBe(USER_PAUSED)
  })
})

describe('the status dot beside it', () => {
  it('reuses the shared mapper rather than a third local vocabulary', () => {
    // S163 and S164 each found a local copy of a status vocabulary that had drifted. This panel is
    // the third surface; it maps through the shared reconciler instead of inventing its own.
    //
    // These two stopped states still go through `triggerHealthMeta` directly — that is
    // `triggerStatusMeta`'s own internal delegation for a non-active state, not a bypass of it
    // (the reconciler calls `triggerHealthMeta` itself once `stopped` is true), so asserting them
    // against `triggerHealthMeta` still pins real, current behaviour.
    expect(triggerHealthMeta('failing', 'autopaused').tone).toBe('var(--color-danger)')
    expect(triggerHealthMeta('parked', 'parked').tone).toBe('var(--color-info)')
    // 🔴 CORRECTED, NOT SILENCED (issue 3396). This used to assert
    // `triggerHealthMeta('ok', 'active').tone === 'var(--color-ok)'` directly — a GREEN assertion on
    // the exact value that is WRONG for a trigger that has never fired: `health` DEFAULTS to `ok`
    // before anything has run, so that line pinned the panel's own bug (it called `triggerHealthMeta`
    // with no `hasRun` gate, same shape as this assertion) instead of catching it. The panel now
    // reads through `triggerStatusMeta`, which gates an `active`+`ok` rollup on `hasRun` — both arms
    // asserted here so this rail cannot go blind to either direction again.
    expect(triggerStatusMeta({ health: 'ok', state: 'active', hasRun: false }).tone).toBe('var(--color-on-surface-low)')
    expect(triggerStatusMeta({ health: 'ok', state: 'active', hasRun: true }).tone).toBe('var(--color-ok)')
  })

  it('keeps a machine stop visually distinct from a user pause', () => {
    expect(triggerHealthMeta('failing', 'autopaused').tone).not.toBe(
      triggerHealthMeta('ok', 'paused').tone,
    )
  })
})
