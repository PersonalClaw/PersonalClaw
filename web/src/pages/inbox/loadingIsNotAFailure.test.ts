import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── An infinite skeleton is a lie about the network ───────────────────────────────
//
// `.catch(() => null)` / `.catch(() => setS(null))` make a failed read RESOLVE, and both inbox-settings
// copies gate on falsiness — so the failure rendered as a permanent loading state. Measured with
// `GET /api/inbox/settings` at 500:
//
//   #/settings/inbox     0 editable controls · 22 shimmering skeleton nodes · no error · no retry
//   #/inbox?settings=1   the drawer's <Loading /> forever
//   sessionStorage['cache:settings:inbox'] === "null"   ← the resolved null was PERSISTED
//
// That last line is the cache-key poisoning from the `'apps'` sweep with `null` instead of `[]`, and this
// key has THREE consumers: the settings panel, the drawer copy, and the dashboard tile's `useInbox`. One
// swallow made all three unable to tell "failed" from "loaded". After: no cache entry is written, both
// panels render "Couldn't load your inbox settings" + Retry, and the tile says so in one line instead of
// shimmering.
//
// 🪤 The shared `loadErrorState` rail covers the settings copy (it uses the hook, so its `error: loadErr`
// matches). The DRAWER copy is hand-rolled and captures with `.catch(setLoadErr)`, which that rail's
// matcher does not recognise — and widening it a FOURTH time to accept a bare setter reference would make
// it match almost anything. So the drawer is asserted here instead, where the shape can be named exactly.

const PAGES = join(process.cwd(), 'src', 'pages')
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(PAGES, rel), 'utf8'))

const DRAWER = 'inbox/InboxSettingsPanel.tsx'
const PANEL = 'settings/InboxSettingsPanel.tsx'
const WIDGET = 'settings/settingsWidgets.tsx'

describe('a failed inbox-settings read is reported, not shown as loading', () => {
  it('the drawer copy captures the rejection instead of substituting null', () => {
    const src = read(DRAWER)
    expect(/inboxSettings\(\)[\s\S]{0,80}\.catch\(\(\) => setS\(null\)\)/.test(src),
      'setS(null) leaves !s true, so <Loading /> renders forever').toBe(false)
    expect(src, 'the rejection must land somewhere').toMatch(/\.catch\(setLoadErr\)/)
    expect(src, 'and be rendered').toMatch(/<LoadError what="inbox settings"/)
  })

  // The drawer's gate now names what is loading (cycle 144: `Loading` became a live region).
  it.each([[DRAWER, /if \(!s\) return <Loading what="inbox settings" \/>/], [PANEL, /return <FormSkeleton/]])(
    '%s puts the error branch before its loading gate', (rel, loadingGate) => {
      const src = read(rel)
      const errAt = src.search(/<LoadError\b/)
      const loadAt = src.search(loadingGate)
      expect(errAt, `${rel} must render LoadError`).toBeGreaterThan(-1)
      expect(loadAt, `${rel} must still have a loading gate`).toBeGreaterThan(-1)
      expect(errAt, `${rel}: after the gate, a failure spins forever`).toBeLessThan(loadAt)
    })

  it('the dashboard tile stops shimmering when the read failed', () => {
    const src = read(WIDGET)
    expect(/inboxSettings\(\)[\s\S]{0,90}\.catch\(/.test(src), 'the tile must not swallow the shared key').toBe(false)
    expect(src, 'loading must not include the failed state').toMatch(/loading=\{s === undefined && !inboxErr\}/)
    expect(src, 'and the failure must be visible in the card').toMatch(/Couldn&rsquo;t load inbox settings/)
  })

  it('reads the real files (not vacuously green)', () => {
    for (const rel of [DRAWER, PANEL, WIDGET]) expect(read(rel).length, rel).toBeGreaterThan(1000)
  })
})
