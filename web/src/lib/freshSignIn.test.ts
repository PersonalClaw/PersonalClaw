/**
 * A write that needs a recent sign-in asks the owner to sign in again, and then carries on.
 *
 * The gateway answers a write that mints a credential, or makes sign-in less strict, from a device
 * that signed in more than ten minutes ago with `401 fresh_sign_in_required`
 * (`dashboard/owner_presence.py`), saying in `error.detail` whether the door is the password (and
 * whether a code goes with it) or a new link from `personalclaw token`. These tests drive the real
 * `api.devicePairStart` through a stubbed `fetch`, so what is asserted is the wire: the prompt the
 * owner sees, the request that signs this device in again, and the write sent once more.
 */
import { createElement, type ReactNode } from 'react'
import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from './api'
import { SignInNotRenewed, freshSignInAsked, tokenFromLink } from './freshSignIn'
import { resetSignedOutForTests, signedOutState } from './signedOut'

const promptSpy = vi.hoisted(() => vi.fn(async (_opts: unknown): Promise<Record<string, string> | null> => null))
const alertSpy = vi.hoisted(() => vi.fn(async (_opts: unknown) => undefined))
vi.mock('../ui/dialog', () => ({
  promptForm: (opts: unknown) => promptSpy(opts),
  alertDialog: (opts: unknown) => alertSpy(opts),
  confirm: async () => true,
}))

const SENTENCE =
  'Pairing a device needs a sign-in from the last 10 minutes. This device signed in yesterday at 09:14. Confirm it’s you by signing in again with your password.'
const LINK_SENTENCE =
  'Pairing a device needs a sign-in from the last 10 minutes. Confirm it’s you: run `personalclaw token` on the computer running PersonalClaw, then open the link it prints on this device.'
const PAIRED = { code: 'ABCD-EFGH', pairing_url: 'http://192.168.1.40:10000/pair?code=ABCD-EFGH', expires_at: 1, expires_in: 300 }

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
}

/** The gateway's "sign in again" answer as this page gets it: a write says it asks the owner
 *  (`X-PersonalClaw-Consent: ask`), so the question comes back as a 200 marked as one
 *  (`dashboard/consent_ask.py`), never as a failed request in the console. *status* 401 is what
 *  any other client gets. */
function asked(detail: { password: boolean; second_factor?: boolean }, message = SENTENCE, status = 200): Response {
  return json(status, {
    error: {
      code: 'fresh_sign_in_required',
      message,
      detail: { action: 'Pairing a device', window_secs: 600, second_factor: false, ...detail },
    },
  }, status === 200 ? { 'X-PersonalClaw-Consent-Asked': '1' } : {})
}

/** What a dialog's body shows: its text, as the owner reads it. */
function shown(body: unknown): string {
  return render(createElement('div', null, body as ReactNode)).container.textContent ?? ''
}

/** The pieces of a dialog's body shown as code. */
function codes(body: unknown): string[] {
  return [...render(createElement('div', null, body as ReactNode)).container.querySelectorAll('code')].map((c) => c.textContent ?? '')
}

interface Call { url: string; method: string; body: unknown }

/** A gateway that answers each request in turn from *answers*, recording what was sent. */
function gateway(answers: Response[]): Call[] {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const next = answers.shift()
    if (!next) throw new Error(`unexpected request to ${url}`)
    return next
  }))
  return calls
}

afterEach(() => {
  promptSpy.mockReset()
  alertSpy.mockReset()
  vi.unstubAllGlobals()
  resetSignedOutForTests()
})

describe('freshSignInAsked', () => {
  it('reads the gateway’s answer, and nothing else', () => {
    const e = new ApiError(SENTENCE, 401, 'fresh_sign_in_required', { password: true, second_factor: true })
    expect(freshSignInAsked(e)).toEqual({ message: SENTENCE, password: true, secondFactor: true })
    expect(freshSignInAsked(new ApiError('Wrong', 401, 'auth_invalid_credentials'))).toBeNull()
    expect(freshSignInAsked(new Error('network'))).toBeNull()
  })
})

describe('tokenFromLink', () => {
  it('takes only the token from a pasted link, and a bare token as it is', () => {
    expect(tokenFromLink('  http://localhost:10000/?token=abc.def#/settings ')).toBe('abc.def')
    expect(tokenFromLink('abc.def')).toBe('abc.def')
    expect(tokenFromLink('https://example.com/elsewhere')).toBe('')
    expect(tokenFromLink('not a link')).toBe('')
  })
})

