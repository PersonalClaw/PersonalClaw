/** URLs the browser loads by itself, signed so they open on a gateway that pairs its sign-ins.
 *
 *  An `<img>`, `<audio>`, `<video>` or `<object>` source, a download link and a link that opens in
 *  a new tab are fetched by the browser, which can't add the pair header (`lib/pair.ts`). A gateway
 *  that pairs its sign-ins signs such a URL for the session asking: `POST /api/auth/signed-urls`,
 *  sent with the pair, answers each URL with a short-lived `pc_exp` and `pc_sig`. The signature
 *  only opens that URL, for that session, until it expires, and only beside that session's cookie.
 *
 *  When there's nothing to sign with, the URL is used as it is, with no request:
 *  - a page that holds no pair, outside the desktop app, is on a gateway that doesn't pair its
 *    sign-ins (one that does always hands this browser a pair), and the cookie opens the URL;
 *  - a gateway without the signing route answers 404 or 405, after which this tab stops asking.
 *
 *  In the desktop app the page holds no pair (the app's main process adds the header to its
 *  windows' requests), so the signer asks whether or not one is stored.
 *
 *  How it asks:
 *  - every URL wanted in one render goes in one request, and a URL already on its way waits for
 *    that request instead of sending its own, so a page full of images asks once, and a gateway
 *    without the route is asked once;
 *  - a signature is signed again at 80% of its life, by every element showing it in one request.
 *    A new one that doesn't end later than the one it was asked to replace means the session ends
 *    there: it is kept until that end, and asked for again past it (a session that really ended is
 *    refused, and that backs off like any failure);
 *  - a failed request (the network, an answer other than the route's) leaves the URL as it was and
 *    is asked again later, waiting longer after each failure of that URL, a re-sign included;
 *  - a URL the gateway declines to sign is used as it is, and not asked about again;
 *  - the signatures are this sign-in's: a change of the pair (another sign-in, a sign-out) forgets
 *    them all, and what was declined. */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { PAIR_CHANGED, pairFetch, storedPair } from './pair'

export const SIGN_PATH = '/api/auth/signed-urls'
/** Re-sign once this share of a signature's life has gone, so a playing video never meets its end. */
const RESIGN_AT = 0.8
/** The wait after a failed request, doubled on each failure up to the cap. */
const RETRY_FIRST_MS = 1000
const RETRY_CAP_MS = 60_000

interface Signed {
  url: string
  exp: number
  signedAt: number
  /** A re-sign that didn't move the end later: the session ends at `exp`, so it is kept as it is
   *  until then, and asked for again past it. */
  final: boolean
}

const cache = new Map<string, Signed>()
let mintAbsent = false
/** Each URL's failed requests in a row, for its wait before the next. */
const failures = new Map<string, number>()
/** URLs the gateway answered without a signature: used as they are until the pair changes. */
const declined = new Set<string>()
interface Batch {
  /** Each URL gathered, and the entry its answer replaces, as it was when it was asked. */
  urls: Map<string, Signed | undefined>
  done: Promise<void>
}
/** The URLs gathered for the request this render sends, and the promise it settles. */
let gathering: Batch | null = null
/** Each URL on its way, and the request that carries it. */
const inFlight = new Map<string, Promise<void>>()

/** Bumped whenever what this page signs with changes (the pair, the route found absent), so a
 *  hook reading it renders again and asks again. */
let epoch = 0
const listeners = new Set<() => void>()
function bump(): void {
  epoch += 1
  for (const listener of listeners) listener()
}
function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function forget(): void {
  cache.clear()
  failures.clear()
  declined.clear()
  bump()
}

if (typeof document !== 'undefined') {
  document.addEventListener(PAIR_CHANGED, forget)
}
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => { if (event.key === null || event.key === 'pc_pair') forget() })
}

/** True when *url* is this gateway's own route, the only kind the signer ever sends: a
 *  `data:`, `blob:` or another site's URL passes through untouched. */
export function signable(url: unknown): url is string {
  if (typeof url !== 'string') return false
  if (!url.startsWith('/') || url.startsWith('//')) {
    try {
      const u = new URL(url)
      return u.origin === window.location.origin && u.pathname.startsWith('/api/')
    } catch {
      return false
    }
  }
  return url.startsWith('/api/')
}

/** True when this page should ask the gateway to sign its URLs. */
export function signs(): boolean {
  if (mintAbsent) return false
  if (storedPair()) return true
  return typeof window !== 'undefined' && 'pclawDesktop' in window
}

function fresh(entry: Signed | undefined, now: number): entry is Signed {
  return !!entry && now < staleAt(entry)
}

/** When *entry* is asked for again: at 80% of its life, or at its end when it's the session's last. */
function staleAt(entry: Signed): number {
  return entry.final ? entry.exp : entry.signedAt + (entry.exp - entry.signedAt) * RESIGN_AT
}

/** How long to wait before asking again for *url* after its last request failed. */
function retryDelay(url: string): number {
  return Math.min(RETRY_CAP_MS, RETRY_FIRST_MS * 2 ** Math.max(0, (failures.get(url) ?? 1) - 1))
}

