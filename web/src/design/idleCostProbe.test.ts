import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  IDLE_FRAME_BUDGET,
  IDLE_PROBE_SCRIPT,
  IDLE_ROUTES,
  IDLE_TRIES,
  IDLE_WINDOW_MS,
  idleCostFindings,
  installIdleProbe,
  measureIdle,
  onceASecond,
  voidReason,
  type IdleProbe,
  type IdleSample,
} from '../../e2e/idleCost'
import { ROUTES } from '../../e2e/routes'

// ── The idle-cost probe sees what an idle page schedules, and fails on it ────────────────────────
//
// `e2e/idleCost.spec.ts` (#3811) loads Home and Chat with the default identity and no turn running,
// and holds what the page schedules over a 5 s window to the redesign's budget: no animation frame
// at all (`web/REDESIGN.md` §8), and no timer or request that recurs faster than once a second. A
// probe that counts nothing reads exactly like a page that costs nothing, so these run the probe's
// own init script on a page of fakes under a fake clock, plant each kind of idle work, and require
// it reported by name. The browser spec runs the same probe on the real pages, with the wave
// backdrop and the same plants as its positive controls.

const noop = () => {}

class FakeSocket extends EventTarget {
  url: string
  sent: unknown[] = []
  constructor(url: string) {
    super()
    this.url = url
  }
  send(data: unknown) { this.sent.push(data) }
  /** The gateway pushing a frame. */
  push(data: string) { this.dispatchEvent(new MessageEvent('message', { data })) }
}

class FakeStream extends EventTarget {
  url: string
  constructor(url: string) {
    super()
    this.url = url
  }
  /** The gateway sending an event, named or plain. */
  emit(type = 'message') { this.dispatchEvent(new MessageEvent(type, { data: '{}' })) }
}

class FakeXHR {
  open(_method: string, _url: string) {}
  send() {}
}

/** A page of fakes the probe installs on: every API it wraps, each running on the fake clock. */
function fakePage() {
  return {
    location: { href: 'http://127.0.0.1:4173/#/dashboard' },
    performance: { now: () => Date.now() },
    setTimeout: (fn: () => void, ms?: number) => setTimeout(fn, ms),
    clearTimeout: (id: ReturnType<typeof setTimeout>) => clearTimeout(id),
    setInterval: (fn: () => void, ms?: number) => setInterval(fn, ms),
    clearInterval: (id: ReturnType<typeof setInterval>) => clearInterval(id),
    // A 60 Hz frame clock.
    requestAnimationFrame: (cb: (t: number) => void) => setTimeout(() => cb(Date.now()), 16),
    cancelAnimationFrame: (id: ReturnType<typeof setTimeout>) => clearTimeout(id),
    fetch: vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response('{}')),
    navigator: { sendBeacon: vi.fn((_url: string, _data?: BodyInit | null) => true) },
    // A class of its own per page: the probe wraps its prototype, as it does the browser's.
    XMLHttpRequest: class extends FakeXHR {},
    WebSocket: FakeSocket,
    EventSource: FakeStream,
  }
}
type FakePage = ReturnType<typeof fakePage> & { __pcIdleCost?: IdleProbe }

/** Install the probe, let the page settle, then measure one window and return what it saw. */
function probe(
  plant: (page: FakePage) => void = noop,
  opts: { before?: (page: FakePage) => void; page?: { document: Document; MutationObserver: typeof MutationObserver } } = {},
) {
  const page = { ...fakePage(), ...opts.page } as FakePage
  installIdleProbe(page as unknown as Window)
  plant(page)
  vi.advanceTimersByTime(1_000)
  opts.before?.(page)
  page.__pcIdleCost!.arm()
  vi.advanceTimersByTime(IDLE_WINDOW_MS)
  return { page, sample: page.__pcIdleCost!.disarm() }
}

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

describe('a quiet page costs nothing', () => {
  it('a page with only slow polls and a one-off timer has no finding', () => {
    const { sample } = probe((page) => {
      // An 8 s poll as a re-armed timeout (`useVisiblePoll`'s shape), and a 30 s interval.
      const poll = () => { void page.fetch('/api/loops'); page.setTimeout(poll, 8_000) }
      page.setTimeout(poll, 8_000)
      page.setInterval(() => { void page.fetch('/api/system') }, 30_000)
      // One frame and one timer while the page loaded, before the window.
      page.requestAnimationFrame(noop)
      page.setTimeout(noop, 500)
    })
    expect(idleCostFindings(sample)).toEqual([])
    expect(sample.windowMs).toBe(IDLE_WINDOW_MS)
  })

  it('a once-a-second ticker and a once-a-second poll are inside the budget', () => {
    const { sample } = probe((page) => {
      page.setInterval(noop, 1_000)
      page.setInterval(() => { void page.fetch('/api/clock') }, 1_000)
    })
    expect(idleCostFindings(sample)).toEqual([])
  })
})

