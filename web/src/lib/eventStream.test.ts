// @module-tag tree-scan
/**
 * The dashboard's server-sent-event streams carry the sign-in's pair, and keep EventSource's
 * behaviour: frames by type, `onerror` before every reconnect, a final stop that leaves the stream
 * CLOSED, and `close()` aborting the request. A refusal that isn't worth retrying stops instead of
 * freezing silently, and a refusal that ends the sign-in shows the one signed-out screen.
 *
 * The rail at the bottom keeps it that way: `new EventSource(` is allowed only in this helper's
 * own module (`openEventStream`, which picks it only when the page holds no pair), so a stream
 * opened anywhere else, which can't carry the pair, turns CI red.
 */
import { join, relative } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EventStream } from './eventStream'
import { resetSignedOutForTests, signedOutState } from './signedOut'
import { filesUnder, readSource } from '../test/sourceTree'

const A = 'A'.repeat(43)

function stream(chunks: string[], status = 200): Response {
  const body = new ReadableStream<Uint8Array>({
    start(c) { for (const chunk of chunks) c.enqueue(new TextEncoder().encode(chunk)); c.close() },
  })
  return new Response(body, { status, headers: { 'Content-Type': 'text/event-stream' } })
}

const tick = () => new Promise((r) => setTimeout(r, 0))

let fetchSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  window.localStorage.clear()
  resetSignedOutForTests()
  fetchSpy = vi.fn()
  vi.stubGlobal('fetch', fetchSpy)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  window.localStorage.clear()
  resetSignedOutForTests()
})

describe('EventStream', () => {
  it('sends the stored pair and delivers frames by type, the default type to onmessage', async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockResolvedValueOnce(stream(['event: changed\ndata: {"a":1}\n\n', 'data: plain\n', '\n']))
    const es = new EventStream('/api/config-fs/stream')
    const changed: string[] = []
    const plain: string[] = []
    es.addEventListener('changed', (e) => changed.push(e.data))
    es.onmessage = (e) => plain.push(e.data)
    es.onerror = () => es.close()
    await vi.waitFor(() => expect(plain).toEqual(['plain']))
    expect(changed).toEqual(['{"a":1}'])
    const sent = new Headers((fetchSpy.mock.calls[0][1] as RequestInit).headers)
    expect(sent.get('X-PersonalClaw-Pair')).toBe(A)
  })

  it('a clean end of stream fires onerror, then reconnects', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    fetchSpy.mockResolvedValue(stream(['data: x\n\n']))
    const es = new EventStream('/api/logs?lines=300')
    const states: number[] = []
    es.onerror = () => states.push(es.readyState)
    await vi.advanceTimersByTimeAsync(0)
    expect(states).toEqual([EventStream.CONNECTING])
    await vi.advanceTimersByTimeAsync(1000)
    expect(fetchSpy).toHaveBeenCalledTimes(2)
    es.close()
  })

  it('a 5xx reconnects; another 4xx stops for good, CLOSED', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    fetchSpy
      .mockResolvedValueOnce(new Response('busy', { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'run_not_found', message: 'gone' } }), { status: 404 }))
    const es = new EventStream('/api/loops/x/stream')
    let errors = 0
    es.onerror = () => { errors += 1 }
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchSpy).toHaveBeenCalledTimes(2)
    expect(errors).toBe(2)
    expect(es.readyState).toBe(EventStream.CLOSED)
  })

  it('a refusal that ends the sign-in shows the signed-out screen and stops', async () => {
    fetchSpy.mockResolvedValueOnce(new Response(
      JSON.stringify({ error: { code: 'session_signed_out', message: 'Signed out elsewhere.', detail: { reason: 'signed_out_elsewhere' } } }),
      { status: 403, headers: { 'Content-Type': 'application/json', 'X-Auth-Required': 'true' } },
    ))
    const es = new EventStream('/api/workflows/runs/r/events')
    await vi.waitFor(() => expect(es.readyState).toBe(EventStream.CLOSED))
    expect(signedOutState()?.code).toBe('session_signed_out')
  })

  it('close() aborts the request in flight and nothing more is sent', async () => {
    let signal: AbortSignal | undefined
    fetchSpy.mockImplementation((_u: string, init: RequestInit) => {
      signal = init.signal ?? undefined
      return new Promise((_, reject) => signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
    })
    const es = new EventStream('/api/file-watch?path=x')
    await tick()
    es.close()
    expect(signal?.aborted).toBe(true)
    await tick()
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    expect(es.readyState).toBe(EventStream.CLOSED)
  })

  it('a signed-out tab opens nothing', async () => {
    const { reportSignedOut } = await import('./signedOut')
    reportSignedOut({ message: 'x', code: 'session_expired', reason: 'expired' })
    const es = new EventStream('/api/logs?lines=1')
    await tick()
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(es.readyState).toBe(EventStream.CLOSED)
  })
})

describe('openEventStream', () => {
  it('uses the browser EventSource with no pair stored, so the stream is exactly today\'s', async () => {
    const { openEventStream } = await import('./eventStream')
    class Native { constructor(public url: string) {} close() {} }
    vi.stubGlobal('EventSource', Native)
    expect(openEventStream('/api/logs?lines=1')).toBeInstanceOf(Native)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('uses EventStream, which carries the pair, when one is stored', async () => {
    const { openEventStream } = await import('./eventStream')
    vi.stubGlobal('EventSource', class { close() {} })
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockResolvedValue(stream([]))
    const es = openEventStream('/api/logs?lines=1')
    expect(es).toBeInstanceOf(EventStream)
    es.close()
  })
})

describe('the rail', () => {
  it('opens no EventSource outside openEventStream', () => {
    const root = join(__dirname, '..')
    const helper = join(root, 'lib', 'eventStream.ts')
    const offenders = filesUnder(root, (n) => /\.(ts|tsx)$/.test(n) && !/\.test\.(ts|tsx)$/.test(n))
      .filter((path) => path !== helper && /new EventSource\(/.test(readSource(path)))
      .map((path) => relative(root, path))
    expect(offenders).toEqual([])
  })
})
