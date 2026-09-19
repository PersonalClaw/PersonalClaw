/**
 * #524 — the client wrapper the entity list never had.
 *
 * `grep -rn "memoryEntityDelete|DELETE.*memory/entities|deleteEntity" web/src src/personalclaw`
 * returned nothing before this: the store could tombstone an entity, and no layer above it could
 * ask. These drive the real request helper through a stubbed `fetch`, because the two things that
 * would silently break this wrapper — the wrong METHOD (a DELETE sent as GET hits the list route
 * and answers 200) and an unencoded id — are invisible to a caller that mocks it.
 */
import { describe, it, expect, afterEach, vi } from 'vitest'
import { api, ApiError } from './api'

const ok = () => new Response('{"ok": true}', { status: 200, headers: { 'Content-Type': 'application/json' } })

afterEach(() => { vi.unstubAllGlobals() })

describe('api.memoryEntityDelete', () => {
  it('sends DELETE to the entity path', async () => {
    const fetchSpy = vi.fn(async () => ok())
    vi.stubGlobal('fetch', fetchSpy)
    await api.memoryEntityDelete('ent_a1b2c3d4')
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/memory/entities/ent_a1b2c3d4')
    expect(init.method).toBe('DELETE')
  })

  it('encodes the id rather than pasting it into the path', async () => {
    // Ids are server-minted (`ent_<hex>`), so this is defence at the seam rather than a live bug:
    // an unencoded `/` would address a different route entirely, and this wrapper is the only place
    // that decides.
    const fetchSpy = vi.fn(async () => ok())
    vi.stubGlobal('fetch', fetchSpy)
    await api.memoryEntityDelete('ent a/b')
    const [url] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/memory/entities/ent%20a%2Fb')
  })

  it('rejects with the gateway’s reason so the panel can report it', async () => {
    // The 404 an already-deleted entity now returns has to reach the caller: `removeSelected`
    // reports `e.message`, and a resolved promise would let a no-op read as a deletion.
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response('{"error": "no such entity"}', { status: 404, headers: { 'Content-Type': 'application/json' } })
    ))
    const err = await api.memoryEntityDelete('ent_gone').then(() => null, (e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(404)
    expect((err as ApiError).message).toBe('no such entity')
  })
})