describe('signing in again with the password', () => {
  it('asks for the password, confirms it, and sends the write again', async () => {
    const calls = gateway([asked({ password: true }), json(200, { ok: true }), json(200, PAIRED)])
    promptSpy.mockResolvedValueOnce({ password: 'correct-horse-battery-staple' })

    await expect(api.devicePairStart()).resolves.toEqual(PAIRED)

    const opts = promptSpy.mock.calls[0][0] as { title: string; body: unknown; fields: { name: string; type?: string }[] }
    expect(opts.title).toBe('Confirm it’s you')
    expect(shown(opts.body)).toBe(SENTENCE)
    expect(opts.fields.map((f) => [f.name, f.type])).toEqual([['password', 'password']])
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'POST /api/devices/pair/start',
      'POST /api/auth/confirm',
      'POST /api/devices/pair/start',
    ])
    expect(calls[1].body).toEqual({ password: 'correct-horse-battery-staple' })
    // A request that was answered "sign in again" is not a sign-out.
    expect(signedOutState()).toBeNull()
  })

  it('reads the plain 401 the same way, as a write that does not say it asks gets it', async () => {
    const calls = gateway([asked({ password: true }, SENTENCE, 401), json(200, { ok: true }), json(200, PAIRED)])
    promptSpy.mockResolvedValueOnce({ password: 'correct-horse-battery-staple' })

    await expect(api.devicePairStart()).resolves.toEqual(PAIRED)
    expect(calls).toHaveLength(3)
    expect(signedOutState()).toBeNull()
  })

  it('asks for the authenticator code too when one is set up', async () => {
    const calls = gateway([asked({ password: true, second_factor: true }), json(200, { ok: true }), json(200, PAIRED)])
    promptSpy.mockResolvedValueOnce({ password: 'correct-horse-battery-staple', code: '123456' })

    await api.devicePairStart()

    const opts = promptSpy.mock.calls[0][0] as { fields: { name: string }[] }
    expect(opts.fields.map((f) => f.name)).toEqual(['password', 'code'])
    expect(calls[1].body).toEqual({ password: 'correct-horse-battery-staple', totp: '123456' })
  })

  it('a wrong password asks again, saying so, and the right one carries on', async () => {
    const wrong = json(401, { error: { code: 'auth_invalid_credentials', message: 'That password isn’t right.' } })
    const calls = gateway([asked({ password: true }), wrong, json(200, { ok: true }), json(200, PAIRED)])
    promptSpy.mockResolvedValueOnce({ password: 'not-it' }).mockResolvedValueOnce({ password: 'correct-horse-battery-staple' })

    await expect(api.devicePairStart()).resolves.toEqual(PAIRED)

    const second = promptSpy.mock.calls[1][0] as { body: unknown }
    expect(shown(second.body)).toBe(`That password isn’t right.\n\n${SENTENCE}`)
    expect(calls).toHaveLength(4)
  })

  it('a lockout stops asking and says why, and the write is not sent again', async () => {
    const locked = json(429, { error: { code: 'auth_locked_out', message: 'Too many failed attempts from this address; try again later.' } })
    const calls = gateway([asked({ password: true }), locked])
    promptSpy.mockResolvedValueOnce({ password: 'not-it' })

    await expect(api.devicePairStart()).rejects.toBeInstanceOf(SignInNotRenewed)

    expect(alertSpy).toHaveBeenCalledOnce()
    expect(shown((alertSpy.mock.calls[0][0] as { body: unknown }).body)).toMatch(/Too many failed attempts/)
    expect(calls).toHaveLength(2)
  })

  it('closing the prompt does nothing: the write is not sent again', async () => {
    const calls = gateway([asked({ password: true })])
    promptSpy.mockResolvedValueOnce(null)

    const refused = api.devicePairStart()
    await expect(refused).rejects.toBeInstanceOf(SignInNotRenewed)
    await expect(refused).rejects.toThrow('Not done — you didn’t sign in again.')
    expect(calls).toHaveLength(1)
  })
})

describe('signing in again with a new link, where password sign-in is off', () => {
  it('opens the pasted link in place and sends the write again', async () => {
    const calls = gateway([asked({ password: false }, LINK_SENTENCE), json(200, { user: 'local-app' }), json(200, PAIRED)])
    promptSpy.mockResolvedValueOnce({ link: 'http://localhost:10000/?token=fresh.token' })

    await expect(api.devicePairStart()).resolves.toEqual(PAIRED)

    const opts = promptSpy.mock.calls[0][0] as { body: unknown; fields: { name: string }[] }
    // The gateway's backticked command is shown as code, as the signed-out screen shows it.
    expect(shown(opts.body)).toBe(LINK_SENTENCE.replaceAll('`', ''))
    expect(codes(opts.body)).toEqual(['personalclaw token'])
    expect(opts.fields.map((f) => f.name)).toEqual(['link'])
    expect(calls[1]).toMatchObject({ method: 'GET', url: '/api/auth/session?token=fresh.token' })
  })

  it('a paste that is no link of this gateway asks again, and this tab stays signed in', async () => {
    const refusal = json(
      403,
      { error: { code: 'session_required', message: 'This device isn’t signed in to PersonalClaw.', detail: { reason: 'not_signed_in', at: 1 } } },
      { 'X-Auth-Required': 'true' },
    )
    const calls = gateway([asked({ password: false }, LINK_SENTENCE), refusal])
    promptSpy.mockResolvedValueOnce({ link: 'abc.not-one-of-its-tokens' }).mockResolvedValueOnce(null)

    await expect(api.devicePairStart()).rejects.toBeInstanceOf(SignInNotRenewed)

    expect(calls[1]).toMatchObject({ method: 'GET', url: '/api/auth/session?token=abc.not-one-of-its-tokens' })
    const second = promptSpy.mock.calls[1][0] as { body: unknown }
    expect(shown(second.body)).toMatch(/^That isn’t a sign-in link from this PersonalClaw\./)
    expect(shown(second.body)).not.toMatch(/isn’t signed in/)
    expect(signedOutState()).toBeNull()
  })

  it('a paste with no token in it is asked for again without a request', async () => {
    const calls = gateway([asked({ password: false }, LINK_SENTENCE)])
    promptSpy.mockResolvedValueOnce({ link: 'not a link' }).mockResolvedValueOnce(null)

    await expect(api.devicePairStart()).rejects.toBeInstanceOf(SignInNotRenewed)

    expect(calls).toHaveLength(1)
    const second = promptSpy.mock.calls[1][0] as { body: unknown }
    expect(shown(second.body)).toMatch(/^That isn’t a sign-in link from this PersonalClaw\./)
  })
})
