/**
 * The sign-in's pair is kept and forgotten in one place, and each change says so once.
 *
 * Every write and every clear dispatches `personalclaw:pair-changed` on `document` exactly once (a
 * same-tab write fires no `storage` event, so a reader in this tab is told this way), and one that
 * changes nothing dispatches nothing. This browser's own sign-out, and replacing the key every
 * sign-in is signed with, forget the pair once they're done.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { PAIR_CHANGED, clearPair, keepPair, storedPair } from './pair'

const A = 'A'.repeat(43)
const B = 'B'.repeat(43)
let events: number

function count() { events += 1 }

beforeEach(() => {
  window.localStorage.clear()
  events = 0
  document.addEventListener(PAIR_CHANGED, count)
})

afterEach(() => {
  document.removeEventListener(PAIR_CHANGED, count)
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

describe('keepPair and clearPair', () => {
  it('a write says so once, and the same value again says nothing', () => {
    expect(keepPair(A)).toBe(true)
    expect(events).toBe(1)
    expect(keepPair(A)).toBe(true)
    expect(events).toBe(1)
    expect(keepPair('not a pair')).toBe(false)
    expect(events).toBe(1)
    expect(storedPair()).toBe(A)
  })

  it('a clear says so once; clearing nothing, or a value no longer stored, says nothing', () => {
    keepPair(A)
    events = 0
    clearPair(B)
    expect(events).toBe(0)
    expect(storedPair()).toBe(A)
    clearPair(A)
    expect(events).toBe(1)
    clearPair()
    expect(events).toBe(1)
  })
})

describe('this browser’s own sign-out', () => {
  function answering(status: number) {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(status === 200 ? { ok: true, revoked: true, signed_out: 3, notice: null } : { error: { code: 'origin_rejected', message: 'no' } }), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })))
  }

  it('forgets the pair once it is done', async () => {
    keepPair(A)
    answering(200)
    await api.authLogout()
    expect(storedPair()).toBe('')
  })

  it('replacing the sign-in key forgets it too: every pair was derived from that key', async () => {
    keepPair(A)
    answering(200)
    await api.rotateSigningKey()
    expect(storedPair()).toBe('')
  })

  it('a sign-out that failed forgets nothing', async () => {
    keepPair(A)
    answering(403)
    await expect(api.authLogout()).rejects.toBeTruthy()
    expect(storedPair()).toBe(A)
  })
})
