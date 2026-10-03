import { afterEach, describe, expect, it, vi } from 'vitest'
import { chunkedUpload, isAbortError, type UploadProgress } from './chunkedUpload'

// ── A cancelled upload stops, sends no complete, and is dropped on the gateway ───────────────
//
// Measured on Knowledge › Add knowledge › Video, a 60 MB file over a throttled connection: Cancel
// at 14% closed the form, and the parts went on, then `complete`, and the file became an item that
// was processed and shown to a model. The parts of an upload this page gives up sat on the gateway
// for a day, and nothing told it the upload was over.
//
// Here: a cancel aborts the part in flight, never asks for `complete`, and drops the upload
// (`DELETE /api/uploads/{id}`); so does a failure, and so does the page closing mid-upload. Once
// every part has landed the upload says it is finishing: the gateway completes it whatever the page
// does next, so that request carries no cancel, and a caller stops offering one.

interface Call { method: string; url: string; signal: AbortSignal | null; keepalive: boolean }

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

/** A gateway for one three-part upload. `hold` keeps that part's PUT in flight until its request is
 *  aborted; `failPart` refuses one; `holdComplete` keeps the complete open until `release()`. */
function gateway(opts: { hold?: number; failPart?: number; holdComplete?: boolean } = {}) {
  const calls: Call[] = []
  let release = () => {}
  vi.stubGlobal('fetch', vi.fn((url: string, init: RequestInit = {}) => {
    const method = (init.method || 'GET').toUpperCase()
    calls.push({ method, url, signal: init.signal ?? null, keepalive: !!init.keepalive })
    if (url === '/api/uploads/init') return Promise.resolve(json({ uploadId: 'u1', partSize: 4, totalParts: 3, category: 'video' }))
    const part = /^\/api\/uploads\/u1\/part\?index=(\d+)$/.exec(url)
    if (part && method === 'PUT') {
      const index = Number(part[1])
      if (index === opts.failPart) return Promise.resolve(json({ error: 'part refused' }, 400))
      if (index === opts.hold) {
        return new Promise<Response>((_, reject) => {
          init.signal?.addEventListener('abort', () => reject(new DOMException('The operation was aborted.', 'AbortError')))
        })
      }
      return Promise.resolve(json({ received: [index] }))
    }
    if (url === '/api/uploads/u1/complete') {
      if (!opts.holdComplete) return Promise.resolve(json({ item_id: 'k1' }))
      return new Promise<Response>((resolve) => { release = () => resolve(json({ item_id: 'k1' })) })
    }
    if (url === '/api/uploads/u1' && method === 'DELETE') return Promise.resolve(json({ uploadId: 'u1', dropped: true }))
    return Promise.resolve(json({ error: `unexpected ${method} ${url}` }, 500))
  }))
  return {
    calls,
    release: () => release(),
    puts: () => calls.filter((c) => c.method === 'PUT'),
    completes: () => calls.filter((c) => c.url.endsWith('/complete')),
    drops: () => calls.filter((c) => c.method === 'DELETE'),
  }
}

const video = () => new File(['0123456789ab'], 'walkthrough.mov', { type: 'video/quicktime' })

afterEach(() => { vi.unstubAllGlobals() })

describe('a cancelled upload', () => {
  it('aborts the part in flight, never asks for complete, and drops the upload', async () => {
    const g = gateway({ hold: 1 })
    const ctrl = new AbortController()
    const upload = chunkedUpload(video(), { target: 'knowledge', signal: ctrl.signal })
    await vi.waitFor(() => expect(g.puts()).toHaveLength(2))

    ctrl.abort()

    await expect(upload).rejects.toSatisfy(isAbortError)
    expect(g.puts()[1].signal?.aborted).toBe(true)
    expect(g.puts().map((c) => c.url)).not.toContain('/api/uploads/u1/part?index=2')
    expect(g.completes()).toEqual([])
    // keepalive: a cancel that closes the page as it goes still reaches the gateway.
    expect(g.drops()).toEqual([expect.objectContaining({ url: '/api/uploads/u1', keepalive: true })])
  })

  it('a cancel before any part has gone still drops the upload the gateway opened', async () => {
    const g = gateway()
    const ctrl = new AbortController()
    const upload = chunkedUpload(video(), {
      target: 'knowledge', signal: ctrl.signal,
    })
    ctrl.abort()

    await expect(upload).rejects.toSatisfy(isAbortError)
    expect(g.puts()).toEqual([])
    expect(g.completes()).toEqual([])
    expect(g.drops()).toHaveLength(1)
  })
})

describe('an upload that fails, or that the page leaves', () => {
  it('a part the gateway refuses drops the upload, and the refusal is what the caller hears', async () => {
    vi.useFakeTimers()
    try {
      const g = gateway({ failPart: 1 })
      const refused = expect(chunkedUpload(video(), { target: 'knowledge' })).rejects.toThrow('part refused')
      await vi.runAllTimersAsync()  // the part's retries wait between attempts

      await refused
      expect(g.completes()).toEqual([])
      expect(g.drops()).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('closing the page mid-upload drops it', async () => {
    const g = gateway({ hold: 1 })
    const ctrl = new AbortController()
    const upload = chunkedUpload(video(), { target: 'knowledge', signal: ctrl.signal })
    await vi.waitFor(() => expect(g.puts()).toHaveLength(2))

    window.dispatchEvent(new Event('pagehide'))

    expect(g.drops()).toEqual([expect.objectContaining({ url: '/api/uploads/u1', keepalive: true })])
    ctrl.abort()
    await expect(upload).rejects.toSatisfy(isAbortError)
  })

  it('a finished upload leaves nothing listening: closing the page afterwards drops nothing', async () => {
    const g = gateway()
    await expect(chunkedUpload(video(), { target: 'knowledge' })).resolves.toEqual({ item_id: 'k1' })

    window.dispatchEvent(new Event('pagehide'))

    expect(g.drops()).toEqual([])
  })
})

describe('once every part has landed', () => {
  it('it says it is finishing, then completes whatever the caller does', async () => {
    const g = gateway({ holdComplete: true })
    const ctrl = new AbortController()
    const seen: UploadProgress[] = []
    const upload = chunkedUpload(video(), { target: 'knowledge', signal: ctrl.signal, onProgress: (p) => seen.push(p) })
    await vi.waitFor(() => expect(g.completes()).toHaveLength(1))

    expect(seen.filter((p) => p.finishing)).toEqual([{ loaded: 12, total: 12, pct: 100, finishing: true }])
    expect(seen.slice(0, -1).every((p) => !p.finishing)).toBe(true)
    // The gateway completes it now, so the request carries no cancel a caller could pull.
    expect(g.completes()[0].signal).toBeNull()

    ctrl.abort()
    g.release()

    await expect(upload).resolves.toEqual({ item_id: 'k1' })
    expect(g.drops()).toEqual([])
  })
})
