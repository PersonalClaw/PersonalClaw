import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { hookToTrigger } from './triggerMeta'
import { LifecycleDetail } from './LifecycleDetail'
import type { HookItem } from '../../lib/api'

// ── A blocking lifecycle hook must SAY whether it is armed (G40) ────────────────────────────────
//
// Measured (AAP-3's sweep, 2026-08-17). One `PreToolUse` hook exiting 2 — the documented block
// signal — driven twice:
//
//   unbound (no agent profile references it) → fired 3× and the write STILL LANDED
//   bound   (id on the agent's `triggers`)   → "(hook blocked: …)" and the file was never created
//
// Two firing paths, one hook kind: `fire_for_ids` is agent-scoped and its `BLOCKED:` sentinel
// rejects the tool; `fire_tool_hooks` → `fire` is the informational path whose results are
// discarded. So the same hook enforces or does not depending on who references it.
//
// The frontend half of the defect: the row said "· dormant" in the dimmest ink available, the same
// word an unbound `Stop` hook got, while the Stats line said "Ran 3×" — a true count whose
// implication was false. `used_by: []` was already on the wire and did not close it, which is why
// `enforcement` is the SERVER's verdict here and is never re-derived in the view. These tests pin
// the three things a user must be able to read: the state, its opposite, and the run count's
// annotation.

vi.mock('../schedule/ScheduleDetail', () => ({ RunHistory: () => null }))

const hook = (over: Partial<HookItem> = {}): HookItem => ({
  id: 'h1', name: 'aap3-pretool', event: 'PreToolUse', matcher: '',
  provider: 'bash', provider_config: { command: 'exit 2' },
  timeout: 30, enabled: true, last_run: 0, last_status: '', run_count: 0, used_by: [],
  ...over,
})

const detail = (h: HookItem) => render(
  <LifecycleDetail
    hook={h}
    providers={[{ name: 'bash', display_name: 'Bash', supports_blocking: true, settingsSchema: {} }]}
    onSaved={() => {}}
    onDeleted={() => {}}
    editing={false}
    onEditingChange={() => {}}
  />,
)

describe('hookToTrigger carries the server enforcement verdict', () => {
  it('passes blocking + enforcement through onto the list view-model', () => {
    const t = hookToTrigger(hook({ blocking: true, enforcement: 'not_enforcing' }))
    expect(t.blocking).toBe(true)
    expect(t.enforcement).toBe('not_enforcing')
  })

  it('does not re-derive enforcement from used_by', () => {
    // The whole defect was that `used_by` was already on the wire and unreadable as a safety
    // state. A second opinion computed here would drift from the binding the firing path reads —
    // and would claim "enforcing" for a bound-but-DISABLED hook, which never fires at all.
    const bound = hookToTrigger(hook({ used_by: ['coder'], enabled: false, blocking: true, enforcement: 'not_enforcing' }))
    expect(bound.usedBy).toEqual(['coder'])
    expect(bound.enforcement).toBe('not_enforcing')
  })

  it('makes no claim when the server sends none', () => {
    const t = hookToTrigger(hook())
    expect(t.enforcement).toBeUndefined()
    expect(t.blocking).toBeUndefined()
  })
})

describe('LifecycleDetail on a blocking hook', () => {
  it('says NOT ENFORCING for an unbound one, and says what to do about it', () => {
    detail(hook({ blocking: true, enforcement: 'not_enforcing' }))
    expect(screen.getByText(/not enforcing/i)).toBeTruthy()
    // Not just a label — the sentence has to name the fix, or the state is legible and useless.
    expect(screen.getByText(/blocking hook that cannot block/i)).toBeTruthy()
    expect(screen.getByText(/No agents reference this trigger yet/)).toBeTruthy()
  })

  it('says ENFORCING for a bound one — a DIFFERENT rendering, not silence', () => {
    detail(hook({ blocking: true, enforcement: 'enforcing', used_by: ['coder'] }))
    expect(screen.getByText(/^Enforcing$/)).toBeTruthy()
    // Vacuity floor: the two states must be genuinely distinguishable. Silence for the armed case
    // is exactly what a user already had, and they read it as armed.
    expect(screen.queryByText(/not enforcing/i)).toBeNull()
    expect(screen.queryByText(/cannot block/i)).toBeNull()
  })

  it('annotates a non-zero run count on an unarmed hook — the measured misread', () => {
    // "Ran 3×" next to an inert policy hook is the trap: three real fires, three landed writes.
    detail(hook({ blocking: true, enforcement: 'not_enforcing', run_count: 3 }))
    expect(screen.getByText(/Ran 3×/)).toBeTruthy()
    expect(screen.getByText(/runs were advisory/i)).toBeTruthy()
  })

  it('leaves a non-blocking hook unlabelled either way', () => {
    // An unbound `Stop` hook is not a disarmed safety control. Badging it would cry wolf on 14 of
    // the 15 events and train the eye past the one that matters.
    detail(hook({ event: 'Stop', blocking: false, enforcement: 'advisory' }))
    expect(screen.queryByText(/enforcing/i)).toBeNull()
    expect(screen.getByText(/it's dormant/)).toBeTruthy()
  })
})