function failed(urls: string[]): void {
  for (const url of urls) failures.set(url, (failures.get(url) ?? 0) + 1)
}

async function send(batch: Batch): Promise<void> {
  const urls = [...batch.urls.keys()]
  if (!signs()) return
  let r: Response | null = null
  try {
    r = await pairFetch(SIGN_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Session-Key': 'dashboard:ui' },
      body: JSON.stringify({ urls }),
    })
  } catch {
    r = null
  }
  if (r && (r.status === 404 || r.status === 405)) {
    mintAbsent = true
    bump()
    return
  }
  if (!r?.ok) {
    failed(urls)
    return
  }
  const now = Date.now() / 1000
  const answered = new Set<string>()
  try {
    const rows = ((await r.json()) as { urls?: { url?: unknown; signed?: unknown; exp?: unknown }[] }).urls ?? []
    for (const row of rows) {
      if (typeof row.url !== 'string' || !batch.urls.has(row.url)) continue
      answered.add(row.url)
      failures.delete(row.url)
      if (typeof row.signed !== 'string' || typeof row.exp !== 'number') {
        declined.add(row.url)
        continue
      }
      const replaced = batch.urls.get(row.url)
      const current = cache.get(row.url)
      if (!replaced || row.exp > replaced.exp) {
        if (!current || row.exp >= current.exp) cache.set(row.url, { url: row.signed, exp: row.exp, signedAt: now, final: false })
      } else {
        cache.set(row.url, { ...(current ?? replaced), final: true })
      }
    }
  } catch {
    // An answer that isn't the signing route's shape (not JSON, no list, a row that isn't one)
    // signs nothing more: the URLs it didn't answer stay as they were, and count as failed.
  }
  failed(urls.filter((u) => !answered.has(u)))
}

/** The request that will carry *url*: the one already on its way, or this render's. */
function ask(url: string, force: boolean): Promise<void> {
  const carrying = inFlight.get(url)
  if (carrying && !force) return carrying
  if (!gathering) {
    const batch: Batch = { urls: new Map(), done: Promise.resolve() }
    batch.done = Promise.resolve().then(async () => {
      if (gathering === batch) gathering = null
      try {
        await send(batch)
      } finally {
        for (const u of batch.urls.keys()) if (inFlight.get(u) === batch.done) inFlight.delete(u)
      }
    })
    gathering = batch
  }
  if (!gathering.urls.has(url)) gathering.urls.set(url, cache.get(url))
  inFlight.set(url, gathering.done)
  return gathering.done
}

/** Each of *urls*, signed where the gateway signs it, as it is otherwise (in the same order). A
 *  URL the gateway refuses to sign comes back as it is; one unsignable URL never drops the rest. */
export async function signUrls(urls: string[], opts: { force?: boolean } = {}): Promise<string[]> {
  if (!signs()) return urls
  const now = Date.now() / 1000
  const wanted = [...new Set(urls.filter((u) => signable(u) && !declined.has(u) && (opts.force || !fresh(cache.get(u), now))))]
  await Promise.all(wanted.map((u) => ask(u, !!opts.force)))
  return urls.map((u) => cache.get(u)?.url ?? u)
}

export async function signUrl(url: string, opts: { force?: boolean } = {}): Promise<string> {
  return (await signUrls([url], opts))[0]
}

/** When to ask again for *url* after asking: when its signature stops being fresh (at 80% of its
 *  life, or at its end when it's the session's last), even after a failed re-sign; with the growing
 *  wait after a failed request when there's no fresh signature; never when the gateway declined it. */
function nextAsk(url: string): number | null {
  if (!signs() || declined.has(url)) return null
  const entry = cache.get(url)
  const now = Date.now() / 1000
  if (fresh(entry, now)) return Math.max(1000, (staleAt(entry) - now) * 1000)
  if (failures.has(url)) return retryDelay(url)
  return null
}

export interface SignedSource {
  /** The URL to load: *url* as it is without signing, else its signature once the gateway
   *  answers (`undefined` until then, so the browser never loads a URL a pairing gateway refuses). */
  src: string | undefined
  /** Whether {@link retry} could try anything: something signs here, the URL is the gateway's, the
   *  gateway hasn't declined to sign it, and its one retry isn't used up. Read it in the `error`
   *  event to decide synchronously. */
  canRetry: () => boolean
  /** For the element's `error` event: signs the URL again, once per URL. Resolves true when that
   *  gave the element a new source to try; false when there's nothing new to try (no signing
   *  here, or the same URL back), so the caller decides what the failure means. */
  retry: () => Promise<boolean>
}

/** *url*, signed while it is shown, signed again before the signature ends, and once more when the
 *  element that loads it fails. */
