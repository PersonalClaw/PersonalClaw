import { it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A microphone error on the loop front door stays until it is dismissed ─────────────────────
//
// 🔴 Before: the loop composer cleared a microphone error six seconds after showing it — the one
// error it cleared on its own — as the chat composer did with every notice. An error stays until
// the user dismisses it or sends again, the rule `ui/composer/ComposerNotice` holds both
// composers to. (Sending already cleared it here: starting a loop takes the notice down.)
//
// Driven through the real LoopComposer: the Voice input button, a browser that blocks the
// microphone, then time passing and the Dismiss.

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const api = new Proxy({} as Record<string, unknown>, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  return { ...real, api }
})

const BLOCKED = 'Microphone access was blocked — allow it in your browser to use voice input.'

let LoopComposer: typeof import('./LoopComposer')['LoopComposer']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  // Faked, and moving with real time too, so the page's own waits run while the test can jump
  // past the six seconds that used to clear the error.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  // jsdom ships no matchMedia; the composer reads it for its mobile breakpoint.
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  })
  // A browser that refuses the microphone, as it does when the user has blocked it.
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: vi.fn(async () => {
        throw Object.assign(new Error('Permission denied'), { name: 'NotAllowedError' })
      }),
    },
  })
  ;({ LoopComposer } = await import('./LoopComposer'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.useRealTimers() })

it('a microphone error stays past the six seconds that used to clear it, until it is dismissed', async () => {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
  render(
    <AppearanceProvider>
      <LoopComposer onCreated={() => {}} onHistory={() => {}} />
    </AppearanceProvider>,
  )

  await user.click(await screen.findByRole('button', { name: 'Voice input' }))
  const alert = (await screen.findByText(BLOCKED)).closest<HTMLElement>('[role="alert"]')
  expect(alert, 'announced as an alert').not.toBeNull()

  act(() => { vi.advanceTimersByTime(60_000) })

  expect(screen.queryByText(BLOCKED), 'still there a minute on').not.toBeNull()
  await user.click(within(alert!).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByText(BLOCKED)).toBeNull()
})