describe('planted idle work fails the probe, each kind by name', () => {
  it('a planted idle interval is reported with its cadence and how often it fired', () => {
    const { sample } = probe((page) => { page.setInterval(noop, 100) })
    const findings = idleCostFindings(sample)
    expect(findings).toHaveLength(1)
    expect(findings[0]).toMatch(/^setInterval\(100 ms\) fired 50 times in 5 s idle, more than once a second: /)
    expect(findings[0]).toContain('idleCostProbe.test.ts')
  })

  it('a planted animation-frame loop is reported, named by the line that asks for the frames', () => {
    const { sample } = probe((page) => {
      const loop = () => { page.requestAnimationFrame(loop) }
      page.requestAnimationFrame(loop)
    })
    const findings = idleCostFindings(sample)
    expect(findings).toHaveLength(1)
    expect(findings[0]).toMatch(/^requestAnimationFrame asked for \d{3} frames in 5 s idle, budget 0: /)
    expect(findings[0]).toContain('idleCostProbe.test.ts')
  })

  it('a frame loop that moves an element names the element beside the frames', () => {
    // A frame loop's own line is the animation library's (motion's `h` on CI's idle Chat, #3905),
    // so the finding also names what changed while it ran: here, the one element it moves.
    const box = document.createElement('div')
    box.className = 'orb glow extra'
    box.setAttribute('aria-label', 'Planted orb')
    document.body.append(box)
    const quiet = document.createElement('span')
    document.body.append(quiet)
    try {
      const { sample } = probe((page) => {
        let x = 0
        const loop = () => { box.style.transform = `translateX(${x++}px)`; page.requestAnimationFrame(loop) }
        page.requestAnimationFrame(loop)
      }, { page: { document, MutationObserver } })
      const findings = idleCostFindings(sample)
      expect(findings[0]).toMatch(/^requestAnimationFrame asked for \d{3} frames in 5 s idle, budget 0: /)
      expect(findings[1]).toMatch(/^while it framed, these changed: html>body>div\[Planted orb\] \(\d{3} times\)$/)
      expect(Object.keys(sample.moved ?? {})).toEqual(['html>body>div[Planted orb]'])
    } finally {
      box.remove()
      quiet.remove()
    }
  })

  it('a frame loop started before the window names the stack that started it', () => {
    // CI's idle Chat asked for six frames from motion's own loop, which a load had started before
    // the window opened (#3905): its frames name only the loop's line, so the start is named too.
    const { sample } = probe((page) => {
      let left = 400
      const tail = () => { if (left-- > 0) page.requestAnimationFrame(tail) }
      const startTheTail = () => page.requestAnimationFrame(tail)
      page.setTimeout(startTheTail, 900)
    })
    expect(sample.wakes?.[0].ms).toBeLessThan(0)
    expect(sample.wakes?.[0].stack).toMatch(/startTheTail/)
    expect(idleCostFindings(sample)[0]).toMatch(/; started \d+ ms before the window by startTheTail /)
  })

  it('a single animation frame fails it: the budget is none', () => {
    const { sample } = probe(noop, { before: (page) => { page.setTimeout(() => page.requestAnimationFrame(noop), 2_000) } })
    expect(IDLE_FRAME_BUDGET).toBe(0)
    expect(idleCostFindings(sample)).toEqual([
      expect.stringMatching(/^requestAnimationFrame asked for 1 frame in 5 s idle, budget 0: /),
    ])
  })

  it('a re-armed timeout that runs faster than once a second is reported like an interval', () => {
    const { sample } = probe((page) => {
      const spin = () => { page.setTimeout(spin, 250) }
      page.setTimeout(spin, 250)
    })
    expect(idleCostFindings(sample)).toEqual([
      expect.stringMatching(/^setTimeout\(250 ms\) fired 20 times in 5 s idle, more than once a second: /),
    ])
  })

  it('a re-armed timeout whose delay varies is one source, reported with the range of its delays', () => {
    // Keyed by its delay, each re-arm would be a source of its own that fires once, and a 100 ms
    // loop with a jittered delay would never reach the budget.
    let n = 0
    const { sample } = probe((page) => {
      const spin = () => { page.setTimeout(spin, 100 + (n++ % 40)) }
      page.setTimeout(spin, 100)
    })
    const findings = idleCostFindings(sample)
    expect(findings).toHaveLength(1)
    expect(findings[0]).toMatch(/^setTimeout\(100-139 ms\) fired \d{2} times in 5 s idle, more than once a second: /)
  })

  it('a planted tight poll is reported by method and path, whatever its query', () => {
    let n = 0
    const { sample } = probe((page) => {
      page.setInterval(() => { void page.fetch(`/api/system?n=${n++}`) }, 200)
    })
    const findings = idleCostFindings(sample)
    expect(findings).toContainEqual('GET /api/system was requested 25 times in 5 s idle, more than once a second')
    expect(findings).toContainEqual(expect.stringMatching(/^setInterval\(200 ms\) fired 25 times/))
  })

  it('every way a page reaches the network counts: fetch, XHR, a beacon, a socket send, a stream opened', () => {
    const { sample } = probe((page) => {
      const sock = new page.WebSocket('ws://127.0.0.1:4173/api/ws')
      page.setInterval(() => {
        void page.fetch(new Request('http://127.0.0.1:4173/api/a', { method: 'POST' }))
        const xhr = new page.XMLHttpRequest()
        xhr.open('PUT', '/api/b')
        xhr.send()
        page.navigator.sendBeacon('/api/c')
        sock.send('ping')
        new page.EventSource('/api/d/stream')
      }, 100)
    })
    const requests = Object.keys(sample.requests).sort()
    expect(requests).toEqual(['GET /api/d/stream (event stream)', 'POST /api/a', 'POST /api/c (beacon)', 'PUT /api/b', 'SEND /api/ws (socket)'])
    for (const key of requests) expect(sample.requests[key]).toBe(50)
    expect(idleCostFindings(sample)).toHaveLength(requests.length + 1)
  })

  it('what the page did before the window opened and after it closed is not counted', () => {
    const page = fakePage() as FakePage
    installIdleProbe(page as unknown as Window)
    for (let i = 0; i < 10; i++) page.requestAnimationFrame(noop)
    void page.fetch('/api/x')
    page.__pcIdleCost!.arm()
    vi.advanceTimersByTime(IDLE_WINDOW_MS)
    const sample = page.__pcIdleCost!.disarm()
    page.requestAnimationFrame(noop)
    void page.fetch('/api/x')
    expect(sample).toEqual({ windowMs: IDLE_WINDOW_MS, frames: {}, timers: {}, requests: {}, pushes: {} })
  })

  it('the wrapped calls still do what the page asked: frames run, timers fire and clear, requests go out', () => {
    const page = fakePage() as FakePage
    const network = page.fetch
    installIdleProbe(page as unknown as Window)
    const ran: string[] = []
    page.requestAnimationFrame(() => ran.push('frame'))
    page.setTimeout(() => ran.push('timeout'), 10)
    const cleared = page.setTimeout(() => ran.push('cleared'), 10)
    page.clearTimeout(cleared)
    const iv = page.setInterval(() => ran.push('interval'), 10)
    vi.advanceTimersByTime(25)
    page.clearInterval(iv)
    vi.advanceTimersByTime(100)
    void page.fetch('/api/x')
    expect(ran).toEqual(['timeout', 'interval', 'frame', 'interval'])
    expect(network).toHaveBeenCalledWith('/api/x')
  })
})

