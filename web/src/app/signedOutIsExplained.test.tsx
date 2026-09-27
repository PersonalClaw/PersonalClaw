import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { api, ApiError } from '../lib/api'
import { useChatSocket } from '../lib/useChatSocket'
import { App } from './App'

// ── ledger 255: a tab whose session ended is TOLD why, once, and stops asking ─────────────────
//
// Measured on main, on a tab whose session another device signed out: every panel failed on its
// own with the gateway's bare `{"error": "token superseded"}`, each drawing its own "Couldn't
// load…", and the tab kept polling — and reconnecting its socket to — a gateway that would refuse
// it forever, every refusal a row in the security log. Now the first refusal that says the
// session is over (`403` + `X-Auth-Required: true`) replaces the whole shell with ONE screen that
// carries the gateway's sentence, and nothing more is sent.
//
// Only the modules main already has are imported here, so this file runs on main unchanged and
// its failures there are assertion failures, not a missing import.

const SENTENCE =
  'This device was signed out today at 09:14 from Settings → Devices on another device. ' +
  'To sign back in, run `personalclaw token` on the computer running PersonalClaw and open the link it prints here.'
const SIGNED_OUT = {
  error: { code: 'session_signed_out', message: SENTENCE, detail: { reason: 'signed_out_elsewhere', at: 1 } },
}

/** A fresh refusal per call — a Response body can be read once. */
function refusing(body: unknown, headers: Record<string, string> = { 'X-Auth-Required': 'true' }) {
  return vi.fn(async () => new Response(JSON.stringify(body), {
    status: 403,
    headers: { 'Content-Type': 'application/json', ...headers },
  }))
}

class FakeSocket {
  static all: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(public url: string) { FakeSocket.all.push(this) }
  close(): void {}
}

beforeEach(() => {
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('a signed-out tab shows why, in place of the app', () => {
  it('the gateway’s sentence replaces the whole shell, and "Sign in again" starts over', async () => {
    vi.stubGlobal('fetch', refusing(SIGNED_OUT))
    render(<App />)

    const heading = await screen.findByRole('heading', { name: 'You’re signed out' })
    const page = screen.getByRole('main', { name: 'You’re signed out' })
    // The sentence is the gateway's, whole: why, when, and how to sign back in.
    expect(page).toHaveAccessibleDescription(SENTENCE.replaceAll('`', ''))
    expect(screen.getByText('personalclaw token').tagName, 'the command reads as a command').toBe('CODE')
    expect(document.activeElement, 'focus lands on what replaced the page').toBe(heading)
    expect(screen.queryByRole('navigation'), 'none of the shell is left behind the notice').toBeNull()

    const reload = vi.fn()
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, reload } as Location)
    fireEvent.click(screen.getByRole('button', { name: 'Sign in again' }))
    expect(reload, 'reloading lands on the gateway’s own sign-in door').toHaveBeenCalledTimes(1)
  })

  it('a refusal the gateway could not explain still gets a true sentence, never its raw reason', async () => {
    vi.stubGlobal('fetch', refusing({ error: 'no active sessions' }))
    render(<App />)

    const page = await screen.findByRole('main', { name: 'You’re signed out' })
    expect(page).toHaveAccessibleDescription(
      'This browser is no longer signed in to PersonalClaw. Sign in again to continue.',
    )
    expect(screen.queryByText(/no active sessions/)).toBeNull()
  })
})

describe('a signed-out tab stops asking', () => {
  it('sends no request after the first refusal says the session is over', async () => {
    const fetchSpy = refusing(SIGNED_OUT)
    vi.stubGlobal('fetch', fetchSpy)

    await expect(api.devices()).rejects.toBeInstanceOf(ApiError)
    await expect(api.devices()).rejects.toMatchObject({ status: 403, message: SENTENCE })
    await expect(api.authLogout()).rejects.toBeInstanceOf(ApiError)
    expect(fetchSpy, 'every later poll could only be refused, and logged').toHaveBeenCalledTimes(1)
  })

  it('a 403 refusing an ACTION is not a sign-out: the next request is still sent', async () => {
    // No X-Auth-Required: the route refused this call, the session itself is fine.
    const fetchSpy = refusing({ error: 'not permitted' }, {})
    vi.stubGlobal('fetch', fetchSpy)

    await expect(api.devices()).rejects.toBeInstanceOf(ApiError)
    await expect(api.devices()).rejects.toBeInstanceOf(ApiError)
    expect(fetchSpy).toHaveBeenCalledTimes(2)
  })

  it('does not reconnect its live socket once the session is over', async () => {
    function Consumer() {
      useChatSocket(() => {})
      return null
    }
    vi.useFakeTimers()
    try {
      render(<Consumer />)
      const sock = FakeSocket.all.at(-1)
      expect(sock, 'the consumer opened the tab’s socket').toBeDefined()
      act(() => { sock?.onopen?.() })

      vi.stubGlobal('fetch', refusing(SIGNED_OUT))
      await expect(api.devices()).rejects.toBeInstanceOf(ApiError)
      const opened = FakeSocket.all.length
      act(() => { sock?.onclose?.() })
      act(() => { vi.advanceTimersByTime(60_000) })
      expect(FakeSocket.all.length, 'every upgrade would be refused, and logged').toBe(opened)
    } finally {
      vi.useRealTimers()
    }
  })
})
