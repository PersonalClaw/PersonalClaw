import { describe, it, expect } from 'vitest'
import { toneChipBg, kindMeta } from './notificationMeta'

// ── Consolidation guard (S2/T2.2) ──────────────────────────────────────────
// toneChipBg() replaced a verbatim-duplicated inline style in NotificationsPage
// (Row) and NotificationBell (ShadeRow). This assertion pins the EXACT output so
// the consolidation stays byte-identical (zero visual change) and a future edit
// can't silently drift one call site.
//
// Its sibling `unreadRail()` is gone — same two consumers, same single home, but as
// `UnreadRail.tsx`: as an inline box-shadow it was suppressing both rows' focus ring
// (`UnreadRail.test.tsx` carries that story and the guard).

describe('notificationMeta shared visual helpers', () => {
  it('toneChipBg: the exact 16% tint over transparent', () => {
    expect(toneChipBg('var(--color-warn)')).toBe('color-mix(in srgb, var(--color-warn) 16%, transparent)')
  })

  it('kindMeta tones are token-routed (CSS vars, never raw hex)', () => {
    for (const kind of ['info', 'error', 'success', 'unknown-kind']) {
      expect(kindMeta(kind).tone).toMatch(/^var\(--color-/)
    }
  })
})

// ── No raw-key leakage for any backend-emitted kind ────────────────────────
// The KINDS map covered 12 keys while the backend registry reaches 31 wire strings, so
// `proposal`, `failed`, `digest`, … fell through to the raw lowercase key: the filter row
// rendered "Info", "Subagent", "Success" beside a bare "proposal".
//
// Hardcoded rather than read from Python at runtime (vitest has no interpreter): this is
// every wire string reachable from src/personalclaw/notification_kinds.py — each
// registration's bare `kind`, plus the `_LEGACY_FLAT` and `_ATTENTION_FLAT` strings
// emitters actually hand to `state.notify()`. Adding a kind there without a row in KINDS
// re-opens the bug, and this list is what makes that a red test.
const BACKEND_KINDS = [
  // bare `kind` of every registration
  'agent_request', 'alert', 'complete', 'digest', 'error', 'failed', 'fired', 'generic',
  'info', 'message', 'needs_input', 'progress', 'proposal', 'result', 'retire',
  'route_drift', 'session', 'stalled', 'status', 'subagent', 'success', 'update', 'warning',
  // legacy + attention flat wire strings
  'agent', 'app.route.drift', 'app_update', 'cron', 'feedback_retire', 'heartbeat', 'hook',
  'inbox_alert', 'loop', 'schedule',
]

describe('kindMeta covers every kind the backend emits', () => {
  it('no backend kind renders as its raw lowercase key', () => {
    const leaked = BACKEND_KINDS.filter((k) => kindMeta(k).label === k)
    expect(leaked).toEqual([])
  })

  it('proposal uses the registry\'s declared display name', () => {
    // notification_kinds.py: NotificationKind("skills", "proposal", "Skill proposal", …)
    expect(kindMeta('proposal').label).toBe('Skill proposal')
  })

  it('every backend kind yields a usable label / icon / token-routed tone', () => {
    for (const kind of BACKEND_KINDS) {
      const km = kindMeta(kind)
      expect(km.label.length, kind).toBeGreaterThan(0)
      expect(km.icon, kind).toBeDefined()
      expect(km.tone, kind).toMatch(/^var\(--color-/)
    }
  })

  it('a genuinely unknown kind still hits the fallback with a usable KindMeta', () => {
    const km = kindMeta('zzz_not_a_kind')
    expect(km.label).toBe('zzz_not_a_kind')   // fail-open: echo it rather than hide it
    expect(km.icon).toBeDefined()
    expect(km.tone).toMatch(/^var\(--color-/)
    // and an empty kind degrades to a generic noun, not an empty chip
    expect(kindMeta('').label).toBe('Notification')
  })
})
