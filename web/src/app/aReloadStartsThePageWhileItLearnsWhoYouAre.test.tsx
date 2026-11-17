import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render } from '@testing-library/react'

// ── A reload starts its page's reads while the app learns who you are ────────────────────────
//
// The shell shows its loading fallback until `GET /api/dashboard/config` answers, and used to
// mount the route, and so send its reads, only after. On production v0.3.9 that read answered
// 3.8 s after a reload on a calm machine, and Needs you's first card showed 5.7 to 7.3 s after
// it. Now the route's code and first reads start while identity is read, and the page joins them.
// Nothing renders early; and a device the gateway does not know keeps nothing it read before the
// app knew. This drives the REAL shell and identity from the hash, against a stubbed `fetch`.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))

class FakeSocket {
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) {}
  send(): void {}
  close(): void { this.readyState = 3 }
}

const { App } = await import('./App')
const { IdentityProvider } = await import('./identity')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
// Optional, so this file reads main's shell as well (red before the prefetch existed).
const prefetch = await import('./routePrefetch').catch(() => null)
const { peekEntry, peekQuery, resetDataStore } = await import('../lib/data')
const { NEEDS_YOU_KEY } = await import('../lib/useNeedsYou')

const BODIES: Record<string, unknown> = {
  '/api/waiting': { waiting: [], unreadable: [] },
  '/api/inbox/open': [],
  '/api/skills/proposals': { proposals: [], lastReview: null },
}
/** The reads Needs you makes for its rows. */
const NEEDS_READS = ['/api/waiting', '/api/inbox/open', '/api/skills/proposals']

/** Every GET that went out, by path, in order. */
let requests: string[] = []
/** The config read's answer, held until the test lets it go. */
let answerConfig: ((r: Response) => void) | null = null
/** How each read answers: 200 with its body, or the status a device the gateway does not know gets. */
let refuse: { reads: number | null; config: number | null } = { reads: null, config: null }
/** Once set, Needs you's reads are held: the page's own reads after it mounts never answer. */
let holdNeedsReads = false
/** Set, every read but the config waits for this to be called before it answers. */
let releaseReads: (() => void) | null = null
let readsHeld: Promise<void> | null = null

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  resetDataStore()
  prefetch?.resetRoutePrefetch()
  requests = []
  answerConfig = null
  refuse = { reads: null, config: null }
  holdNeedsReads = false
  releaseReads = null
  readsHeld = null
  location.hash = '#/mission-control'
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).split('?')[0]
    if ((init?.method ?? 'GET').toUpperCase() === 'GET') requests.push(path)
    if (path === '/api/dashboard/config') {
      if (refuse.config) return Promise.resolve(json({ error: 'not signed in' }, refuse.config))
      return new Promise<Response>((resolve) => { answerConfig = resolve })
    }
    if (refuse.reads) return Promise.resolve(json({ error: 'not signed in' }, refuse.reads))
    if (readsHeld) return readsHeld.then(() => json(path in BODIES ? BODIES[path] : []))
    if (holdNeedsReads && NEEDS_READS.includes(path)) return new Promise<Response>(() => {})
    return Promise.resolve(json(path in BODIES ? BODIES[path] : []))
  })
  Element.prototype.scrollIntoView ??= () => {}
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const settle = (ms = 100) => act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)) })

function shell() {
  return render(
    <ThemeProvider><AppearanceProvider><PersonalityProvider>
      <IdentityProvider><App /></IdentityProvider>
    </PersonalityProvider></AppearanceProvider></ThemeProvider>,
  )
}

describe('a reload of Needs you', () => {
  it('sends the page’s reads before the read of who you are answers, and paints what they read', async () => {
    const view = shell()
    await settle()

    expect(answerConfig, 'the shell reads who you are').not.toBeNull()
    for (const read of NEEDS_READS) {
      expect(requests, `${read} went out while the config read was still out`).toContain(read)
    }
    // Nothing of the page shows before identity answers.
    expect(view.container.querySelector('section[aria-label="Needs you"]')).toBeNull()
    expect(view.queryByText('Loading waiting list…')).toBeNull()
    expect(view.queryByText('Nothing is waiting on you.')).toBeNull()

    // From here the page's own reads never answer: what it paints is what the prefetch read.
    holdNeedsReads = true
    answerConfig!(json({ user_name: 'Ada', username: 'ada', onboarded: true }))
    await settle(300)

    expect(view.container.querySelector('section[aria-label="Needs you"]'), 'Needs you mounted').not.toBeNull()
    expect(view.queryByText('Loading waiting list…'), 'the page waited for reads of its own').toBeNull()
    expect(peekQuery(NEEDS_YOU_KEY), 'the prefetch landed what waits on you').toBeDefined()
  })

  it('keeps nothing in the cache for a device the gateway does not know', async () => {
    refuse = { reads: 401, config: 401 }
    shell()
    await settle(200)

    expect(requests).toContain('/api/waiting')
    expect(peekQuery(NEEDS_YOU_KEY), 'a refused read was kept as data').toBeUndefined()
  })

  it('writes nothing from a read that lands after the read of who you are failed', async () => {
    readsHeld = new Promise<void>((resolve) => { releaseReads = resolve })
    refuse = { reads: null, config: 403 }
    shell()
    await settle(200)  // identity has failed; the page's reads are still on the wire

    expect(requests).toContain('/api/waiting')
    releaseReads!()
    await settle(100)

    expect(peekQuery(NEEDS_YOU_KEY), 'an answer that landed after identity failed was kept').toBeUndefined()
  })

  it('forgets what it read when the read of who you are fails', async () => {
    // The page's reads answer; who you are does not. What they read must not stay.
    refuse = { reads: null, config: 403 }
    shell()
    await settle(200)

    expect(requests).toContain('/api/waiting')
    expect(peekQuery(NEEDS_YOU_KEY), 'reads made before identity failed were kept').toBeUndefined()
  })
})