describe('a window is idle only when nothing reached the page and no turn ran', () => {
  it('a frame pushed over the socket voids the window', () => {
    const { sample } = probe(noop, {
      before: (page) => {
        const sock = new page.WebSocket('ws://127.0.0.1:4173/api/ws')
        page.setTimeout(() => sock.push('{"type":"sessions"}'), 1_000)
      },
    })
    expect(sample.pushes).toEqual({ 'socket /api/ws': 1 })
    expect(voidReason(sample, [])).toBe('the page was sent 1 push (socket /api/ws), so it was not idle')
  })

  it('an event stream event voids it too, a named event as well as a plain one', () => {
    const { sample } = probe(noop, {
      before: (page) => {
        const es = new page.EventSource('/api/config/fs/stream')
        es.addEventListener('change', noop)
        page.setTimeout(() => { es.emit('message'); es.emit('change') }, 1_000)
      },
    })
    expect(sample.pushes).toEqual({ 'stream /api/config/fs/stream message': 1, 'stream /api/config/fs/stream change': 1 })
    expect(voidReason(sample, [])).toMatch(/^the page was sent 2 pushes/)
  })

  it('a turn running on the gateway voids it', () => {
    const { sample } = probe()
    expect(voidReason(sample, [])).toBe('')
    expect(voidReason(sample, ['chat-1', 'chat-2'])).toBe('a turn was running (chat-1, chat-2), so the page was not idle')
  })
})

