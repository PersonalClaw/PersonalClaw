/** The value a browser's sign-in keeps beside its cookie, and the one place it is read, kept and sent.
 *
 *  A gateway that pairs its sign-ins hands each browser session a value of its own in the answer
 *  that signed it in, and refuses the cookie without it. That value lives here, in this origin's
 *  own storage, and it rides every request this page makes to its own origin:
 *
 *  - `pairFetch` — every `fetch` the app makes (`api.ts`, the app SDK, uploads, the stream reader):
 *    the `X-PersonalClaw-Pair` header, read at send time, never cached in a constant;
 *  - `openSocket` — the three sockets, which can't set a header on their upgrade, offer it as the
 *    `pc-pair.<pair>` subprotocol beside `personalclaw`.
 *
 *  A gateway that doesn't pair its sign-ins never hands a value over, so nothing is stored and
 *  nothing is sent: every request and every upgrade is exactly what it was. In the desktop app the
 *  page never holds the value either; the app's main process adds the header to its windows'
 *  traffic.
 *
 *  The refusal rules (the gateway's `session_required` with `detail.reason`):
 *  - `pair_wrong` (the cookie's own session, with another value): another tab may just have signed
 *    in again and stored a new one, so wait for that change or a short grace and send once more.
 *    A second refusal is final: the value is cleared only if storage still holds the one that was
 *    sent, so a newer one another tab stored is never wiped.
 *  - `pair_missing` (a live session, no value): when one is stored now, send once more with it.
 *    When none is, this tab can't do anything until it signs in again: it is latched signed out
 *    with the gateway's sentence, and nothing is cleared.
 *  - A refusal of a request that carried `?token=` (a sign-in link) never clears or latches: that
 *    request was about the link, not about this tab's sign-in. */
import { reportSignedOut } from './signedOut'

export const PAIR_HEADER = 'X-PersonalClaw-Pair'
/** The subprotocol a socket names itself by, which the gateway selects. */
export const SOCKET_PROTOCOL = 'personalclaw'
export const PAIR_PROTOCOL_PREFIX = 'pc-pair.'
/** Dispatched on `document`, with no detail, after this tab writes or clears the value: a write in
 *  the same tab fires no `storage` event, and a reader (the browse connector's content script)
 *  then reads storage itself. */
export const PAIR_CHANGED = 'personalclaw:pair-changed'

const KEY = 'pc_pair'
const SHAPE = /^[A-Za-z0-9_-]{43}$/
/** How long a refused `pair_wrong` waits for another tab's newer value before sending again. */
export const PAIR_GRACE_MS = 600

/** True when *value* has a pair's shape: 43 characters of base64url, nothing else. */
export function isPair(value: unknown): value is string {
  return typeof value === 'string' && SHAPE.test(value)
}

/** The stored value, or `''` when there is none, it is malformed, or storage can't be read. */
export function storedPair(): string {
  try {
    const value = window.localStorage.getItem(KEY)
    return isPair(value) ? value : ''
  } catch {
    return ''
  }
}

function changed(): void {
  try { document.dispatchEvent(new Event(PAIR_CHANGED)) } catch { /* no document: nothing listens */ }
}

/** Keep *value* when it has a pair's shape. True when it was stored. */
export function keepPair(value: unknown): boolean {
  if (!isPair(value)) return false
  try {
    if (window.localStorage.getItem(KEY) === value) return true
    window.localStorage.setItem(KEY, value)
  } catch {
    return false
  }
  changed()
  return true
}

/** Keep the value an answer carries in its `X-PersonalClaw-Pair` header, if it carries one. */
export function keepPairFrom(r: Response): void {
  const value = r.headers?.get?.(PAIR_HEADER)
  if (value) keepPair(value)
}

/** Forget the stored value. With *sent*, only while storage still holds exactly that value
 *  (compare-and-clear), so a newer one another tab stored survives. */
export function clearPair(sent?: string): void {
  try {
    const now = window.localStorage.getItem(KEY)
    if (now === null) return
    if (sent !== undefined && now !== sent) return
    window.localStorage.removeItem(KEY)
  } catch {
    return
  }
  changed()
}

/** The header to add to a request to this page's own origin: `{}` when nothing is stored. */
export function pairHeader(): Record<string, string> {
  const value = storedPair()
  return value ? { [PAIR_HEADER]: value } : {}
}

/** The subprotocols a socket offers: `undefined` when nothing is stored, so the upgrade is
 *  exactly today's. */
