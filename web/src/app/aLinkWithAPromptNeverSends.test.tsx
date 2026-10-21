import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, waitFor } from '@testing-library/react'

// ── A link with a prompt fills the composer and sends nothing ──────────────────────────────────
//
// `personalclaw://threads/new?prompt=hello` reaches the page as `#/chat/new?prompt=hello`. It is
// input from any app or web page on the machine, so the product's consent rule holds for it: the
// text is shown in a new thread's composer, and only the person sends it. This drives the REAL
// shell from the hash, and records every gateway call and every request, so "nothing was sent"
// is read off what went out rather than off one spy.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

// The control's switch. Off, the composer below is the real one, unchanged. On, it sends its draft
// the moment it mounts, which is the defect the checks in this file exist to catch: the control
// test turns it on and shows that each check fails. Either way it records the draft each composer
// mounts with.
const ctl = vi.hoisted(() => ({ sendOnMount: false, mounts: [] as string[] }))
vi.mock('../pages/chat/composer/useChatComposer', async (orig) => {
  const real = await orig<typeof import('../pages/chat/composer/useChatComposer')>()
  const { useEffect } = await import('react')
  return {
    ...real,
    useChatComposer: (...args: Parameters<typeof real.useChatComposer>) => {
      const composer = real.useChatComposer(...args)
      useEffect(() => {
        ctl.mounts.push(args[0].seed)
        if (ctl.sendOnMount) void composer.send()
      }, []) // eslint-disable-line react-hooks/exhaustive-deps
      return composer
    },
  }
})

const h = vi.hoisted(() => ({ calls: [] as string[] }))
const ENVELOPES: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
}
// Each call is recorded by name AND made for real, so it goes out as the request it is (to the
// stubbed `fetch` below); the page is handed a benign answer. The two checks are therefore
// independent: one reads the names, the other reads what went out.
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const realApi = real.api as unknown as Record<string, unknown>
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      h.calls.push(prop)
      const fn = realApi[prop]
      if (typeof fn === 'function') {
        try { void Promise.resolve((fn as (...a: unknown[]) => unknown)(...args)).catch(() => {}) } catch { /* the stub's answer stands */ }
      }
      return Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : [])
    },
  })
  return { ...real, api: stub }
})

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
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
await import('../pages/ChatPage')

/** Every request that went out, as `METHOD url`. */
let requests: string[] = []

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  h.calls.length = 0
  requests = []
  ctl.sendOnMount = false
  ctl.mounts.length = 0
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    requests.push(`${(init?.method ?? 'GET').toUpperCase()} ${String(input)}`)
    return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }))
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

/** The api calls that would start, send, approve, answer or run something. `running` reads the list
 *  of live work, and runs nothing. */
const ACTS = /^(send|create|start|approve|answer|run(?!ning)|resolve|decide|steer|submit|cancel|delete)/i
const acted = () => h.calls.filter((c) => ACTS.test(c))
const posted = () => requests.filter((r) => !r.startsWith('GET '))

/** The two checks every test here makes, each on its own so the control can show each one fail. */
const checkNoActingCall = () => expect(acted(), 'no call that starts, sends or approves anything').toEqual([])
const checkNoWrite = () => expect(posted(), 'no request that writes anything').toEqual([])
const checkNothingActed = () => { checkNoActingCall(); checkNoWrite() }

function shell() {
  return render(<ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>)
}

async function composerText(container: HTMLElement): Promise<string> {
  const editor = await waitFor(() => {
    const el = container.querySelector('.cm-content') as HTMLElement | null
    expect(el, 'the new thread mounted its composer').toBeTruthy()
    return el!
  })
  return editor.textContent ?? ''
}

/** Let every effect and read the page starts on mount settle. */
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)) })

describe('a link that opens a new thread with a prompt', () => {
  it('leaves the prompt in the composer, and creates no thread and sends no message', async () => {
    location.hash = '#/chat/new?prompt=hello'
    const { container } = shell()
    await waitFor(async () => expect(await composerText(container)).toContain('hello'))
    await settle()

    checkNothingActed()
    expect(await composerText(container)).toContain('hello')
    expect(ctl.mounts, 'the new thread mounted once, with its draft already in it').toEqual(['hello'])
  })

  it("records the draft in the address under the page's own key, so a reload shows it again unsent", async () => {
    location.hash = '#/chat/new?prompt=hello'
    const entries = history.length
    const first = shell()
    await waitFor(async () => expect(await composerText(first.container)).toContain('hello'))
    await waitFor(() => expect(location.hash).toBe('#/chat/new?seed=hello'))
    expect(history.length, 'the rewrite replaced the entry the link opened, and added none').toBe(entries)
    first.unmount()

    // The reload: the same address, a fresh shell.
    const again = shell()
    await waitFor(async () => expect(await composerText(again.container)).toContain('hello'))
    await settle()
    expect(acted()).toEqual([])
    expect(posted()).toEqual([])
  })

  it('sends nothing when the address also holds a key that would act', async () => {
    location.hash = '#/chat/new?prompt=hello&send=1'
    const { container } = shell()
    await waitFor(async () => expect(await composerText(container)).toContain('hello'))
    await settle()
    expect(acted()).toEqual([])
    expect(posted()).toEqual([])
  })
})

