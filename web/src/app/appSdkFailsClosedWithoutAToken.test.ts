/**
 * An app page's call fails when its app token can't be minted, and its socket is not opened.
 *
 * The token is what holds an app's request to its declared permissions. A call sent without it
 * would go out on the owner's cookie alone, with the owner's whole reach.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createAppApi, createAppEvents } from './appSdk'

let fetchSpy: ReturnType<typeof vi.fn>
let sockets: string[]

class FakeSocket {
  constructor(url: string) { sockets.push(url) }
  close() {}
  addEventListener() {}
  set onopen(_f: unknown) {}
  set onmessage(_f: unknown) {}
  set onclose(_f: unknown) {}
  set onerror(_f: unknown) {}
}

beforeEach(() => {
  sockets = []
  fetchSpy = vi.fn(async () => new Response(JSON.stringify({ error: { code: 'app_disabled', message: 'This app is turned off.' } }), {
    status: 403,
    headers: { 'Content-Type': 'application/json' },
  }))
  vi.stubGlobal('fetch', fetchSpy)
  vi.stubGlobal('WebSocket', FakeSocket)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function app(name: string) {
  return { name, permissions: { api: ['/api/knowledge/items'], events: ['sessions'] } } as never
}

describe('an app whose token is refused', () => {
  it('has its call fail with the refusal, and the call itself is never sent', async () => {
    await expect(createAppApi(app('notes-a')).get('/api/knowledge/items')).rejects.toThrow(/turned off/)
    expect(fetchSpy.mock.calls.map((c) => String(c[0]))).toEqual(['/api/apps/notes-a/token'])
  })

  it('opens no socket, and asks again later rather than at once', async () => {
    vi.useFakeTimers()
    const stop = createAppEvents(app('notes-b'), () => {})
    await vi.advanceTimersByTimeAsync(100)
    expect(sockets).toEqual([])
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    stop()
  })
})
