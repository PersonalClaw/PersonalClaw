import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

// ── Three Settings hub tiles that said things the server never said ──────────────────────────────
//
//   Search   "DuckDuckGo (keyless) is the default; add a provider in Providers to upgrade" — fixed
//            text, shown exactly when NO search provider existed (core names no vendor, so nothing
//            could search), pointing at the wrong page (providers come from the Store).
//   Chat     "Density: Comfortable / Compact" over `widget_density`, which is how readily the AGENT
//            uses inline widgets. Clicking Compact changed nothing on screen.
//   Updates  "Up to date" whenever `available` was false — including an install never compared with
//            anything, a pin naming no release, and checking switched off.
//
// Each tile is mounted for real over a stubbed `fetch`, so what is asserted is what the tile renders
// from what the server sends — not a source pattern that a rewrite could satisfy while still lying.

let routes: Record<string, unknown> = {}
const puts: { url: string; body: unknown }[] = []
const ok = (v: unknown) => new Response(JSON.stringify(v), { status: 200, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  puts.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if ((init?.method ?? 'GET') !== 'GET') {
      puts.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      return ok({ ok: true })
    }
    return ok(routes[url] ?? {})
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function mountTile(id: string) {
  const { SETTINGS_WIDGETS } = await import('./settingsWidgets')
  const tile = SETTINGS_WIDGETS.find((w) => w.id === id)
  if (!tile) throw new Error(`the ${id} tile is gone`)
  function Host() { return <>{tile!.render('', () => {})}</> }
  return render(<Host />)
}

const PROVIDER = (name: string) => ({
  name, display_name: name, available: true,
  capabilities: { returns_answer: false, returns_content: false, supports_fetch: false, supports_recency: false },
})
const WEB_SEARCH = { name: 'web_search', description: '', provider: 'web-tools' }

describe('the Search tile reads the real state', () => {
  const search = (providers: unknown[], tools: unknown[], useCases: Record<string, string[]> = {}) => {
    routes = {
      '/api/search/providers': { providers },
      '/api/search/active': { use_cases: useCases },
      '/api/tools': { tools },
    }
  }

  it('with no provider it says the agent cannot search — no invented default, and the Store, not Providers', async () => {
    search([], [WEB_SEARCH])
    const { container } = await mountTile('search')
    await waitFor(() => expect(container.textContent).toContain('No search provider'))
    const text = container.textContent ?? ''
    expect(text).toMatch(/can’t search the web until you install a search provider app from the Store/)
    expect(text, 'core ships no search vendor, so nothing is "the default"').not.toMatch(/DuckDuckGo|is the default/)
    expect(text, 'search providers are apps from the Store').not.toMatch(/in Providers/)
  })

  it('with providers but no web_search tool it says a chat turn cannot search, and where the tool ships', async () => {
    search([PROVIDER('tavily')], [{ name: 'web_fetch', description: '', provider: 'web-tools' }], { 'search-general': ['tavily'] })
    const { container } = await mountTile('search')
    await waitFor(() => expect(container.textContent).toContain('No search tool'))
    expect(container.textContent).toMatch(/the Web Tools app/)
    expect(container.textContent, 'a ticked binding would claim a search that cannot run').not.toContain('tavily')
  })

  it('names a working binding, and marks one whose provider is gone rather than ticking it', async () => {
    search([PROVIDER('tavily')], [WEB_SEARCH], { 'search-general': ['tavily'], 'search-news': ['duckduckgo'] })
    const { container } = await mountTile('search')
    await waitFor(() => expect(container.textContent).toContain('tavily'))
    expect(container.textContent).toContain('duckduckgo — not installed')
  })
})

describe('the Chat tile leaves widget density to the Chat page', () => {
  beforeEach(() => {
    routes = {
      '/api/dashboard/config': {
        restore_sessions: true, send_on_enter: true, show_timestamps: false, widget_density: 'more',
      },
    }
  })

  // The Chat card carries the three switches people flip (#5897). How readily the agent reaches for
  // inline widgets is set once and rarely, on the Chat page, under the label it has there, which is
  // where the relabel that once fixed this tile's words now lives alone.
  it('carries the three chat switches and no density control', async () => {
    const { container } = await mountTile('chat')
    for (const name of ['Restore sessions', 'Send on Enter', 'Timestamps']) {
      expect(await screen.findByRole('switch', { name }), name).toBeTruthy()
    }
    expect(screen.queryByRole('button', { name: /^Widget density/ })).toBeNull()
    expect(container.textContent).not.toMatch(/Comfortable|Compact/)
  })
})

describe('the Updates tile never says "Up to date" without a check that said so', () => {
  const BASE = { available: false, changes: '', auto: 'off', version: '0.1.3', kind: 'pip', check_enabled: true, pin: '' }
  const pill = async (check: Record<string, unknown>) => {
    routes = { '/api/update/check': { ...BASE, ...check } }
    const { container } = await mountTile('updates')
    await waitFor(() => expect(container.textContent).toContain('0.1.3'))
    return container.textContent ?? ''
  }

  it('an install whose check produced no answer says so', async () => {
    const text = await pill({ checked: false })
    expect(text).toContain("Couldn't check for updates")
    expect(text).not.toContain('Up to date')
  })

  it('a pin that names no release says so — nothing is offered while it stands', async () => {
    const text = await pill({ checked: true, pin: '0.2.1', pin_miss: true })
    expect(text).toContain('No release matches pin 0.2.1')
    expect(text).not.toContain('Up to date')
  })

  it('a pin set back to an older release says so — the install is about to go back, not "Up to date"', async () => {
    const text = await pill({ checked: true, current: '0.1.3', pin: '0.1.2', latest: '0.1.2', pin_older: true })
    expect(text).toContain('Pinned to v0.1.2, older than this build (v0.1.3)')
    expect(text).not.toContain('Up to date')
  })

  it('checking switched off is not a verdict about the install', async () => {
    const text = await pill({ checked: true, check_enabled: false })
    expect(text).toContain('Automatic update checks are off')
    expect(text).not.toContain('Up to date')
  })

  it('a check that compared and found nothing newer does say "Up to date" (vacuity floor)', async () => {
    expect(await pill({ checked: true })).toContain('Up to date')
  })

  it('an available update is still headlined with its version', async () => {
    expect(await pill({ checked: true, available: true, latest: '0.1.4' })).toContain('Update available — 0.1.4')
  })
})
