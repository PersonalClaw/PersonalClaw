/** A server-sent-event stream that carries the sign-in's pair, in `EventSource`'s shape.
 *
 *  `EventSource` can't set a header, so on a gateway that pairs its sign-ins every stream it
 *  opened would be refused. This reads the same `text/event-stream` through `pairFetch`, which adds
 *  the pair at each attempt (`lib/pair.ts`), and keeps the surface the dashboard's nine streams use:
 *  `addEventListener`, `removeEventListener`, `onmessage`, `onopen`, `onerror`, `close()` and
 *  `readyState`.
 *
 *  What it keeps from `EventSource`, and what it adds:
 *  - `onerror` fires before every reconnect, and once more on a final stop, after which
 *    `readyState` is `CLOSED`. `close()` aborts the request in flight.
 *  - A dropped connection, a stream the gateway ended cleanly, a 5xx and a 429 reconnect, with a
 *    backoff (1 s doubling to 30 s). No gateway stream writes `id:` or `retry:`, so there is
 *    nothing to resume from.
 *  - **Any other refusal stops it.** `EventSource` closes on a non-200 too, but the page could not
 *    read why, so a panel froze while the tab looked signed in. Here a refusal that ends this
 *    browser's sign-in (403 + `X-Auth-Required`) goes through `apiError`, which shows the one
 *    signed-out screen; every other 4xx simply stops.
 *  - A signed-out tab opens nothing. */
import { apiError } from './api'
import { pairFetch, storedPair } from './pair'
import { isSignedOutRefusal, signedOutState } from './signedOut'

type Listener = (e: MessageEvent<string>) => void

const FIRST_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30_000

export class EventStream {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2

  readonly url: string
  readyState: number = EventStream.CONNECTING
  onopen: ((e: Event) => void) | null = null
  onmessage: Listener | null = null
  onerror: ((e: Event) => void) | null = null

  private listeners = new Map<string, Set<Listener>>()
  private abort: AbortController | null = null
  private timer: ReturnType<typeof setTimeout> | undefined
  private backoff = FIRST_BACKOFF_MS

  constructor(url: string) {
    this.url = url
    void this.connect()
  }

  addEventListener(type: string, listener: Listener): void {
    let set = this.listeners.get(type)
    if (!set) { set = new Set(); this.listeners.set(type, set) }
    set.add(listener)
  }

  removeEventListener(type: string, listener: Listener): void {
    this.listeners.get(type)?.delete(listener)
  }

  close(): void {
    this.readyState = EventStream.CLOSED
    if (this.timer !== undefined) { clearTimeout(this.timer); this.timer = undefined }
    this.abort?.abort()
    this.abort = null
  }

  private stop(): void {
    if (this.readyState === EventStream.CLOSED) return
    this.close()
    this.onerror?.(new Event('error'))
  }

  private retry(): void {
    if (this.readyState === EventStream.CLOSED) return
    this.readyState = EventStream.CONNECTING
    this.onerror?.(new Event('error'))
    if (this.readyState === EventStream.CLOSED) return  // the handler closed it
    const wait = this.backoff
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS)
    this.timer = setTimeout(() => { this.timer = undefined; void this.connect() }, wait)
  }

  private async connect(): Promise<void> {
    if (this.readyState === EventStream.CLOSED) return
    if (signedOutState()) { this.stop(); return }
    const abort = new AbortController()
    this.abort = abort
    let r: Response
    try {
      r = await pairFetch(this.url, { headers: { Accept: 'text/event-stream' }, cache: 'no-store', signal: abort.signal })
    } catch {
      if (!abort.signal.aborted) this.retry()
      return
    }
    if (this.readyState === EventStream.CLOSED) return
    if (!r.ok || !r.body) {
      if (r.status >= 500 || r.status === 429) { this.retry(); return }
      if (isSignedOutRefusal(r)) await apiError(r)  // the one signed-out screen
      this.stop()
      return
    }
    this.readyState = EventStream.OPEN
    this.backoff = FIRST_BACKOFF_MS
    this.onopen?.(new Event('open'))
    try {
      await this.read(r.body)
    } catch {
      if (abort.signal.aborted) return
    }
    this.retry()
  }

  private async read(body: ReadableStream<Uint8Array>): Promise<void> {
    const reader = body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let type = ''
    let data: string[] = []
    for (;;) {
      const { value, done } = await reader.read()
      if (done) return
      buffer += decoder.decode(value, { stream: true })
      let end: number
      while ((end = buffer.search(/\r\n|\r|\n/)) >= 0) {
        const line = buffer.slice(0, end)
        buffer = buffer.slice(end + (buffer.startsWith('\r\n', end) ? 2 : 1))
        if (line === '') {
          if (data.length) this.dispatch(type || 'message', data.join('\n'))
          type = ''
          data = []
          continue
        }
        if (line.startsWith(':')) continue
        const colon = line.indexOf(':')
        const field = colon < 0 ? line : line.slice(0, colon)
        const raw = colon < 0 ? '' : line.slice(colon + 1)
        const val = raw.startsWith(' ') ? raw.slice(1) : raw
        if (field === 'event') type = val
        else if (field === 'data') data.push(val)
      }
    }
  }

  private dispatch(type: string, data: string): void {
    if (this.readyState === EventStream.CLOSED) return
    const event = new MessageEvent<string>(type, { data })
    if (type === 'message') this.onmessage?.(event)
    for (const listener of [...(this.listeners.get(type) ?? [])]) listener(event)
  }
}

/** What the dashboard's streams use: the native `EventSource` or an `EventStream`, the same surface. */
export interface EventStreamLike {
  readonly url: string
  readonly readyState: number
  onopen: ((e: Event) => void) | null
  onmessage: ((e: MessageEvent<string>) => void) | null
  onerror: ((e: Event) => void) | null
  addEventListener(type: string, listener: (e: MessageEvent<string>) => void): void
  removeEventListener(type: string, listener: (e: MessageEvent<string>) => void): void
  close(): void
}

/** Open a stream to this page's own gateway. With a pair stored, an `EventStream`, which carries
 *  it. With none (a gateway that doesn't pair its sign-ins, or the desktop app, whose main process
 *  adds the header to its windows' requests itself), the browser's own `EventSource`, exactly as
 *  before. The one place in the app that may construct either. */
export function openEventStream(url: string): EventStreamLike {
  if (storedPair()) return new EventStream(url)
  return new EventSource(url) as unknown as EventStreamLike
}