export function socketProtocols(): string[] | undefined {
  const value = storedPair()
  return value ? [SOCKET_PROTOCOL, `${PAIR_PROTOCOL_PREFIX}${value}`] : undefined
}

/** Open a socket to this page's own origin, offering the stored value. */
export function openSocket(url: string): WebSocket {
  const protocols = socketProtocols()
  return protocols ? new WebSocket(url, protocols) : new WebSocket(url)
}

function urlOf(input: RequestInfo | URL): URL | null {
  try {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    return new URL(raw, window.location.href)
  } catch {
    return null
  }
}

/** *init* with the pair header set to *value* (or absent for `''`), keeping the headers' own
 *  shape: a plain object stays a plain object, so a caller (or a test's stub) reading
 *  `init.headers[name]` sees every header it set. */
function withPair(init: RequestInit, value: string): RequestInit {
  const given = init.headers
  if (given === undefined || (typeof given === 'object' && !(given instanceof Headers) && !Array.isArray(given))) {
    const plain: Record<string, string> = {}
    for (const [k, v] of Object.entries((given ?? {}) as Record<string, string>)) {
      if (k.toLowerCase() !== PAIR_HEADER.toLowerCase()) plain[k] = v
    }
    if (value) plain[PAIR_HEADER] = value
    return { ...init, headers: plain }
  }
  const headers = new Headers(given)
  headers.delete(PAIR_HEADER)
  if (value) headers.set(PAIR_HEADER, value)
  return { ...init, headers }
}

interface Refusal { reason: 'pair_missing' | 'pair_wrong'; message: string; code: string }

/** The gateway's pair refusal, when *r* is one. Reads a clone, so the caller still has the body. */
async function pairRefusal(r: Response): Promise<Refusal | null> {
  if (r.status !== 403) return null
  try {
    const body = (await r.clone().json()) as { error?: { code?: unknown; message?: unknown; detail?: { reason?: unknown } } }
    const err = body?.error
    const reason = err?.detail?.reason
    if (err?.code !== 'session_required' || (reason !== 'pair_missing' && reason !== 'pair_wrong')) return null
    return { reason, message: typeof err.message === 'string' ? err.message : '', code: 'session_required' }
  } catch {
    return null
  }
}

/** Resolve with the stored value once it differs from *sent*, or after the grace with whatever
 *  is stored then. */
function nextPair(sent: string): Promise<string> {
  return new Promise((resolve) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      window.removeEventListener('storage', onStorage)
      document.removeEventListener(PAIR_CHANGED, onChange)
      window.clearTimeout(timer)
      resolve(storedPair())
    }
    const onStorage = (e: StorageEvent) => { if (e.key === KEY && storedPair() !== sent) finish() }
    const onChange = () => { if (storedPair() !== sent) finish() }
    window.addEventListener('storage', onStorage)
    document.addEventListener(PAIR_CHANGED, onChange)
    const timer = window.setTimeout(finish, PAIR_GRACE_MS)
  })
}

/** What each answer was sent with, so a later reader (an ending's compare-and-clear) knows. */
const sentWith = new WeakMap<Response, string>()

/** The value *r* was sent with, `''` when none or when it didn't go through `pairFetch`. */
export function pairSentWith(r: Response): string {
  return sentWith.get(r) ?? ''
}

/** `fetch`, with the stored value on every request to this page's own origin, and the refusal
 *  rules above. A request to any other origin goes out exactly as asked. */
export async function pairFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const url = urlOf(input)
  if (!url || url.origin !== window.location.origin) return fetch(input, init)
  // A `Request` body is consumed by the first send, so only a URL is sent twice.
  const resendable = !(typeof Request !== 'undefined' && input instanceof Request)
  const send = async (value: string) => {
    const r = await fetch(input, withPair(init, value))
    sentWith.set(r, value)
    return r
  }
  const sent = storedPair()
  const first = await send(sent)
  const refusal = await pairRefusal(first)
  if (!refusal || url.searchParams.has('token')) return first
  if (refusal.reason === 'pair_wrong') {
    if (!resendable) return first
    const again = await send(await nextPair(sent))
    const second = await pairRefusal(again)
    if (second?.reason === 'pair_wrong') clearPair(pairSentWith(again))
    return again
  }
  // `pair_missing`: a live session the gateway saw with no value.
  const now = storedPair()
  if (now && now !== sent && resendable) return send(now)
  if (!now) reportSignedOut({ message: refusal.message, code: refusal.code, reason: refusal.reason })
  return first
}
