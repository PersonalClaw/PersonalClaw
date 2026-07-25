import { describe, it, expect } from 'vitest'
import { unreadRail, toneChipBg, kindMeta } from './notificationMeta'

// ── Consolidation guard (S2/T2.2) ──────────────────────────────────────────
// unreadRail() and toneChipBg() replaced verbatim-duplicated inline styles in
// NotificationsPage (Row) and NotificationBell (ShadeRow). These assertions
// pin the EXACT output so the consolidation stays byte-identical (zero visual
// change) and a future edit can't silently drift one call site.

describe('notificationMeta shared visual helpers', () => {
  it('unreadRail: unread → the exact inset accent rail; acked → undefined', () => {
    expect(unreadRail('var(--color-primary)', false)).toEqual({
      boxShadow: 'inset 2px 0 0 0 var(--color-primary)',
    })
    expect(unreadRail('var(--color-primary)', true)).toBeUndefined()
  })

  it('toneChipBg: the exact 16% tint over transparent', () => {
    expect(toneChipBg('var(--color-warn)')).toBe('color-mix(in srgb, var(--color-warn) 16%, transparent)')
  })

  it('kindMeta tones are token-routed (CSS vars, never raw hex)', () => {
    for (const kind of ['info', 'error', 'success', 'unknown-kind']) {
      expect(kindMeta(kind).tone).toMatch(/^var\(--color-/)
    }
  })
})
