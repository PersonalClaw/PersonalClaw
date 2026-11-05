// @module-tag tree-scan
/**
 * Every request this page makes to its own gateway carries the sign-in's pair, when one is stored.
 *
 * A gateway that pairs its sign-ins refuses the session cookie without the value it handed this
 * browser (`lib/pair.ts`). These tests drive the real `api` client and the real sockets through a
 * stubbed `fetch` and `WebSocket`, so what is asserted is the wire: the header on each request,
 * the subprotocol on each upgrade, and what each refusal leaves behind. With nothing stored, the
 * wire is exactly what it was, so a gateway that doesn't pair sees no change at all.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from './api'
import { PAIR_CHANGED, PAIR_GRACE_MS } from './pair'
import { resetSignedOutForTests, signedOutState } from './signedOut'

const A = 'A'.repeat(43)
const B = 'B'.repeat(43)

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
}

const refused = (reason: string, signedOut: boolean) =>
  json(
    403,
    { error: { code: 'session_required', message: `refused: ${reason}`, detail: { reason } } },
    signedOut ? { 'X-Auth-Required': 'true' } : {},
  )

function sentPair(call: unknown[]): string | null {
  return new Headers((call[1] as RequestInit | undefined)?.headers).get('X-PersonalClaw-Pair')
}

let fetchSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  window.localStorage.clear()
  resetSignedOutForTests()
  fetchSpy = vi.fn(async () => json(200, { sessions: [], total: 0, has_more: false }))
  vi.stubGlobal('fetch', fetchSpy)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  window.localStorage.clear()
  resetSignedOutForTests()
})

describe('the header', () => {
  it('rides a read, a write and a removal when a pair is stored', async () => {
    window.localStorage.setItem('pc_pair', A)
    await api.retagStatus()
    await api.cancelRetag()
    await api.deleteNotification('1')
    expect(fetchSpy.mock.calls.length).toBe(3)
    for (const call of fetchSpy.mock.calls) expect(sentPair(call)).toBe(A)
  })

  it('is read at send time, so a pair stored after load rides the next request', async () => {
    await api.retagStatus()
    expect(sentPair(fetchSpy.mock.calls[0])).toBeNull()
    window.localStorage.setItem('pc_pair', A)
    await api.retagStatus()
    expect(sentPair(fetchSpy.mock.calls[1])).toBe(A)
  })

  it('is absent with nothing stored, or with a malformed value, so the wire is unchanged', async () => {
    await api.retagStatus()
    window.localStorage.setItem('pc_pair', 'not a pair')
    await api.retagStatus()
    for (const call of fetchSpy.mock.calls) expect(sentPair(call)).toBeNull()
  })
})

describe('a refusal', () => {
  it('pair_wrong: sends once more with the newer pair another tab stored, and stays signed in', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    window.localStorage.setItem('pc_pair', A)
    fetchSpy
      .mockImplementationOnce(async () => {
        // Another tab signs in again while this request is out.
        queueMicrotask(() => { window.localStorage.setItem('pc_pair', B); document.dispatchEvent(new Event(PAIR_CHANGED)) })
        return refused('pair_wrong', true)
      })
      .mockImplementationOnce(async () => json(200, { sessions: [], total: 0, has_more: false }))
    const read = api.retagStatus()
    await vi.advanceTimersByTimeAsync(PAIR_GRACE_MS)
    await read
    expect(sentPair(fetchSpy.mock.calls[0])).toBe(A)
    expect(sentPair(fetchSpy.mock.calls[1])).toBe(B)
    expect(signedOutState()).toBeNull()
    expect(window.localStorage.getItem('pc_pair')).toBe(B)
  })

  it('pair_wrong twice: final, signed out, and only the value it sent is cleared', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockImplementation(async () => refused('pair_wrong', true))
    const read = api.retagStatus().catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(PAIR_GRACE_MS)
    const err = await read
    expect(err).toBeInstanceOf(ApiError)
    expect(fetchSpy).toHaveBeenCalledTimes(2)
    expect(signedOutState()?.reason).toBe('pair_wrong')
    expect(window.localStorage.getItem('pc_pair')).toBeNull()
  })

  it('pair_missing with nothing stored: one signed-out screen with the gateway sentence, nothing cleared', async () => {
    fetchSpy.mockImplementation(async () => refused('pair_missing', false))
    const err = await api.retagStatus().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(signedOutState()).toMatchObject({ code: 'session_required', reason: 'pair_missing', message: 'refused: pair_missing' })
    // A signed-out tab sends nothing more.
    const calls = fetchSpy.mock.calls.length
    await api.retagStatus().catch(() => undefined)
    expect(fetchSpy.mock.calls.length).toBe(calls)
  })

  it('pair_missing with a pair stored since it was sent: sends once more with it', async () => {
    fetchSpy
      .mockImplementationOnce(async () => { window.localStorage.setItem('pc_pair', A); return refused('pair_missing', false) })
      .mockImplementationOnce(async () => json(200, { sessions: [], total: 0, has_more: false }))
    await api.retagStatus()
    expect(sentPair(fetchSpy.mock.calls[1])).toBe(A)
    expect(signedOutState()).toBeNull()
  })

  it('an ending clears only the value the refused request carried', async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockImplementation(async () =>
      json(403, { error: { code: 'session_signed_out', message: 'Signed out.', detail: { reason: 'signed_out_elsewhere' } } }, { 'X-Auth-Required': 'true' }),
    )
    await api.retagStatus().catch(() => undefined)
    expect(signedOutState()?.code).toBe('session_signed_out')
    expect(window.localStorage.getItem('pc_pair')).toBeNull()
  })
})

describe('the sockets', () => {
  class FakeSocket {
    static opened: { url: string; protocols?: string | string[] }[] = []
    static OPEN = 1
    readyState = 0
    onopen: (() => void) | null = null
    onclose: (() => void) | null = null
    onmessage: (() => void) | null = null
    onerror: (() => void) | null = null
    constructor(url: string, protocols?: string | string[]) { FakeSocket.opened.push({ url, protocols }) }
    close() {}
    send() {}
  }

  beforeEach(() => { FakeSocket.opened = []; vi.stubGlobal('WebSocket', FakeSocket) })

  it('offer the pair as a subprotocol beside personalclaw when one is stored', async () => {
    window.localStorage.setItem('pc_pair', A)
    const { openSocket } = await import('./pair')
    openSocket('ws://localhost/api/ws')
    expect(FakeSocket.opened[0].protocols).toEqual(['personalclaw', `pc-pair.${A}`])
  })

  it('offer nothing with nothing stored, so the upgrade is unchanged', async () => {
    const { openSocket } = await import('./pair')
    openSocket('ws://localhost/api/ws')
    expect(FakeSocket.opened[0].protocols).toBeUndefined()
  })
})

describe('the rail', () => {
  it('sends no request a pair could miss: fetch, sockets and beacons only through lib/pair.ts', async () => {
    const { join, relative, sep } = await import('node:path')
    const { filesUnder, readSource } = await import('../test/sourceTree')
    const root = join(__dirname, '..')
    const RULES: { pattern: RegExp; allowed: string[]; what: string }[] = [
      // `sw.ts` fetches only build output: it reads no `/api` route (`app/swPolicy.test.ts`).
      { pattern: /(^|[^A-Za-z0-9_.$])fetch\(/, allowed: [`lib${sep}pair.ts`, 'sw.ts'], what: 'fetch(' },
      { pattern: /new WebSocket\(/, allowed: [`lib${sep}pair.ts`], what: 'new WebSocket(' },
      { pattern: /sendBeacon\(/, allowed: [], what: 'sendBeacon(' },
    ]
    const offenders: string[] = []
    const sources = filesUnder(root, (n, path) => /\.(ts|tsx)$/.test(n) && !/\.test\.(ts|tsx)$/.test(n) && !path.includes(`${sep}test${sep}`))
    for (const path of sources) {
      const rel = relative(root, path)
      const text = readSource(path).split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n')
      for (const rule of RULES) if (rule.pattern.test(text) && !rule.allowed.includes(rel)) offenders.push(`${rel}: ${rule.what}`)
    }
    expect(offenders).toEqual([])
  })
})
