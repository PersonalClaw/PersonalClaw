// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import type { AppSummary } from '../../lib/api'

// ── A native app is updated with PersonalClaw, so its panel offers no Update ────────────────
//
// A native app ships inside PersonalClaw and its files are re-synced from the package at every
// start, and the gateway refuses an update of one from a source. Its panel offered Update all
// the same, which opened a dialog whose every answer was a refusal. An app installed from a
// source keeps its Update. Only the API is mocked; the Library and its panel are the shipped
// components.

function app(over: Partial<AppSummary> = {}): AppSummary {
  return {
    name: 'fixture-search', displayName: 'Fixture Search', version: '1.0.0',
    description: 'Ranked links for a query.',
    enabled: true, origin: 'local', source: '/apps/fixture-search', icon: '', hasBackend: false,
    hasUI: false, uiPages: [], isProvider: true, providerType: 'search', hasConfig: true,
    permissions: {}, tags: [], backendRunning: false, backendPort: null, disclosure: null,
    ...over,
  }
}

async function open(item: AppSummary, view: 'native' | 'library') {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      apps: () => Promise.resolve([item]),
      appCatalog: () => Promise.resolve({ bundled: [], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [] }),
    },
  }))
  const { AppsSection } = await import('./AppsSection')
  render(<AppsSection query={{ view, open: item.name }} setQuery={() => {}} navigate={() => {}} />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Configure/ })).toBeTruthy())
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  vi.doMock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("a native app's panel", () => {
  it('offers no Update, and says it is updated with PersonalClaw', async () => {
    await open(app({ name: 'shipped-search', displayName: 'Shipped Search', native: true, origin: 'builtin', source: 'builtin' }), 'native')
    expect(screen.getByText(/Native app — always on/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Update/ })).toBeNull()
    expect(screen.getByText(/is updated with it/)).toBeTruthy()
  })

  it('an app installed from a source keeps its Update', async () => {
    await open(app(), 'library')
    expect(screen.getByRole('button', { name: /^Update/ })).toBeTruthy()
  })
})
