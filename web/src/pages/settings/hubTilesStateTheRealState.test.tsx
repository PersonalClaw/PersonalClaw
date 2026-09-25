import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

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
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)) }

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
    expect(container.textContent).toMatch(/Native Tools \(Web\)/)
    expect(container.textContent, 'a ticked binding would claim a search that cannot run').not.toContain('tavily')
  })

  it('names a working binding, and marks one whose provider is gone rather than ticking it', async () => {
    search([PROVIDER('tavily')], [WEB_SEARCH], { 'search-general': ['tavily'], 'search-news': ['duckduckgo'] })
    const { container } = await mountTile('search')
    await waitFor(() => expect(container.textContent).toContain('tavily'))
    expect(container.textContent).toContain('duckduckgo — not installed')
  })
})

describe('the Chat tile names widget density for what it is', () => {
  beforeEach(() => {
    routes = {
      '/api/dashboard/config': {
        restore_sessions: true, send_on_enter: true, show_timestamps: false, widget_density: 'more',
      },
    }
  })

  it('labels it the way the Chat panel does — More / Less inline widgets, not a layout density', async () => {
    const { container } = await mountTile('chat')
    await screen.findByRole('button', { name: 'Widget density: More' })
    expect(screen.getByRole('button', { name: 'Widget density: Less' })).toBeTruthy()
    expect(container.textContent).not.toMatch(/Comfortable|Compact/)
  })

  it('still writes `widget_density` — the relabel changed the words, not the wiring', async () => {
    await mountTile('chat')
    const less = await screen.findByRole('button', { name: 'Widget density: Less' })
    await act(async () => { fireEvent.click(less); await flush() })
    expect(puts).toContainEqual({ url: '/api/dashboard/config', body: { widget_density: 'less' } })
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

  it('checking switched off is not a verdict about the install', async () => {
    const text = await pill({ checked: true, check_enabled: false })
    expect(text).toContain('Update checks are off')
    expect(text).not.toContain('Up to date')
  })

  it('a check that compared and found nothing newer does say "Up to date" (vacuity floor)', async () => {
    expect(await pill({ checked: true })).toContain('Up to date')
  })

  it('an available update is still headlined with its version', async () => {
    expect(await pill({ checked: true, available: true, latest: '0.1.4' })).toContain('Update available — 0.1.4')
  })
})
