import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

// ── Exporting everything asks for a recent sign-in, then downloads (#4102) ──
//
// The whole-home export hands out as much as a minted credential, so the gateway answers it from a
// device that signed in more than ten minutes ago with `401 fresh_sign_in_required`
// (`dashboard/owner_presence.py`). The panel's part: that answer opens the sign-in prompt, and once
// the owner signs in again the export is sent again and downloads. Driven through the real
// `api.durabilityExport` over a stubbed `fetch`, so what is asserted is the wire.

const promptSpy = vi.hoisted(() => vi.fn(async (_opts: unknown): Promise<Record<string, string> | null> => null))
const notifySpy = vi.hoisted(() => vi.fn())
vi.mock('../../ui/dialog', () => ({
  promptForm: (opts: unknown) => promptSpy(opts),
  alertDialog: async () => undefined,
  confirm: async () => true,
}))
vi.mock('../../app/appSdk', () => ({ notify: (...args: unknown[]) => notifySpy(...args) }))

import { PortabilityPanel } from './PortabilityPanel'
import { resetSignedOutForTests, signedOutState } from '../../lib/signedOut'

const SENTENCE =
  'Downloading a copy of everything PersonalClaw keeps needs a sign-in from the last 10 minutes. '
  + 'This device signed in yesterday at 09:14. Confirm it’s you by signing in again with your password.'

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const asked = () => json(401, {
  error: {
    code: 'fresh_sign_in_required',
    message: SENTENCE,
    detail: { action: 'Downloading a copy of everything PersonalClaw keeps', window_secs: 600, password: true, second_factor: false },
  },
})
const zip = () => new Response(new Blob(['PK']), { status: 200, headers: { 'Content-Type': 'application/zip' } })

interface Call { url: string; method: string }

function gateway(answers: Response[]): Call[] {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET' })
    const next = answers.shift()
    if (!next) throw new Error(`unexpected request to ${url}`)
    return next
  }))
  return calls
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:http://localhost/export-1')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  promptSpy.mockReset()
  notifySpy.mockReset()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  resetSignedOutForTests()
})

async function exportEverything() {
  render(<PortabilityPanel />)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Export everything/ })) })
}

describe('exporting everything from a device that signed in long ago', () => {
  it('asks the owner to sign in again, then sends the export again and downloads it', async () => {
    const calls = gateway([asked(), json(200, { ok: true }), zip()])
    promptSpy.mockResolvedValueOnce({ password: 'correct-horse-battery-staple' })

    await exportEverything()

    const opts = promptSpy.mock.calls[0][0] as { title: string }
    expect(opts.title).toBe('Confirm it’s you')
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'POST /api/durability/export',
      'POST /api/auth/confirm',
      'POST /api/durability/export',
    ])
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    expect(notifySpy).toHaveBeenCalledWith('Everything export downloaded', 'success')
    // A request answered "sign in again" is not a sign-out.
    expect(signedOutState()).toBeNull()
  })

  it('closing the prompt sends nothing more and downloads nothing', async () => {
    const calls = gateway([asked()])

    await exportEverything()

    expect(calls).toHaveLength(1)
    expect(URL.createObjectURL).not.toHaveBeenCalled()
    expect(notifySpy).toHaveBeenCalledWith(
      'Couldn\'t export everything: Not done — you didn’t sign in again.', 'error',
    )
  })
})
