import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { STORE_CATALOGUE, answerStubbedRead, type StubbedRead } from '../../../e2e/stubbedReads'
import { catalogApps } from '../../lib/appCatalog'
import type { AppCatalog } from '../../lib/api'

// ── The UI quality check's Store is a snapshot, not the live registry ───────────────────────────
//
// The Store lists the app registry as it stands upstream, so an edit to a listing in another
// repository changed the Store's tree in the UI quality check (`e2e/surfaces.spec.ts`) and turned
// core's main red with no core change (#3905: the Menu Bar Companion's text,
// PersonalClaw/PersonalClawApps#503). The check answers the Store's catalogue read with a snapshot
// (`STORE_CATALOGUE`, `e2e/fixtures/app-catalog.json`). This holds the committed tree to that
// snapshot, and plants a registry edit to show it never reaches the page.

/** The committed tree: the pinned image's, the only set there is (`-linux`, #3943). */
const TREE = readFileSync(join(process.cwd(), 'e2e/__aria__/apps-store-linux.aria.yml'), 'utf8')
const SNAPSHOT = ('json' in STORE_CATALOGUE ? STORE_CATALOGUE.json : null) as AppCatalog
/** A name as the tree writes it: every run of digits is `#` (`normalizeTree`, e2e/surfaceChecks.ts). */
const asTree = (s: string) => s.replace(/\d+/g, '#')
const CARDS = [...TREE.matchAll(/^\s*- button "(.+) — details"$/gm)].map((m) => m[1]).sort()

/** A route as the matrix hands one to a stubbed read, whose live answer is `live`. */
function routeAnswering(live: unknown) {
  return {
    fetch: vi.fn(async () => ({ json: async () => live })),
    fulfill: vi.fn(async (_: { json?: unknown }) => {}),
  }
}

/** The live catalogue after a registry edit: one listing's text changed, and one app added. */
function editedRegistry(): AppCatalog {
  const live = structuredClone(SNAPSHOT)
  const companion = live.gitApps!.find((a) => /companion/i.test(a.displayName ?? a.name))!
  companion.description = 'A listing edited upstream after the snapshot was taken.'
  live.gitApps!.push({ ...structuredClone(companion), name: 'planted-app', displayName: 'Planted App' })
  return live
}

describe("the UI quality check's Store", () => {
  it('reads the committed tree and a snapshot with apps in it (not vacuously green)', () => {
    expect(TREE).toMatch(/- radio "Store" \[checked\]/)
    expect(catalogApps(SNAPSHOT).length).toBeGreaterThan(20)
  })

  it("lists the snapshot's apps, every one and no other", () => {
    expect(CARDS).toEqual(catalogApps(SNAPSHOT).map((a) => asTree(a.displayName || a.name)).sort())
  })

  it("answers the Store's catalogue read and no other", () => {
    expect(STORE_CATALOGUE.url.test('http://127.0.0.1:41234/api/apps/catalog')).toBe(true)
    expect(STORE_CATALOGUE.url.test('http://127.0.0.1:41234/api/apps/catalog?refresh=1')).toBe(true)
    expect(STORE_CATALOGUE.url.test('http://127.0.0.1:41234/api/apps/sources')).toBe(false)
    expect(STORE_CATALOGUE.url.test('http://127.0.0.1:41234/api/apps/catalog/extra')).toBe(false)
  })

  it('never shows a registry edit: the page gets the snapshot, and the gateway is not asked', async () => {
    const live = editedRegistry()
    const route = routeAnswering(live)
    await answerStubbedRead(STORE_CATALOGUE, route)
    expect(route.fetch).not.toHaveBeenCalled()
    expect(route.fulfill).toHaveBeenCalledWith({ json: SNAPSHOT })
    expect(SNAPSHOT, 'the planted edit is not in the snapshot itself').not.toEqual(live)
  })

  it('a read that patches the live body would show the same edit (the plant reaches a live read)', async () => {
    const live = editedRegistry()
    const route = routeAnswering(live)
    const patched: StubbedRead = { url: STORE_CATALOGUE.url, why: 'control', patch: (body) => body }
    await answerStubbedRead(patched, route)
    expect(route.fetch).toHaveBeenCalledOnce()
    expect(route.fulfill.mock.calls[0][0].json).toEqual(live)
  })
})
