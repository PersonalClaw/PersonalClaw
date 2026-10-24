import type { Page } from '@playwright/test'
import type { RouteEntry } from './routes'

// ── The idle-cost probe: what an idle page schedules, held to a budget (#3811) ───────────────────
//
// `web/REDESIGN.md` §8: with the default identity, the window focused and no turn running, the app
// schedules no animation-frame callbacks. `idleCost.spec.ts` loads each route below, lets it come to
// rest, and counts over a fixed window what the page asks for on its own:
//
//   · animation frames: every `requestAnimationFrame` call, by the line that made it. Budget: none.
//   · timers: every `setTimeout` and `setInterval` callback that runs, by the line that set it, its
//     delays named as a range (a re-armed timeout whose delay varies is still one source). Budget:
//     once a second each. A timer that fires faster is a frame loop by another name, or a poll
//     nobody slowed down.
//   · requests: every `fetch`, XHR, beacon, socket send and event stream opened, by method and path
//     (the query left out, so a poll that stamps its query is one source). Budget: once a second each.
//   · what moved: every element whose `style` or `class` changed, by a short path. No budget: it is
//     what a frame finding was spent on, named beside it, since a frame loop's own line is the
//     animation library's and says nothing of what it animates (#3905: an idle Chat on CI framed
//     from motion's loop alone).
//   · what woke the loop: the stack of each animation-frame request made outside a frame, the
//     window's and the last one before it opened, since a loop that a load started can run on into
//     the window, and its frames then name only the loop's own line. CI's idle Chat asked for six
//     such frames and moved nothing (#3905).
//
// Idle means nothing reached the page either. A push over the socket or an event stream is the
// gateway telling the page something changed, and what the page does about it is not idle cost; so
// is a turn running anywhere on the gateway, which the shared run's other specs drive at any time.
// Either one voids the window, and the probe measures again, up to `IDLE_TRIES` windows. A run that
// never gets an idle window fails, naming why each was thrown away: it never reads as clean.
//
// The routes are the issue's minimum. Every route in `routes.ts` joins once the backdrop and the
// orb are idle-clean everywhere (#3811's open question).

/** The window, from the issue: an idle page schedules no animation frame for 5 s. */
export const IDLE_WINDOW_MS = 5_000
/** Animation frames an idle page may ask for in the window. */
export const IDLE_FRAME_BUDGET = 0
/** Windows the probe tries before it gives up on getting an idle one. */
export const IDLE_TRIES = 4
/** How long the probe waits after a window it threw away, so a burst of pushes can end first. */
const SETTLE_AFTER_VOID_MS = 1_000
/** How many of the elements that moved a frame finding names, most changed first. */
const MOVED_NAMED = 5

/** A route the probe holds, and the read a window on it must hold an answer of: what a page costs
 *  at rest is mostly what each answer of its polls makes it do, so a window that caught none would
 *  pass whatever an answer costs. */
export interface IdleRoute extends RouteEntry {
  holds?: string
}

/** The routes the probe holds, from the manifest: Home and Chat. Neither polls at rest: Home reads
 *  what waits on you and what runs once, and again on the frames that say they moved (#3813), and
 *  Chat polls nothing. A route that polls names the read a window must hold an answer of. */
export const IDLE_ROUTES: readonly IdleRoute[] = [
  { route: 'dashboard', label: 'Home' },
  { route: 'chat', label: 'Chat' },
]

/** What the page did over one window, each count keyed by its source. */
export interface IdleSample {
  /** How long the window was open, read off the page's own clock. */
  windowMs: number
  frames: Record<string, number>
  timers: Record<string, number>
  requests: Record<string, number>
  /** What reached the page: socket frames and event-stream events. Any one voids the window. */
  pushes: Record<string, number>
  /** The elements whose `style` or `class` changed, by a short path: what the frames moved. */
  moved?: Record<string, number>
  /** The start of each frame loop the window's frames belong to (a request made outside any
   *  frame), most frames first: when, in ms from the window's opening (negative: before it), and
   *  the stack that made it. */
  wakes?: { ms: number; stack: string }[]
}

/** The probe the init script leaves on the page. */
export interface IdleProbe {
  arm(): void
  disarm(): IdleSample
}

declare global {
  interface Window {
    __pcIdleCost?: IdleProbe
  }
}

/** How many times one source may recur over a window: once a second, and one more for a tick that
 *  was due before the window opened and ran late inside it. */
