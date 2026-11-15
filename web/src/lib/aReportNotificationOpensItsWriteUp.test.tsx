import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { itemRouteForNote, useNativeNotifications } from './nativeNotifications'

// ── A report's notification opens its write-up ──────────────────────────────────────────────────
//
// A scheduled report's finding is one Inbox row, and its notification is a view of that row: the
// gateway copies the row's refs into the note (`inbox._notify_view`), so the note carries the
// write-up its run rewrote, `knowledge_item_id`. A tap still landed on the `knowledge` source's
// one route, the Knowledge page, and the tap handler opens only those routes or one this window's
// banners issued, so no edit to the source table could have taken it to the write-up. The note's
// own route is built here, for an id in a shape the store mints a knowledge item's id in only (a
// uuid4, or the persist step's 12 hex characters, which a report's write-up has), raised with its
// banner and issued, as an alert's route is; any other note keeps its source's route, and a tap on
// an item route no banner issued is still dropped.

let deliver: ((m: { type: string; data: Record<string, unknown> }) => void) | null = null
vi.mock('./useChatSocket', () => ({
  useChatSocket: (cb: (m: { type: string; data: Record<string, unknown> }) => void) => { deliver = cb },
}))
vi.mock('./api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: { waiting: () => Promise.resolve({ waiting: [], unreadable: [] }) },
}))
vi.mock('../ui/buddy/paintOrb', () => ({ stillOrbPng: () => 'data:image/png;base64,' }))

function fakeBridge() {
  const shown: Array<{ title: string; body: string; route: string }> = []
  const taps: Array<(p: { route: string }) => void> = []
  ;(window as unknown as { pclawDesktop: unknown }).pclawDesktop = {
    capabilities: { names: () => [], probe: vi.fn(), snapshot: vi.fn(), request: vi.fn(), on: () => () => {} },
    notifications: {
      show: (n: { title: string; body: string; route: string }) => { shown.push(n); return Promise.resolve({ ok: true, route: n.route }) },
      on: (cb: (p: { route: string }) => void) => { taps.push(cb); return () => {} },
    },
  }
  return { shown, tap: (route: string) => taps.forEach((t) => t({ route })) }
}

const live = { native: { deliver: true, reason: '' } }
const finding = (knowledge_item_id: unknown) => ({
  kind: 'knowledge/research_finding', source: 'knowledge', title: 'Weekly perf digest', body: 'Two regressions this week.',
  research_report_id: 'rpt-1a2b3c4d', knowledge_item_id, ...live,
})

beforeEach(() => { deliver = null })
afterEach(() => { delete (window as unknown as { pclawDesktop?: unknown }).pclawDesktop })

describe("a report's notification", () => {
  it('🔑 raises its banner with the write-up\'s route, and its tap opens the write-up', () => {
    const id = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'
    const bridge = fakeBridge()
    const navigate = vi.fn()
    renderHook(() => useNativeNotifications(navigate))
    deliver!({ type: 'notification', data: finding(id) })
    expect(bridge.shown).toEqual([{ title: 'Weekly perf digest', body: 'Two regressions this week.', route: `knowledge/item/${id}` }])
    bridge.tap(`knowledge/item/${id}`)
    expect(navigate).toHaveBeenCalledWith(`knowledge/item/${id}`)
  })

  it('🔑 a tap on an item route no banner issued does nothing', () => {
    const bridge = fakeBridge()
    const navigate = vi.fn()
    renderHook(() => useNativeNotifications(navigate))
    bridge.tap('knowledge/item/1b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e')
    expect(navigate).not.toHaveBeenCalled()
    // The source's own route still opens.
    bridge.tap('knowledge')
    expect(navigate).toHaveBeenCalledWith('knowledge')
  })

  it('🔑 a knowledge note without the id, or with one of another shape, keeps the knowledge route', () => {
    const bridge = fakeBridge()
    const navigate = vi.fn()
    renderHook(() => useNativeNotifications(navigate))
    for (const id of [undefined, '', 'k-0a1b2c3d', '../settings', '0A1B2C3D-4E5F-4A6B-8C7D-9E0F1A2B3C4D', 7, '3f9a0c1d2e4', '3f9a0c1d2e4bc']) {
      deliver!({ type: 'notification', data: finding(id) })
    }
    expect(bridge.shown.map((n) => n.route)).toEqual(Array(8).fill('knowledge'))
    bridge.tap('knowledge/item/k-0a1b2c3d')
    expect(navigate).not.toHaveBeenCalled()
  })

  it('🔑 a write-up the persist step made, 12 hex characters, opens too', () => {
    // A report's run writes its write-up through the persist step, which gives a new item
    // `uuid4().hex[:12]`: the shape a real report's note carries.
    const bridge = fakeBridge()
    const navigate = vi.fn()
    renderHook(() => useNativeNotifications(navigate))
    deliver!({ type: 'notification', data: finding('3f9a0c1d2e4b') })
    expect(bridge.shown.map((n) => n.route)).toEqual(['knowledge/item/3f9a0c1d2e4b'])
    bridge.tap('knowledge/item/3f9a0c1d2e4b')
    expect(navigate).toHaveBeenCalledWith('knowledge/item/3f9a0c1d2e4b')
  })

  it('only a knowledge note names its write-up: another source keeps its own route', () => {
    const id = '2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f'
    expect(itemRouteForNote({ source: 'knowledge', knowledge_item_id: id })).toBe(`knowledge/item/${id}`)
    expect(itemRouteForNote({ source: 'inbox', knowledge_item_id: id })).toBe('')
    expect(itemRouteForNote({ knowledge_item_id: id })).toBe('')
  })
})
