import { describe, expect, it } from 'vitest'
import { eventDormancyReason, eventIsDormant, lifecycleEventMeta, storeToTrigger } from './triggerMeta'
import type { TriggerVariables, Trigger as WireTrigger } from '../../lib/api'

// ── Lifecycle-event dormancy, from the UI's side (S67) ──────────────────────
//
// 7 of the 15 declared lifecycle events have no fire site: the API accepts a hook on them, the list
// renders it enabled, and nothing ever runs it. The badge exists so a user learns that at the moment
// of CHOICE rather than by waiting for a trigger that cannot fire.
//
// The property that makes the badge safe: dormancy is read from the SERVER catalog, never from a
// local list. A hard-coded copy would drift the wrong way — once the backend wires an event, a stale
// list would tell a user their WORKING hook is dead, which is worse than showing no badge at all.
// So every helper here must return "fires" for anything it was not explicitly told is dormant.

const cat = (over: Partial<TriggerVariables> = {}): TriggerVariables => ({
  schedule: ['$NOW'],
  lifecycle: [
    { event: 'PreToolUse', label: 'Pre tool use', desc: 'Before a tool runs', vars: ['$EVENT'], blocking: true },
    { event: 'SessionEnd', label: 'Session end', desc: 'A session ends', vars: ['$EVENT'], blocking: false, dormant: true, dormant_reason: 'session teardown has no fire site' },
    { event: 'MemoryWrite', label: 'Memory write', desc: 'A memory is written', vars: ['$EVENT'], blocking: false, dormant: true },
  ],
  ...over,
})

describe('eventIsDormant', () => {
  it('flags an event the server marked dormant', () => {
    expect(eventIsDormant(cat(), 'SessionEnd')).toBe(true)
  })

  it('does not flag a live event', () => {
    expect(eventIsDormant(cat(), 'PreToolUse')).toBe(false)
  })

  it('treats an unknown event as live, not dormant', () => {
    // The fail-safe direction. An event the catalog has not heard of (a newer backend, a truncated
    // response) must NOT be badged "never fires" — that is the claim that misleads.
    expect(eventIsDormant(cat(), 'SomeNewEvent')).toBe(false)
  })

  it('treats a still-loading catalog as live', () => {
    // `useTriggerVariables` returns null while fetching. Badging everything dormant for that beat
    // would flash "never fires" across every option on first paint.
    expect(eventIsDormant(null, 'SessionEnd')).toBe(false)
  })

  it('is false for an absent event name', () => {
    expect(eventIsDormant(cat(), undefined)).toBe(false)
    expect(eventIsDormant(cat(), '')).toBe(false)
  })
})

describe('eventDormancyReason', () => {
  it('returns the server-supplied reason', () => {
    expect(eventDormancyReason(cat(), 'SessionEnd')).toBe('session teardown has no fire site')
  })

  it('returns empty for a live event even if a reason were present', () => {
    // Gated on `dormant`, not on the presence of a reason string: a live event must never render
    // dormancy copy.
    const c = cat({
      lifecycle: [{ event: 'Stop', label: 'Stop', desc: '', vars: [], blocking: false, dormant: false, dormant_reason: 'stale text' }],
    })
    expect(eventDormancyReason(c, 'Stop')).toBe('')
  })

  it('returns empty when a dormant event carries no reason, so callers can supply a fallback', () => {
    expect(eventDormancyReason(cat(), 'MemoryWrite')).toBe('')
    expect(eventIsDormant(cat(), 'MemoryWrite')).toBe(true)
  })

  it('returns empty for an unknown event and a loading catalog', () => {
    expect(eventDormancyReason(cat(), 'Nope')).toBe('')
    expect(eventDormancyReason(null, 'SessionEnd')).toBe('')
  })
})

describe('lifecycleEventMeta', () => {
  it('carries the dormancy fields through so a caller can badge from one lookup', () => {
    const em = lifecycleEventMeta(cat(), 'SessionEnd')
    expect(em.label).toBe('Session end')
    expect(em.dormant).toBe(true)
  })

  it('falls back to an empty shell without inventing dormancy', () => {
    const em = lifecycleEventMeta(null, 'Whatever')
    expect(em.event).toBe('Whatever')
    expect(em.dormant).toBeUndefined()
  })
})

// ── storeToTrigger: the file/web_watch/idle/… kinds surfaced from the unified store (S94/S95) ──
//
// These automations are created in chat (the automation_* tools) and, before S94, were invisible on
// the Automations page — created, fired, and unlistable. The mapper projects the wire row onto the
// shared list view-model. The key invariants: the store's own id survives as rawId (it is itself
// <kind>:<slug>, and the toggle/run/delete helpers re-namespace it), a broken row is carried so the
// list can flag it rather than hiding it, and an unknown store_kind degrades to a neutral label
// instead of a blank row.

const storeRow = (over: Partial<WireTrigger> = {}): WireTrigger => ({
  kind: 'store', id: 'store:file:summarize-notes', raw_id: 'file:summarize-notes',
  name: 'Summarize notes', enabled: true, action: { provider: 'run-prompt', config: {} },
  store_kind: 'file', spec: { paths: ['~/notes/**'] }, broken: [],
  ...over,
})

describe('storeToTrigger', () => {
  it('projects a file automation onto the list view-model', () => {
    const t = storeToTrigger(storeRow())
    expect(t.kind).toBe('store')
    expect(t.storeKind).toBe('file')
    expect(t.whenLabel).toBe('On file change')
    expect(t.actionLabel).toBe('Run Prompt')
    expect(t.enabled).toBe(true)
  })

  it('keeps the store id as rawId so mutations re-namespace correctly', () => {
    // The namespaced id is `store:file:summarize-notes`; rawId must be the store's own
    // `file:summarize-notes`, or toggle/run/delete would target the wrong path.
    const t = storeToTrigger(storeRow())
    expect(t.id).toBe('store:file:summarize-notes')
    expect(t.rawId).toBe('file:summarize-notes')
  })

  it('carries a broken row rather than hiding it', () => {
    const t = storeToTrigger(storeRow({ broken: ['unknown trigger kind'] }))
    expect(t.broken).toEqual(['unknown trigger kind'])
  })

  it('falls back to a neutral label for an unknown store_kind', () => {
    const t = storeToTrigger(storeRow({ store_kind: 'brand_new_kind' }))
    // Not blank — the raw kind stands in so the row is still readable.
    expect(t.whenLabel).toBe('brand_new_kind')
  })

  it('labels each declared store kind', () => {
    const kinds: Array<[string, string]> = [
      ['file', 'On file change'],
      ['web_watch', 'On web page change'],
      ['idle', 'When idle'],
      ['run_completed', 'When a run finishes'],
      ['webhook', 'On webhook'],
    ]
    for (const [k, label] of kinds) {
      expect(storeToTrigger(storeRow({ store_kind: k })).whenLabel).toBe(label)
    }
  })
})
