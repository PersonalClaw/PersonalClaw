import { describe, expect, it } from 'vitest'
import {
  eventDormancyReason, eventIsDormant, lifecycleEventMeta, storeToTrigger,
  EVENT_PATTERN_META, eventPatternMeta, eventSourceIcon, actionIsSendCapable,
} from './triggerMeta'
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

// ── Data-event pattern metadata (EIAT-5) ────────────────────────────────────
//
// The create form shows one matcher field per pattern, chosen by EVENT_PATTERN_META. The invariants
// that keep the form honest: the table is in lockstep with the backend `event_triggers.EVENT_PATTERNS`
// tuple (same 6 members), each row's `matcher` names the ONE spec field the backend's `matches()`
// reads for that pattern (so the form never shows an inert field), and `matcherRequired` mirrors the
// server's `sender_glob_required` gate so the UI blocks the same empty submit the API would reject.

describe('EVENT_PATTERN_META', () => {
  it('has exactly one row per backend EVENT_PATTERNS member', () => {
    // Lockstep with src/personalclaw/event_triggers.py::EVENT_PATTERNS — a drift here means the form
    // offers a pattern the backend rejects, or hides one it accepts.
    const patterns = EVENT_PATTERN_META.map((p) => p.pattern).sort()
    expect(patterns).toEqual(
      ['ContentMatch', 'InboxAddress', 'InboxMessage', 'InboxSender', 'MemoryKeyPattern', 'MemoryUpdate'],
    )
  })

  it('maps each pattern to the source the backend derives, never a free choice', () => {
    // source is display-only here; the backend derives it from the pattern (PATTERN_SOURCE). The
    // three Inbox* patterns are inbox; the three Memory*/Content are memory.
    const bySource = (s: string) => EVENT_PATTERN_META.filter((p) => p.source === s).map((p) => p.pattern).sort()
    expect(bySource('inbox')).toEqual(['InboxAddress', 'InboxMessage', 'InboxSender'])
    expect(bySource('memory')).toEqual(['ContentMatch', 'MemoryKeyPattern', 'MemoryUpdate'])
  })

  it('names a real spec matcher field (or null) for every pattern', () => {
    // Each `matcher` must be one of the EventTrigger fields matches() reads — or null for the
    // fire-on-everything patterns. A typo'd field name would post a key the backend ignores.
    const allowed = new Set(['sender_glob', 'address_glob', 'key_glob', 'content_re', null])
    for (const p of EVENT_PATTERN_META) expect(allowed.has(p.matcher)).toBe(true)
    // The two catch-all patterns carry no matcher.
    expect(eventPatternMeta('InboxMessage').matcher).toBeNull()
    expect(eventPatternMeta('MemoryUpdate').matcher).toBeNull()
    // The narrowing patterns each read their one field.
    expect(eventPatternMeta('InboxSender').matcher).toBe('sender_glob')
    expect(eventPatternMeta('InboxAddress').matcher).toBe('address_glob')
    expect(eventPatternMeta('MemoryKeyPattern').matcher).toBe('key_glob')
    expect(eventPatternMeta('ContentMatch').matcher).toBe('content_re')
  })

  it('marks only InboxSender as matcher-required, mirroring the server gate', () => {
    // The backend rejects an empty sender_glob on InboxSender (code sender_glob_required); no other
    // pattern gates its matcher. The form must block the same submit, not a different set.
    const required = EVENT_PATTERN_META.filter((p) => p.matcherRequired).map((p) => p.pattern)
    expect(required).toEqual(['InboxSender'])
  })
})

describe('eventPatternMeta', () => {
  it('resolves a known pattern to its row', () => {
    expect(eventPatternMeta('InboxSender').label).toBe('Inbox message from a sender')
  })

  it('falls back to the first row for an unknown/absent pattern rather than crashing', () => {
    // The URL is the source of truth for the pattern (?pattern=…); a hand-edited or stale value must
    // degrade to a valid default, never undefined (which would blow up pm.matcher reads in the form).
    expect(eventPatternMeta('bogus').pattern).toBe('InboxMessage')
    expect(eventPatternMeta(undefined).pattern).toBe('InboxMessage')
  })
})

describe('eventSourceIcon', () => {
  it('gives distinct icons for inbox vs memory', () => {
    // Not a value assertion beyond "they differ" — the icons are lucide components; the contract is
    // that the two sources are visually distinguishable in the option list.
    expect(eventSourceIcon('inbox')).not.toBe(eventSourceIcon('memory'))
  })

  it('falls back to the memory icon for an unknown source', () => {
    expect(eventSourceIcon('whatever')).toBe(eventSourceIcon('memory'))
  })
})

// ── actionIsSendCapable (EIAT-5 draft-by-default surfacing) ──────────────────
//
// A UI-copy heuristic, NOT a core capability flag (none exists — EIAT-3 owns the real posture in the
// mail-inbox app). It decides whether to show the draft-by-default reminder next to the action. It
// must catch the one bundled send provider today and any future `send-*` channel provider, without
// false-flagging unrelated providers whose names merely contain "send".

describe('actionIsSendCapable', () => {
  it('flags the bundled send-message provider', () => {
    expect(actionIsSendCapable('send-message')).toBe(true)
  })

  it('flags a future send-* channel provider', () => {
    expect(actionIsSendCapable('send-telegram')).toBe(true)
  })

  it('does not flag non-sending providers', () => {
    for (const p of ['bash', 'notify', 'run-prompt', 'create-task', 'webhook', 'run-workflow']) {
      expect(actionIsSendCapable(p)).toBe(false)
    }
  })

  it('does not flag a provider that merely contains "send" mid-name', () => {
    // The check is a `send-` prefix, not a substring — `resend-digest` is not a send-capable action.
    expect(actionIsSendCapable('resend-digest')).toBe(false)
  })

  it('is false for an absent provider', () => {
    expect(actionIsSendCapable(undefined)).toBe(false)
    expect(actionIsSendCapable('')).toBe(false)
  })
})