describe('a link that opens a thread with a pane', () => {
  it("writes the pane as the thread's own flag", async () => {
    location.hash = '#/chat/web%3Aabc?pane=activity'
    const entries = history.length
    shell()
    await waitFor(() => expect(location.hash).toBe('#/chat/web:abc?activity=1'))
    await settle()
    expect(history.length, 'the rewrite replaced the entry the link opened, and added none').toBe(entries)
    checkNothingActed()
  })
})

/** What the desktop does with a link once the app is open: it assigns the window's hash. */
const deliver = (hash: string) => act(async () => {
  location.hash = hash
  await new Promise((resolve) => setTimeout(resolve, 50))
})

describe('a link delivered to a window that is already open, as the desktop delivers it', () => {
  // [where the window is, the link's hash, the address the link leaves, the draft it replaces]
  const rows: Array<[string, string, string, string]> = [
    ['#/dashboard', '#/chat/new?prompt=hello', '#/chat/new?seed=hello', ''],
    ['#/chat', '#/chat/new?prompt=hello', '#/chat/new?seed=hello', ''],
    ['#/chat/new', '#/chat/new?prompt=hello', '#/chat/new?seed=hello', ''],
    ['#/chat/new?project=p-1', '#/chat/new?project=p-1&prompt=hello', '#/chat/new?project=p-1&seed=hello', ''],
    ['#/chat/new?prompt=first', '#/chat/new?prompt=hello', '#/chat/new?seed=hello', 'first'],
  ]
  for (const [from, link, address, before] of rows) {
    it(`${from}, then ${link}: the composer shows the prompt the address names, unsent`, async () => {
      location.hash = from
      const { container } = shell()
      if (before) await waitFor(async () => expect(await composerText(container)).toContain(before))
      await settle()
      const entries = history.length
      const mounted = ctl.mounts.length

      await deliver(link)
      await waitFor(() => expect(location.hash).toBe(address))
      await waitFor(async () => expect(await composerText(container), 'the composer shows the draft the address names').toContain('hello'))
      await settle()
      if (before) expect(await composerText(container)).not.toContain(before)
      expect(location.hash).toBe(address)
      expect(ctl.mounts.slice(mounted), 'the link mounted one new thread, holding its draft').toEqual(['hello'])
      expect(history.length, 'the link added its own entry, and the rewrite none').toBe(entries + 1)
      checkNothingActed()
    })
  }

  it('onto a new thread, a pane link opens the pane and leaves the composer as it is', async () => {
    location.hash = '#/chat/new'
    const { container } = shell()
    await composerText(container)
    await settle()
    const entries = history.length
    const mounted = ctl.mounts.length

    await deliver('#/chat/new?pane=history')
    await waitFor(() => expect(location.hash).toBe('#/chat/new?history=1'))
    await settle()
    expect(ctl.mounts.slice(mounted), 'the composer was not mounted again').toEqual([])
    expect(history.length, 'the link added its own entry, and the rewrite none').toBe(entries + 1)
    checkNothingActed()
  })

  it("onto the thread it names: the pane becomes the thread's flag", async () => {
    location.hash = '#/chat/web%3Aabc'
    shell()
    await settle()
    const entries = history.length

    await deliver('#/chat/web%3Aabc?pane=activity')
    await waitFor(() => expect(location.hash).toBe('#/chat/web:abc?activity=1'))
    await settle()
    expect(history.length, 'the link added its own entry, and the rewrite none').toBe(entries + 1)
    checkNothingActed()
  })
})

describe('the control: a composer that sends on mount', () => {
  it('is caught by the name check and by the request check, each on its own', async () => {
    ctl.sendOnMount = true
    location.hash = '#/chat/new?prompt=hello'
    shell()
    await waitFor(() => expect(acted().length, 'the planted send reached the gateway api').toBeGreaterThan(0))
    await settle()

    expect(checkNoActingCall, `the name check missed: ${acted().join(', ')}`).toThrow(/no call that starts, sends or approves anything/)
    expect(checkNoWrite, `the request check missed: ${posted().join(', ')}`).toThrow(/no request that writes anything/)
    expect(posted().some((r) => r.startsWith('POST /api/chat')), posted().join(', ')).toBe(true)
  })
})
