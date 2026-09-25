import { describe, expect, it } from 'vitest'
import { resolveOpenTrigger, scheduleToTrigger, storeToTrigger, hookToTrigger, type Trigger } from './triggerMeta'
import type { HookItem, ScheduleJob, Trigger as WireTrigger } from '../../lib/api'

// ── `?open=` resolves the id the trigger SUBSTRATE mints, not only the page's own (B8) ───────────
//
// `delivery.status_url`, the autopause attention card and the triage digest all link
// `#/triggers?open=<store id>` — `clock:standup-nudge` — while the list names that row
// `schedule:clock:standup-nudge`. An exact match on the namespaced id opened the list with NO panel
// for every one of those links, so the notification's deep link led nowhere even once it was wired.

const sched = scheduleToTrigger({ id: 'clock:standup-nudge', name: 'Standup nudge', enabled: true, schedule: 'every 60s', message: '' } as ScheduleJob)
const store = storeToTrigger({ kind: 'store', id: 'store:file:notes', raw_id: 'file:notes', name: 'Notes', enabled: true, action: { provider: 'notify', config: {} } } as WireTrigger)
// A lifecycle hook whose (independently minted) id happens to equal a store id — the collision the
// fallback must not cross.
const hook = hookToTrigger({ id: 'file:notes', name: 'A hook', event: 'Stop', matcher: '', provider: 'notify', provider_config: {}, timeout: 30, enabled: true, last_run: 0, last_status: '', run_count: 0, used_by: [] } as HookItem)
const ALL: Trigger[] = [sched, store]

describe('resolveOpenTrigger', () => {
  it('the page\'s own namespaced id still resolves', () => {
    expect(resolveOpenTrigger(ALL, 'schedule:clock:standup-nudge')).toBe(sched)
    expect(resolveOpenTrigger(ALL, 'store:file:notes')).toBe(store)
  })

  it('the STORE id a notification carries resolves to the same row', () => {
    expect(resolveOpenTrigger(ALL, 'clock:standup-nudge')).toBe(sched)
    expect(resolveOpenTrigger(ALL, 'file:notes')).toBe(store)
  })

  it('a bare id never crosses into a lifecycle hook\'s namespace', () => {
    expect(resolveOpenTrigger([hook], 'file:notes')).toBeNull()
    // …and when both exist, the trigger-store row is the one a substrate link means.
    expect(resolveOpenTrigger([hook, store], 'file:notes')).toBe(store)
  })

  it('an exact namespaced match wins over a raw-id match', () => {
    const twin = { ...store, id: 'clock:standup-nudge' } as Trigger
    expect(resolveOpenTrigger([sched, twin], 'clock:standup-nudge')).toBe(twin)
  })

  it('nothing to open is null, not the first row', () => {
    expect(resolveOpenTrigger(ALL, null)).toBeNull()
    expect(resolveOpenTrigger(ALL, 'clock:gone')).toBeNull()
    expect(resolveOpenTrigger(null, 'clock:standup-nudge')).toBeNull()
  })
})