describe('a reload of the Library', () => {
  it('sends the list and its side reads before the read of who you are answers, under the page’s keys', async () => {
    // An old Knowledge address (its `type` the page maps to a kind) and a sort kept on this device.
    location.hash = '#/library?type=note'
    localStorage.setItem('library:sort', 'name')
    shell()
    await settle()

    expect(answerConfig, 'the shell reads who you are').not.toBeNull()
    for (const read of ['/api/library', '/api/projects', '/api/knowledge/collections', '/api/knowledge/stats']) {
      expect(requests, `${read} went out while the config read was still out`).toContain(read)
    }
    const lib = await import('../pages/library/useLibrary')
    const key = lib.libraryKey(lib.libraryParamsOf(lib.mappedQuery({ type: 'note' }), 'name'))
    expect(key).toContain('"sort":"name"')
    expect(peekQuery(key), 'the list landed under the key the page reads').toBeDefined()
  })
})

describe('what a failed read of who you are leaves', () => {
  it('drops the copy this tab kept of a read the prefetch made', async () => {
    // An earlier load of this tab kept the projects list (other readers of it persist it); now the
    // Library reloads on a device the gateway no longer knows.
    sessionStorage.setItem('cache:projects:list', JSON.stringify({ v: [{ id: 'p1', name: 'Kept' }], at: 1 }))
    location.hash = '#/library'
    refuse = { reads: 401, config: null }
    shell()
    await settle()  // the Library's reads went out; the read of who you are is still out

    expect(requests).toContain('/api/projects')
    answerConfig!(json({ error: 'not signed in' }, 403))
    await settle(200)

    expect(sessionStorage.getItem('cache:projects:list'), 'the kept copy outlived the failed read').toBeNull()
    expect(peekEntry('projects:list'), 'a reader would still paint the kept copy').toBeUndefined()
  })

  it('lets the page that mounts on a retry write its own read while the forgotten one is still out', async () => {
    readsHeld = new Promise<void>((resolve) => { releaseReads = resolve })
    const view = shell()
    await settle()  // the prefetch's reads are on the wire, held
    expect(requests).toContain('/api/waiting')
    answerConfig!(json({ error: 'not signed in' }, 403))
    await settle()  // identity failed: what the prefetch started is forgotten, its reads still out

    // Signed in now: the retry reads who you are again, and Needs you mounts and reads.
    fireEvent.click(view.getByRole('button', { name: /Retry/ }))
    await settle()
    answerConfig!(json({ user_name: 'Ada', username: 'ada', onboarded: true }))
    await settle()
    expect(view.container.querySelector('section[aria-label="Needs you"]'), 'Needs you mounted').not.toBeNull()

    releaseReads!()
    await settle(300)

    expect(peekQuery(NEEDS_YOU_KEY), 'the page joined the forgotten read, which writes nothing').toBeDefined()
    expect(view.queryByText('Nothing is waiting on you.'), 'the page still waits for a read').not.toBeNull()
  })
})

describe('the prefetch itself', () => {
  it('starts nothing for an address naming a member every object has', () => {
    if (!prefetch || !('startRoutePrefetch' in prefetch)) throw new Error('no route prefetch')
    for (const hash of ['#/constructor', '#/__proto__', '#/toString', '#/hasOwnProperty', '#/valueOf']) {
      prefetch.resetRoutePrefetch()
      let keys: string[] | undefined
      expect(() => { keys = prefetch.startRoutePrefetch(hash) }, hash).not.toThrow()
      expect(keys, hash).toEqual([])
    }
    expect(requests, 'an inherited member started a read').toEqual([])
  })

  it('starts nothing once forgotten, even a read its route reaches after its code loads', async () => {
    if (!prefetch || !('startRoutePrefetch' in prefetch)) throw new Error('no route prefetch')
    // The Library's list read starts once its module loads: forgotten before that, it never goes.
    prefetch.startRoutePrefetch('#/library')
    prefetch.forgetRoutePrefetch()
    await settle(200)

    expect(requests, 'a read went out after the prefetch was forgotten').not.toContain('/api/library')
  })
})

