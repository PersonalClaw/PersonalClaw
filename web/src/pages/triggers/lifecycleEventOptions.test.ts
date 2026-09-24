import { describe, it, expect } from 'vitest'
import { lifecycleEventOptions } from './triggerMeta'
import type { TriggerVariables } from '../../lib/api'

// ── The lifecycle picker: live first, and a heading only when it separates something ───
//
// PEP-1's second half. The problem it solves is real — an event nothing fires reads like an
// equal choice in a flat list of fifteen, and the only feedback is a trigger that saves and
// never runs — but the population it was written against has MOVED:
//
//   plan text:  "~15 events, 7 of which warn 'never fires'"
//   measured:   `GET /api/triggers/variables`, current build → **15 events, 0 dormant**
//
// So an unconditional heading would label all fifteen "Live events" and separate nothing:
// chrome that costs a line of vertical space and teaches nothing, on the exact surface this
// atom is trying to make legible. Hence the conditional. Both branches are asserted here,
// which is also what keeps the rail from being vacuous — the dormant branch is exercised by a
// fixture even while the live catalog has no dormant events in it.

const ev = (event: string, label: string, dormant = false) => ({ event, label, desc: `${label} desc`, dormant, vars: [] })

const cat = (lifecycle: ReturnType<typeof ev>[]): TriggerVariables =>
  ({ lifecycle, schedule: [], app_sources: [] } as unknown as TriggerVariables)

describe('lifecycleEventOptions', () => {
  it('emits NO group at all while nothing is dormant — todays measured shape', () => {
    const opts = lifecycleEventOptions(cat([ev('SessionStart', 'Session start'), ev('Stop', 'Stop')]))
    expect(opts.map((o) => o.value)).toEqual(['SessionStart', 'Stop'])
    expect(opts.every((o) => o.group === undefined)).toBe(true)
    // And no option is mislabelled as dead.
    expect(opts.some((o) => /never fires/.test(o.label))).toBe(false)
  })

  it('groups live-first the moment ONE event is dormant', () => {
    const opts = lifecycleEventOptions(cat([
      ev('Dead', 'Dead event', true),
      ev('SessionStart', 'Session start'),
      ev('AlsoDead', 'Also dead', true),
      ev('Stop', 'Stop'),
    ]))
    // Live events come first, so the picker OPENS on what can actually fire.
    expect(opts.map((o) => o.value)).toEqual(['SessionStart', 'Stop', 'Dead', 'AlsoDead'])
    // `Combobox` renders group headings in FIRST-SEEN order, so the sort is the grouping.
    expect(opts.map((o) => o.group)).toEqual([
      'Live events', 'Live events',
      'Advanced — nothing fires these yet', 'Advanced — nothing fires these yet',
    ])
  })

  it('marks a dormant event inline as well as grouping it', () => {
    const opts = lifecycleEventOptions(cat([ev('Dead', 'Dead event', true), ev('Stop', 'Stop')]))
    expect(opts.find((o) => o.value === 'Dead')?.label).toBe('Dead event · never fires')
    expect(opts.find((o) => o.value === 'Stop')?.label).toBe('Stop')
  })

  it('keeps dormant events PICKABLE rather than hiding them', () => {
    // Hiding them would make a legitimate pre-wire impossible, and the form already warns at
    // the point of choice.
    const opts = lifecycleEventOptions(cat([ev('Dead', 'Dead event', true)]))
    expect(opts).toHaveLength(1)
    expect(opts[0].value).toBe('Dead')
  })

  it('carries every event through — grouping is presentation, not a filter', () => {
    const input = Array.from({ length: 15 }, (_, i) => ev(`E${i}`, `Event ${i}`, i % 3 === 0))
    const opts = lifecycleEventOptions(cat(input))
    expect(opts).toHaveLength(15)
    expect(new Set(opts.map((o) => o.value)).size).toBe(15)
  })

  it('is empty and unthrowing while the catalog is still loading', () => {
    expect(lifecycleEventOptions(null)).toEqual([])
  })
})
