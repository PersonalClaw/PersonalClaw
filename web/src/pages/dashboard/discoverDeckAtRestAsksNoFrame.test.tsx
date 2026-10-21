import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'

// ── Home's Discover deck asks for no animation frame when a read answers ─────────────────────────
//
// `web/REDESIGN.md` §8: an idle Home asks for no animation frame. Every read Home makes lands in one
// context, so each answer re-renders every widget that reads it, Discover among them, even when
// nothing it shows has changed: the 8 s loops poll answers the same empty list, and the 20 s status
// read always changes (it carries the uptime). The deck's cards are `layout` elements, and a
// `layout` element with no `layoutDependency` measures itself on every render, which asks the
// motion library for a frame. Measured in a browser on an idle #/dashboard: a frame after each
// answer, from the layout projection, once the button's sheen no longer asked for one.
//
// The deck moves when a card's depth changes (the front one is dismissed and the next rises), so
// the depth is what each card measures on; the promotion is held to still ask for frames.

const frames = vi.hoisted(() => {
  const state = { asked: 0 }
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) => {
    state.asked += 1
    return setTimeout(() => cb(performance.now()), 0) as unknown as number
  }) as typeof requestAnimationFrame
  globalThis.cancelAnimationFrame = ((id: number) => clearTimeout(id)) as typeof cancelAnimationFrame
  return state
})

const deck = vi.hoisted(() => ({ dismissed: new Set<string>(), uptime: 0 }))

vi.mock('../../lib/api', async (orig) => {
  const tip = (id: string) => ({ id, area: 'chat', title: `Tip ${id}`, lesson: `The lesson of ${id}.`, try_it: { label: 'Try it', route: 'chat' } })
  const discover = () => {
    const tips = ['a', 'b', 'c'].filter((id) => !deck.dismissed.has(id)).map(tip)
    return { enabled: true, areas: [{ area: 'chat', tips }], visible_count: tips.length, total: 3, dismissed_count: deck.dismissed.size, restorable_count: 0 }
  }
  return {
    ...(await orig<Record<string, unknown>>()),
    api: {
      approvals: () => Promise.resolve([]),
      inboxOpen: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [] }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [] }),
      status: () => Promise.resolve({ uptime: `${(deck.uptime += 20)}s` }),
      discover: () => Promise.resolve(discover()),
      dismissDiscoverTip: (id: string) => { deck.dismissed.add(id); return Promise.resolve({ ok: true, dismissed: [...deck.dismissed] }) },
    },
  }
})
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))

const { DashboardLiveProvider, useDashboardLive } = await import('./DashboardLive')
const { Discover } = await import('./widgets/Discover')

let live: ReturnType<typeof useDashboardLive> | null = null
function Grab() { live = useDashboardLive(); return null }

/** Let every frame asked for run, and the frames those ask for, until none is left. */
async function settle() {
  for (let i = 0; i < 40; i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)) })
}

async function mountDeck() {
  deck.dismissed.clear()
  const view = render(
    <DashboardLiveProvider>
      <Grab />
      <Discover navigate={() => {}} sub="" navEpoch={0} query={{}} setQuery={() => {}} />
    </DashboardLiveProvider>,
  )
  await settle()
  const front = () => view.container.querySelector('[aria-hidden="false"]')?.textContent ?? ''
  expect(front(), 'the deck drew its front card').toContain('Tip a')
  // The counter is wired only if the motion library loaded after it: the deck's entrance asks for frames.
  expect(frames.asked, 'frames asked for by the mount: the counter reaches the motion library').toBeGreaterThan(0)
  return { view, front }
}

afterEach(() => { live = null })

describe("Home's Discover deck at rest", () => {
  it('asks for no frame when Home reads everything again, the deck unchanged and the status moved on', async () => {
    const { view } = await mountDeck()
    frames.asked = 0
    for (let i = 0; i < 3; i++) {
      act(() => live!.refreshAll())
      await settle()
    }
    expect(frames.asked, 'animation frames asked for by three answers that move nothing in the deck').toBe(0)
    view.unmount()
  })

  it('still measures its cards when the front one is dismissed and the next rises', async () => {
    const { view, front } = await mountDeck()
    // The layout projection reads each card's box to animate it from where it was; a card that
    // skipped it would jump into the front card's place. Counted on the cards that stay.
    const measured = new Set<string>()
    const box = HTMLElement.prototype.getBoundingClientRect
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      // A card is the deck's element carrying `aria-hidden` (false on the front one).
      if (this.hasAttribute('aria-hidden')) measured.add(this.textContent ?? '')
      return box.call(this)
    })
    await act(async () => { await live!.dismissDiscoverTip('a') })
    await settle()
    spy.mockRestore()
    expect(front(), 'the next card is at the front').toContain('Tip b')
    expect([...measured].some((t) => t.includes('Tip b')), 'the card that rose was measured for its move').toBe(true)
    view.unmount()
  })
})
