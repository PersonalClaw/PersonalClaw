// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'

// ── A tap on an alert's notification opens the buddy on it (#3897) ───────────────────────────
//
// While Buddy status is on, the shell shows the buddy's window and rings it
// (`pclaw-buddy:open-alert`). The ring carries nothing: the page opens exactly as a click on the
// orb opens it, so the state machine decides what shows, and it shows the alert the one order
// puts first. Nothing is answered by the ring.

const sent: unknown[] = []
vi.mock('../../lib/buddy/host', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  createBuddyHost: () => ({
    send: (input: unknown) => sent.push(input),
    ring: () => {},
    stop: () => {},
    answer: vi.fn(),
  }),
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))

afterEach(() => {
  sent.length = 0
  delete (window as { pclawBuddy?: unknown }).pclawBuddy
})

function buddyWindow() {
  const listeners: Array<() => void> = []
  let off = 0
  ;(window as { pclawBuddy?: unknown }).pclawBuddy = {
    pointerOver: () => {}, setRegions: () => {}, dragStart: () => {}, dragBy: () => {}, dragEnd: () => {},
    setPresentation: () => {}, openInApp: vi.fn(),
    onRing: () => () => {}, onConnection: () => () => {}, onPlacement: () => () => {}, onFullScreen: () => () => {},
    onOpenAlert: (cb: () => void) => { listeners.push(cb); return () => { off += 1 } },
  }
  return { ring: () => listeners.forEach((l) => l()), offs: () => off }
}

describe('the ring', () => {
  it('opens the buddy as a click on the orb does, and answers nothing', async () => {
    const shell = buddyWindow()
    const { BuddyPage } = await import('./BuddyPage')
    render(<BuddyPage sub="" navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />)
    act(() => shell.ring())
    expect(sent).toEqual([{ type: 'click' }])
    expect(sent.some((s) => (s as { type: string }).type === 'answer')).toBe(false)
  })

  it('is let go of when the page goes', async () => {
    const shell = buddyWindow()
    const { BuddyPage } = await import('./BuddyPage')
    const { unmount } = render(<BuddyPage sub="" navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />)
    unmount()
    expect(shell.offs()).toBe(1)
  })

  it('a shell built before the ring has none to hear, and the page draws as before', async () => {
    buddyWindow()
    delete (window as { pclawBuddy?: { onOpenAlert?: unknown } }).pclawBuddy!.onOpenAlert
    const { BuddyPage } = await import('./BuddyPage')
    expect(() => render(<BuddyPage sub="" navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />)).not.toThrow()
  })
})
