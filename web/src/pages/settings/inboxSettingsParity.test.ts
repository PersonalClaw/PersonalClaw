import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Two panels for one settings surface, and one of them was missing controls ─
//
// `InboxSettingsPanel` is defined TWICE — `pages/settings/` (reached via Settings → Inbox, the
// canonical home for every other inbox setting) and `pages/inbox/` (the in-context side panel).
// Both are live and imported by different pages, so neither is dead code; the drift was that
// they did not offer the same controls.
//
// Two config flags existed ONLY in the side panel:
//
//     inbox.enabled                        "Poll message sources"
//     inbox.engagement_ranking_enabled     "Engagement ranking"
//
// Both are real, consumed flags — `handlers_inbox.py` gates the ranking blend on the second
// ("GATED behind inbox.engagement_ranking_enabled"), and 7 + 3 backend references respectively.
// So a user who went to Settings → Inbox looking for them could not turn poll collection on and
// could not find the ranking switch at all. Verified live after the fix: both toggles render on
// #/settings/inbox, and a real click on "Engagement ranking" issues
// `PATCH /api/config/personalclaw {"path":"inbox.engagement_ranking_enabled","value":true}` and
// the value PERSISTS (false → true, then restored).
//
// This is a source scan because the invariant is "these two files offer the same controls", which
// is a relationship between files — a render test of either one in isolation cannot see it, and
// mounting both needs a live config fetch that jsdom has no answer for.
//
// NOT asserted: identical label text or identical layout. The side panel is a compact drawer and
// the settings page is a full form; `Retention (days)` vs a `Retention` row with a `days` suffix
// is the same control presented for its context, and forcing those to match would be flattening
// a real distinction. What must match is WHICH SETTINGS a user can reach.

const PAGES = join(process.cwd(), 'src/pages')
const SETTINGS = join(PAGES, 'settings/InboxSettingsPanel.tsx')
const DRAWER = join(PAGES, 'inbox/InboxSettingsPanel.tsx')

/** Config-flag paths a panel writes through the config PATCH. */
function patchedFlags(src: string): Set<string> {
  return new Set([...src.matchAll(/patchConfig\(\s*'([^']+)'/g)].map((m) => m[1]))
}
/** Fields a panel writes to the inbox entity-settings store. */
function savedFields(src: string): Set<string> {
  return new Set([...src.matchAll(/patch\(\{\s*([a-z_]+):/g)].map((m) => m[1]))
}

describe('the two InboxSettingsPanel implementations', () => {
  const settings = readFileSync(SETTINGS, 'utf8')
  const drawer = readFileSync(DRAWER, 'utf8')

  it('both files exist and are non-trivial (guards a silently-empty scan)', () => {
    expect(settings.length).toBeGreaterThan(500)
    expect(drawer.length).toBeGreaterThan(500)
  })

  it('Settings → Inbox reaches every config flag the drawer does', () => {
    const missing = [...patchedFlags(drawer)].filter((f) => !patchedFlags(settings).has(f))
    expect(
      missing,
      'These inbox config flags are editable ONLY from the in-context drawer, so a user who ' +
        'goes to Settings → Inbox — where every other inbox setting lives — cannot reach them:\n  ' +
        missing.join('\n  '),
    ).toEqual([])
  })

  it('Settings → Inbox reaches every stored field the drawer does', () => {
    const missing = [...savedFields(drawer)].filter((f) => !savedFields(settings).has(f))
    expect(missing, `entity-settings fields missing from the canonical panel: ${missing}`).toEqual([])
  })

  it('writes config flags through patchConfig, never the entity store', () => {
    // `inbox.enabled` / `inbox.engagement_ranking_enabled` live in config.json, not the inbox
    // entity-settings store. Routing them through `saveInboxSettings` would write somewhere the
    // runtime never reads — a toggle that flips, saves, and changes nothing.
    for (const [name, src] of [['settings', settings], ['drawer', drawer]] as const) {
      for (const flag of ['enabled', 'engagement_ranking_enabled']) {
        expect(
          savedFields(src).has(flag),
          `${name} panel writes ${flag} to the entity store; it belongs in patchConfig`,
        ).toBe(false)
      }
    }
  })
})
