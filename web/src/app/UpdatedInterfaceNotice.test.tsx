/**
 * An open page says when the gateway now serves a newer interface, and offers Reload (#5901).
 *
 * 🔴 Measured before: an update built a new SPA and restarted the gateway, the update sheet said
 * "Update complete" and closed, and the page went on running the old build. Nothing said so until
 * a lazy chunk the page had never loaded was gone. The gateway now says which build it serves
 * (`servedBuild.ts`), and this notice is what the page does with it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WsMessage } from '../lib/useChatSocket'

const sockets: Array<(m: WsMessage) => void> = []
vi.mock('../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: WsMessage) => void) => { sockets.push(onMessage) },
}))

const { UpdatedInterfaceNotice, NOTICE_MS, NOTICE_TEXT } = await import('./UpdatedInterfaceNotice')
const { NOTICED_KEY, SPA_BUILD_META } = await import('./servedBuild')
const { setUpdateSheetOpen } = await import('../ui/updateSheetOpen')

const BOOTED = '1111aaaa2222bbbb'
const NEWER = '3333cccc4444dddd'

/** The gateway's frame on a socket open: the build it serves. */
function serves(build: string) {
  act(() => { for (const s of sockets) s({ type: 'spa_build', data: { build } }) })
}

function boot(build: string | null = BOOTED) {
  document.head.querySelectorAll(`meta[name="${SPA_BUILD_META}"]`).forEach((m) => m.remove())
  if (build !== null) {
    const meta = document.createElement('meta')
    meta.name = SPA_BUILD_META
    meta.content = build
    document.head.appendChild(meta)
  }
}

const reload = vi.fn()
const card = () => document.querySelector('[data-updated-interface]')
const live = () => document.querySelector('[role="status"][aria-live="polite"]')

beforeEach(() => {
  sockets.length = 0
  sessionStorage.clear()
  reload.mockReset()
  boot()
})
afterEach(() => {
  vi.useRealTimers()
  setUpdateSheetOpen(false)
  boot(null)
})

describe('the notice shows when the gateway serves another build than this page runs', () => {
  it('🔴 says the interface was updated and offers Reload, announced politely', () => {
    render(<UpdatedInterfaceNotice reload={reload} />)
    expect(card()).toBeNull()
    expect(live()).not.toBeNull()  // mounted before its words, or it is not reliably announced

    serves(NEWER)

    expect(card()).not.toBeNull()
    // With motion allowed it fades; `UpdatedInterfaceNotice.reducedMotion.test.tsx` is the other half.
    expect(card()?.getAttribute('data-updated-interface')).toBe('fades')
    expect(NOTICE_TEXT).toBe("PersonalClaw's interface was updated. The changes take effect when you reload.")
    expect(live()?.textContent).toBe(NOTICE_TEXT)
    fireEvent.click(screen.getByRole('button', { name: /^Reload/ }))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('stays away on the build this page runs, with no build to compare, and under the dev server', () => {
    render(<UpdatedInterfaceNotice reload={reload} />)
    serves(BOOTED)
    serves('')
    expect(card()).toBeNull()

    boot(null)  // the dev server stamps no build
    serves(NEWER)
    expect(card()).toBeNull()
    expect(live()?.textContent).toBe('')
  })

  it('takes no focus', () => {
    render(<><input aria-label="draft" /><UpdatedInterfaceNotice reload={reload} /></>)
    const draft = screen.getByLabelText('draft')
    draft.focus()
    serves(NEWER)
    expect(card()).not.toBeNull()
    expect(document.activeElement).toBe(draft)
  })

  it('shows once per build in a tab, and again for a later build', async () => {
    render(<UpdatedInterfaceNotice reload={reload} />)
    serves(NEWER)
    expect(sessionStorage.getItem(NOTICED_KEY)).toBe(NEWER)
    fireEvent.click(screen.getByRole('button', { name: /^Dismiss/ }))
    await waitFor(() => expect(card()).toBeNull())

    serves(NEWER)  // the next reconnect says the same
    expect(card()).toBeNull()

    serves('5555eeee6666ffff')
    expect(card()).not.toBeNull()
  })

  it('a build this tab was told about before, in an earlier page of it, does not show again', () => {
    sessionStorage.setItem(NOTICED_KEY, NEWER)
    render(<UpdatedInterfaceNotice reload={reload} />)
    serves(NEWER)
    expect(card()).toBeNull()
  })

  it('waits while the update sheet is open, and shows when it closes', () => {
    setUpdateSheetOpen(true)
    render(<UpdatedInterfaceNotice reload={reload} />)
    serves(NEWER)
    expect(card()).toBeNull()
    expect(live()?.textContent).toBe('')

    act(() => setUpdateSheetOpen(false))
    expect(card()).not.toBeNull()
  })
})

describe('it leaves on its own after a while, but never while you are on it', () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }) })

  /** Run the notice's clock forward. */
  function after(ms: number) {
    act(() => { vi.advanceTimersByTime(ms) })
  }

  // Whether the notice is up, read off its polite region, which empties the moment it leaves. The
  // card itself stays in the DOM while it fades, so "the card is there" holds for a notice already
  // leaving, and a hold that never held passed with it.
  const up = () => live()?.textContent === NOTICE_TEXT

  it('leaves after NOTICE_MS', async () => {
    expect(NOTICE_MS).toBe(30_000)
    render(<UpdatedInterfaceNotice reload={reload} />)
    serves(NEWER)
    after(NOTICE_MS - 1)
    expect(up()).toBe(true)
    after(1)
    expect(up()).toBe(false)
    vi.useRealTimers()
    await waitFor(() => expect(card()).toBeNull())
  })

  it('waits while the pointer is over it, and gives the whole time again after', () => {
    render(<UpdatedInterfaceNotice reload={reload} />)
    serves(NEWER)
    fireEvent.pointerEnter(card()!)
    after(NOTICE_MS * 3)
    expect(up(), 'it left with the pointer on it').toBe(true)

    fireEvent.pointerLeave(card()!)
    after(NOTICE_MS - 1)
    expect(up()).toBe(true)
    after(1)
    expect(up()).toBe(false)
  })

  it('waits while focus is inside it', () => {
    render(<UpdatedInterfaceNotice reload={reload} />)
    serves(NEWER)
    const button = screen.getByRole('button', { name: /^Reload/ })
    act(() => button.focus())
    after(NOTICE_MS * 3)
    expect(up(), 'it left with focus in it').toBe(true)

    act(() => button.blur())
    after(NOTICE_MS - 1)
    expect(up()).toBe(true)
    after(1)
    expect(up()).toBe(false)
  })
})