describe('the probe measures until it gets an idle window, and says so when it never does', () => {
  const quiet: IdleSample = { windowMs: IDLE_WINDOW_MS, frames: {}, timers: {}, requests: {}, pushes: {} }
  const pushed: IdleSample = { ...quiet, pushes: { 'socket /api/ws': 2 } }

  /** A Playwright page reduced to what `measureIdle` uses, over a probe that hands back `samples`. */
  function pageOver(samples: IdleSample[], running: string[][] = []) {
    const win = window as unknown as { __pcIdleCost?: Partial<IdleProbe> }
    win.__pcIdleCost = { arm: vi.fn(), disarm: vi.fn(() => samples.shift()!) }
    const sessions = (keys: string[]) => keys.map((key) => ({ key, run_state: { turn: { message_id: 'm', started_at: '' } } }))
    return {
      page: {
        evaluate: async <T>(fn: () => T) => fn(),
        waitForTimeout: vi.fn(async () => {}),
        request: { get: vi.fn(async () => ({ ok: () => true, json: async () => sessions(running.shift() ?? []) })) },
      },
      probe: win.__pcIdleCost,
    }
  }

  it('takes the first idle window', async () => {
    const { page } = pageOver([{ ...quiet }])
    const got = await measureIdle(page as never)
    expect(got).toEqual({ sample: quiet, voided: [] })
    expect(page.waitForTimeout).toHaveBeenCalledWith(IDLE_WINDOW_MS)
    expect(page.request.get).toHaveBeenCalledWith('/api/chat/sessions')
  })

  it('measures again after a window something reached, and reports the windows it threw away', async () => {
    const { page } = pageOver([pushed, { ...quiet }], [[], [], ['chat-9'], [], [], []])
    const got = await measureIdle(page as never)
    expect(got.sample).toEqual(quiet)
    expect(got.voided).toEqual([
      'the page was sent 2 pushes (socket /api/ws), so it was not idle',
      'a turn was running (chat-9), so the page was not idle',
    ])
  })

  it('throws away a window in which a turn started, read when the window closes', async () => {
    const { page } = pageOver([{ ...quiet }, { ...quiet }], [[], ['chat-3'], [], []])
    const got = await measureIdle(page as never)
    expect(got.voided).toEqual(['a turn was running (chat-3), so the page was not idle'])
    expect(page.request.get).toHaveBeenCalledTimes(4)
  })

  it('throws away a window that holds none of the read the route names, which shows nothing of what an answer costs', async () => {
    // Home's cost was a frame after each answer of its 8 s poll: a 5 s window that caught no
    // answer passed whatever an answer cost.
    const read: IdleSample = { ...quiet, requests: { 'GET /api/loops': 1 } }
    const { page } = pageOver([{ ...quiet }, read])
    const got = await measureIdle(page as never, 'GET /api/loops')
    expect(got.sample).toEqual(read)
    expect(got.voided).toEqual(['the window held no GET /api/loops, so it shows nothing of what an answer costs the page'])
  })

  it(`fails, naming every reason, when none of ${IDLE_TRIES} windows was idle`, async () => {
    const { page } = pageOver(Array.from({ length: IDLE_TRIES }, () => ({ ...pushed })))
    await expect(measureIdle(page as never)).rejects.toThrow(
      new RegExp(`^no idle ${IDLE_WINDOW_MS / 1000} s window in ${IDLE_TRIES} tries: the page was sent 2 pushes`),
    )
  })
})

describe('the probe as the browser runs it', () => {
  it('installs from its own source alone, as the spec hands it to the page', () => {
    const page = fakePage() as FakePage
    // The very script the spec installs, run with no module around it and the page as `window`.
    new Function('window', IDLE_PROBE_SCRIPT)(page)
    page.setInterval(noop, 100)
    page.__pcIdleCost!.arm()
    vi.advanceTimersByTime(IDLE_WINDOW_MS)
    expect(idleCostFindings(page.__pcIdleCost!.disarm())).toHaveLength(1)
  })

  it('installs once: a second run of the script (a reload of the same document) does not count twice', () => {
    const page = fakePage() as FakePage
    installIdleProbe(page as unknown as Window)
    installIdleProbe(page as unknown as Window)
    page.setInterval(noop, 100)
    page.__pcIdleCost!.arm()
    vi.advanceTimersByTime(IDLE_WINDOW_MS)
    expect(Object.values(page.__pcIdleCost!.disarm().timers)).toEqual([50])
  })

  it('allows a source once a second over the window, with one more for a tick that ran late', () => {
    expect(onceASecond(5_000)).toBe(6)
    expect(onceASecond(5_400)).toBe(6)
    expect(onceASecond(6_000)).toBe(7)
  })

  it('probes the routes the issue names: Home and Chat, from the route manifest', () => {
    expect(IDLE_ROUTES.map((r) => r.route)).toEqual(['dashboard', 'chat'])
    // Neither polls at rest: Home re-reads on the frames that move it (#3813), and Chat polls nothing.
    expect(IDLE_ROUTES.map((r) => r.holds)).toEqual([undefined, undefined])
    for (const r of IDLE_ROUTES) expect(ROUTES).toContainEqual(expect.objectContaining({ route: r.route, label: r.label }))
  })
})