export function onceASecond(windowMs: number): number {
  return Math.floor(windowMs / 1000) + 1
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
const seconds = (ms: number) => `${Math.round(ms / 100) / 10} s`
const total = (counts: Record<string, number>) => Object.values(counts).reduce((a, b) => a + b, 0)

/** The window's findings against the budget: one per source over it, worst first. */
export function idleCostFindings(sample: IdleSample): string[] {
  const window = seconds(sample.windowMs)
  const allowed = onceASecond(sample.windowMs)
  const worst = (counts: Record<string, number>) => Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  const out: string[] = []
  if (total(sample.frames) > IDLE_FRAME_BUDGET) {
    // The first frame finding also says what started the loop, which its own line cannot.
    const woke = sample.wakes?.[0]
    const started = woke
      ? `; started ${woke.ms < 0 ? `${-woke.ms} ms before the window` : `${woke.ms} ms into it`} by ${woke.stack.split('\n').slice(0, 4).join(' < ')}`
      : ''
    for (const [i, [at, n]] of worst(sample.frames).entries()) {
      out.push(`requestAnimationFrame asked for ${plural(n, 'frame')} in ${window} idle, budget ${IDLE_FRAME_BUDGET}: ${at}${i === 0 ? started : ''}`)
    }
    const moved = worst(sample.moved ?? {}).slice(0, MOVED_NAMED)
    if (moved.length) out.push(`while it framed, these changed: ${moved.map(([el, n]) => `${el} (${plural(n, 'time')})`).join(', ')}`)
  }
  for (const [source, n] of worst(sample.timers)) {
    if (n > allowed) out.push(source.replace(' at ', ` fired ${n} times in ${window} idle, more than once a second: `))
  }
  for (const [request, n] of worst(sample.requests)) {
    if (n > allowed) out.push(`${request} was requested ${n} times in ${window} idle, more than once a second`)
  }
  return out
}

/** Why a window was not idle, or `''` when it was: something was pushed to the page, or a turn ran. */
export function voidReason(sample: IdleSample, running: readonly string[]): string {
  if (running.length) return `a turn was running (${running.join(', ')}), so the page was not idle`
  const pushes = total(sample.pushes)
  if (pushes) return `the page was sent ${plural(pushes, 'push', 'pushes')} (${Object.keys(sample.pushes).join(', ')}), so it was not idle`
  return ''
}

/** The chats with a turn running on the gateway, read beside the page rather than by it, so the
 *  read is not one of the page's own requests. */
export async function turnsRunning(page: Pick<Page, 'request'>): Promise<string[]> {
  const res = await page.request.get('/api/chat/sessions')
  if (!res.ok()) throw new Error(`could not read whether a turn is running: GET /api/chat/sessions answered ${res.status()}`)
  const rows = (await res.json()) as { key: string; run_state?: { turn?: unknown } }[]
  return rows.filter((r) => r.run_state?.turn).map((r) => r.key)
}

/** Measure windows until one is idle, and return it with the reasons the others were thrown away.
 *  The page must carry the probe (`installIdleProbe`, as an init script) and be at rest. With
 *  `holds`, a window must hold that request too (`IdleRoute`). */
export async function measureIdle(
  page: Pick<Page, 'evaluate' | 'waitForTimeout' | 'request'>,
  holds?: string,
): Promise<{ sample: IdleSample; voided: string[] }> {
  const voided: string[] = []
  const none: IdleSample = { windowMs: 0, frames: {}, timers: {}, requests: {}, pushes: {} }
  for (let i = 0; i < IDLE_TRIES; i++) {
    const before = voidReason(none, await turnsRunning(page))
    if (before) {
      voided.push(before)
      await page.waitForTimeout(SETTLE_AFTER_VOID_MS)
      continue
    }
    await page.evaluate(() => window.__pcIdleCost!.arm())
    await page.waitForTimeout(IDLE_WINDOW_MS)
    const sample = await page.evaluate(() => window.__pcIdleCost!.disarm())
    const why = voidReason(sample, await turnsRunning(page))
      || (holds && !sample.requests[holds] ? `the window held no ${holds}, so it shows nothing of what an answer costs the page` : '')
    if (!why) return { sample, voided }
    voided.push(why)
    await page.waitForTimeout(SETTLE_AFTER_VOID_MS)
  }
  throw new Error(`no idle ${IDLE_WINDOW_MS / 1000} s window in ${IDLE_TRIES} tries: ${voided.join('; ')}`)
}

/** The init script: wraps what a page schedules work with, and counts it while armed.
 *
 *  🪤 SELF-CONTAINED. Playwright runs it from its source text (`page.addInitScript`), so it reads
 *  nothing from this module: every name it uses is its own or the page's. It installs once per
 *  document, before any of the page's scripts, so the app only ever holds the wrapped calls. Each
 *  wrapped call does exactly what it did before; the counting is all it adds. */
export function installIdleProbe(win: Window = window): void {
  const w = win as Window & typeof globalThis & { __pcIdleCost?: IdleProbe }
  if (w.__pcIdleCost) return
  let armed = false
  let openedAt = 0
  let frames: Record<string, number> = {}
  let timers: Record<string, number> = {}
  // Each timer site's delays over the window, so a re-armed timeout is one source however it varies.
  let delays: Record<string, [number, number]> = {}
  let requests: Record<string, number> = {}
  let pushes: Record<string, number> = {}
  let moved: Record<string, number> = {}
  const bump = (counts: Record<string, number>, key: string) => { counts[key] = (counts[key] ?? 0) + 1 }
  // The line that called the wrapped function: frame 0 is this one, frame 1 the wrapper.
  const caller = () => {
    const at = String(new Error().stack ?? '').split('\n').map((l) => l.trim()).filter((l) => l.startsWith('at '))
    return (at[2] ?? 'an unknown caller').slice(3).replace(/(?:https?|wss?|file):\/\/[^/\s)]*/g, '')
  }
  const pathOf = (url: unknown) => {
    try { return new URL(String(url), w.location.href).pathname } catch { return String(url) }
  }

  // What woke a frame loop: a request made outside any frame starts one (a burst), and a request
  // made inside a frame's callback carries that frame's burst on. Each burst keeps its start's stack
  // (eight lines, past this wrapper), so a window's frames name the start of the loop they belong
  // to, however long before the window it was. The last 64 bursts are kept.
  let current = 0
  let seq = 0
  let starts: Record<number, { at: number; stack: string }> = {}
  let byBurst: Record<number, number> = {}
  const stackOf = () => String(new Error().stack ?? '').split('\n').map((l) => l.trim()).filter((l) => l.startsWith('at '))
    .slice(2, 10).map((l) => l.slice(3).replace(/(?:https?|wss?|file):\/\/[^/\s)]*/g, '')).join('\n')
  const raf = w.requestAnimationFrame
  w.requestAnimationFrame = function (this: unknown, cb: FrameRequestCallback) {
    let id = current
    if (!id) {
      id = ++seq
      starts[id] = { at: w.performance.now(), stack: stackOf() }
      delete starts[id - 64]
    }
    if (armed) {
      bump(frames, caller())
      byBurst[id] = (byBurst[id] ?? 0) + 1
    }
    return raf.call(w, function (this: unknown, t: number) {
      const outer = current
      current = id
      try { return cb(t) } finally { current = outer }
    })
  } as typeof w.requestAnimationFrame

  const timer = (kind: 'setTimeout' | 'setInterval') => {
    const native = w[kind] as (handler: TimerHandler, ms?: number, ...rest: unknown[]) => number
    return function (this: unknown, handler: TimerHandler, ms?: number, ...rest: unknown[]) {
      if (typeof handler !== 'function') return native.call(w, handler, ms, ...rest)
      const source = `${kind} at ${caller()}`
      const delay = Number(ms) || 0
      return native.call(w, function (this: unknown, ...args: unknown[]) {
        if (armed) {
          bump(timers, source)
          const seen = delays[source]
          delays[source] = seen ? [Math.min(seen[0], delay), Math.max(seen[1], delay)] : [delay, delay]
        }
        return (handler as (...a: unknown[]) => unknown).apply(this, args)
      }, ms, ...rest)
    }
  }
  w.setTimeout = timer('setTimeout') as typeof w.setTimeout
  w.setInterval = timer('setInterval') as typeof w.setInterval

  const fetchNative = w.fetch
  if (fetchNative) {
    w.fetch = function (this: unknown, ...args: Parameters<typeof fetch>) {
      if (armed) {
        const [input, init] = args
        const req = typeof input === 'object' && input !== null && 'url' in input ? (input as Request) : null
        const method = String(init?.method ?? req?.method ?? 'GET').toUpperCase()
        bump(requests, `${method} ${pathOf(req ? req.url : input)}`)
      }
      return fetchNative.apply(w, args)
    } as typeof w.fetch
  }

  const xhr = w.XMLHttpRequest?.prototype
  if (xhr) {
    const open = xhr.open
    const send = xhr.send
    const target = new WeakMap<object, string>()
    xhr.open = function (this: XMLHttpRequest, method: string, url: string | URL, ...rest: unknown[]) {
      target.set(this, `${String(method).toUpperCase()} ${pathOf(url)}`)
      return (open as (...a: unknown[]) => void).call(this, method, url, ...rest)
    } as typeof xhr.open
    xhr.send = function (this: XMLHttpRequest, body?: Document | XMLHttpRequestBodyInit | null) {
      if (armed) bump(requests, target.get(this) ?? 'XHR (not opened)')
      return send.call(this, body)
    }
  }

  const nav = w.navigator as Navigator | undefined
  if (nav && typeof nav.sendBeacon === 'function') {
    const beacon = nav.sendBeacon
    nav.sendBeacon = function (url: string | URL, data?: BodyInit | null) {
      if (armed) bump(requests, `POST ${pathOf(url)} (beacon)`)
      return beacon.call(nav, url, data)
    }
  }

  const Socket = w.WebSocket
  if (Socket) {
    w.WebSocket = class extends Socket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        const path = pathOf(url)
        this.addEventListener('message', () => { if (armed) bump(pushes, `socket ${path}`) })
      }
      send(data: Parameters<WebSocket['send']>[0]) {
        if (armed) bump(requests, `SEND ${pathOf(this.url)} (socket)`)
        return super.send(data)
      }
    } as typeof w.WebSocket
  }

  // No class fields in either subclass: a transpiler may lower them into a helper this source
  // cannot carry with it. Per-stream state lives in the maps.
  const Stream = w.EventSource
  if (Stream) {
    const listen = Stream.prototype.addEventListener as (this: EventTarget, ...args: unknown[]) => void
    const streamPath = new WeakMap<object, string>()
    const counted = new WeakMap<object, Set<string>>()
    // Count every event of `type` that reaches the page, once per type however many listen.
    const count = (es: EventTarget, type: string) => {
      const seen = counted.get(es)
      if (!seen || type === 'open' || type === 'error' || seen.has(type)) return
      seen.add(type)
      listen.call(es, type, () => { if (armed) bump(pushes, `stream ${streamPath.get(es)} ${type}`) })
    }
    const Counted = class extends Stream {
      constructor(url: string | URL, init?: EventSourceInit) {
        super(url, init)
        streamPath.set(this, pathOf(url))
        counted.set(this, new Set())
        if (armed) bump(requests, `GET ${pathOf(url)} (event stream)`)
        count(this, 'message')
      }
    }
    // A named event reaches only the type a listener asked for, so each type is counted as it is.
    Counted.prototype.addEventListener = function (this: EventTarget, type: string, ...rest: unknown[]) {
      count(this, type)
      listen.call(this, type, ...rest)
    } as typeof Counted.prototype.addEventListener
    w.EventSource = Counted as typeof w.EventSource
  }

  // What moved: an observer of every `style` and `class` change, counted while armed by a short path
  // (three levels, each its tag and its label, id or first two classes). Bounded to 40 elements: an
  // idle page that moves more than that has said enough.
  const label = (el: Element) => {
    const name = el.getAttribute('aria-label') || el.id
    const cls = String(el.getAttribute('class') || '').split(/\s+/).filter(Boolean).slice(0, 2).join('.')
    return el.tagName.toLowerCase() + (name ? `[${name.slice(0, 30)}]` : cls ? `.${cls}` : '')
  }
  const pathTo = (el: Element) => {
    const bits: string[] = []
    for (let e: Element | null = el; e && e.tagName && bits.length < 3; e = e.parentElement) bits.unshift(label(e))
    return bits.join('>')
  }
  const record = (list: MutationRecord[]) => {
    if (!armed) return
    for (const m of list) {
      const key = pathTo(m.target as Element)
      if (key in moved || Object.keys(moved).length < 40) bump(moved, key)
    }
  }
  const Observer = w.MutationObserver
  const observer = Observer && w.document ? new Observer(record) : null
  observer?.observe(w.document, { subtree: true, attributes: true, attributeFilter: ['style', 'class'] })

  w.__pcIdleCost = {
    arm() {
      frames = {}
      timers = {}
      delays = {}
      requests = {}
      pushes = {}
      moved = {}
      byBurst = {}
      openedAt = w.performance.now()
      armed = true
    },
    disarm() {
      // The records not yet delivered are the window's too.
      if (observer) record(observer.takeRecords())
      armed = false
      // Each timer named with its delay, or the range of its delays: `setTimeout(100-139 ms) at …`.
      const named: Record<string, number> = {}
      for (const [source, n] of Object.entries(timers)) {
        const [lo, hi] = delays[source]
        named[source.replace(' at ', `(${lo === hi ? lo : `${lo}-${hi}`} ms) at `)] = n
      }
      // The starts of the bursts the window's frames belong to, most frames first.
      const woke = Object.entries(byBurst).sort((a, b) => b[1] - a[1])
        .map(([id]) => starts[Number(id)]).filter(Boolean)
        .map((x) => ({ ms: Math.round(x.at - openedAt), stack: x.stack }))
      // `moved` only where a page can say what moved: a page with no document has nothing to name.
      return {
        windowMs: Math.round(w.performance.now() - openedAt), frames, timers: named, requests, pushes,
        ...(observer ? { moved } : {}), ...(woke.length ? { wakes: woke } : {}),
      }
    },
  }
}

/** The probe as the page runs it: `installIdleProbe` from its own source text, on the page's
 *  window, ahead of the page's scripts (`page.addInitScript`). */
export const IDLE_PROBE_SCRIPT = `(${installIdleProbe.toString()})(window)`