export function useSignedSource(url: string | undefined): SignedSource {
  const current = useSyncExternalStore(subscribe, () => epoch, () => epoch)
  const [signed, setSigned] = useState<{ from: string; to: string } | null>(null)
  const retried = useRef('')
  const willSign = signable(url) && signs()
  useEffect(() => {
    if (!url || !willSign) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const run = async (force: boolean) => {
      const to = await signUrl(url, { force })
      if (!alive) return
      setSigned((prev) => (prev && prev.from === url && prev.to === to ? prev : { from: url, to }))
      const wait = nextAsk(url)
      // Unforced: every element showing this URL shares the one request its re-sign sends.
      if (wait !== null) timer = setTimeout(() => { void run(false) }, wait)
    }
    void run(false)
    return () => { alive = false; if (timer !== undefined) clearTimeout(timer) }
  }, [url, willSign, current])
  const src = !url ? undefined : !willSign ? url : signed && signed.from === url ? signed.to : undefined
  const canRetry = () => !!url && signable(url) && signs() && !declined.has(url) && retried.current !== url
  const retry = async (): Promise<boolean> => {
    if (!url || !canRetry()) return false
    retried.current = url
    const to = await signUrl(url, { force: true })
    if (to === src) return false
    setSigned({ from: url, to })
    return true
  }
  return { src, canRetry, retry }
}

/** *url*, signed while it is shown, and signed again before the signature ends. */
export function useSignedUrl(url: string | undefined): string | undefined {
  return useSignedSource(url).src
}

interface MediaState { time: number; paused: boolean; rate: number }

function mediaOf(el: Element): HTMLMediaElement | null {
  return typeof HTMLMediaElement !== 'undefined' && el instanceof HTMLMediaElement ? el : null
}

/** Put *to* in the element, carrying a media element's position, paused state and speed over. */
function swapSource(el: HTMLImageElement | HTMLMediaElement, to: string): void {
  if (el.getAttribute('src') === to) return
  const media = mediaOf(el)
  const before: MediaState | null = media && media.currentTime > 0
    ? { time: media.currentTime, paused: media.paused, rate: media.playbackRate }
    : null
  el.setAttribute('src', to)
  if (media && before) {
    media.addEventListener('loadedmetadata', () => {
      media.currentTime = before.time
      media.playbackRate = before.rate
      if (!before.paused) media.play().catch(() => { /* the autoplay rule: it stays paused there */ })
    }, { once: true })
  }
}

/** Keep *el*'s source on *url*, signed as `useSignedSource` keeps it, for code that holds an
 *  element rather than a React tree (an app's page): signed at once, signed again before the
 *  signature ends and once when the element fails, each time carrying a playing element's place,
 *  paused state and speed over. Returns the function that stops it. *sign* is the signer to use. */
export function bindSignedSource(
  el: HTMLImageElement | HTMLMediaElement,
  url: string,
  sign: (url: string, opts?: { force?: boolean }) => Promise<string> = signUrl,
): () => void {
  let alive = true
  let retried = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const run = async (force: boolean) => {
    const to = await sign(url, { force })
    if (!alive) return
    swapSource(el, to)
    const wait = nextAsk(url)
    if (wait !== null) timer = setTimeout(() => { void run(false) }, wait)
  }
  const onError = () => {
    if (retried || !signable(url) || !signs() || declined.has(url)) return
    retried = true
    if (timer !== undefined) clearTimeout(timer)
    void run(true)
  }
  el.addEventListener('error', onError)
  void run(false)
  return () => {
    alive = false
    if (timer !== undefined) clearTimeout(timer)
    el.removeEventListener('error', onError)
  }
}

/** When a playing element's source is swapped for a fresh signature, carry on where it was:
 *  the position, paused or playing, and the speed. */
export function useKeepsPlaying(ref: { current: HTMLMediaElement | null }, src: string | undefined): void {
  const last = useRef<{ src: string; time: number; paused: boolean; rate: number } | null>(null)
  useEffect(() => {
    const el = ref.current
    if (!el || !src) return
    const before = last.current
    if (before && before.src !== src && before.time > 0) {
      const restore = async () => {
        el.currentTime = before.time
        el.playbackRate = before.rate
        if (before.paused) return
        try {
          await el.play()
        } catch {
          // The browser's autoplay rule refused it: it stays paused, at the same place.
        }
      }
      el.addEventListener('loadedmetadata', () => { void restore() }, { once: true })
    }
    const note = () => { last.current = { src, time: el.currentTime, paused: el.paused, rate: el.playbackRate } }
    note()
    el.addEventListener('timeupdate', note)
    el.addEventListener('pause', note)
    el.addEventListener('play', note)
    el.addEventListener('ratechange', note)
    return () => {
      el.removeEventListener('timeupdate', note)
      el.removeEventListener('pause', note)
      el.removeEventListener('play', note)
      el.removeEventListener('ratechange', note)
    }
  }, [ref, src])
}

/** Tests only: forget every signature, every request on its way, and that the route was absent. */
export function resetSignedUrlsForTests(): void {
  cache.clear()
  inFlight.clear()
  failures.clear()
  declined.clear()
  gathering = null
  mintAbsent = false
  bump()
}
