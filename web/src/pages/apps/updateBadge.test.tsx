// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { AppCatalogEntry } from '../../lib/api'

// ── APE-7: the installed-card "Update" badge ────────────────────────────────────────────────────
//
// The behavioral half (apps/catalog + handlers/apps + test_app_catalog) proves the wire fields
// `updateAvailable` / `latestVersion` are COMPUTED and tagged onto the app on the read path. This
// proves the card actually RENDERS them — and, as importantly, that it renders them ONLY for an
// installed app that has one. The badge is an installed-card affordance (`item.installed &&
// item.updateAvailable`); a store listing for a not-yet-installed app must not wear it, and an app
// with no update must show nothing rather than an empty chrome.

vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))
vi.mock('../../lib/api', () => ({ api: {} }))
vi.mock('../../lib/useGuardedInstall', () => ({
  useGuardedInstall: () => ({
    install: () => Promise.resolve({}), confirmInstall: () => Promise.resolve({}),
    reset: () => {}, blocked: null, busy: false, error: null, fixPrompt: null,
  }),
  guardedFromApp: (r: unknown) => r,
  isBlockingResult: () => false,
  terminalRefusalReason: () => '',
}))

// Imported after the mocks so the component binds them.
import { StoreView } from './AppsSection'

const entry: AppCatalogEntry = {
  name: 'reporter', displayName: 'Reporter', description: 'reports', version: '1.0.0',
  icon: '', author: 'acme', source: 'https://github.com/acme/reporter.git', sourceKind: 'git',
  isProvider: false, providerType: '', tags: [],
  permissions: { api: [], cron: false, agent: false, network: false }, crons: [],
}

const EMPTY_CATALOG = {
  bundled: [], gitSources: [], defaultGitSources: [], builtinGitSources: [],
  localSources: [], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [],
}

const grid = (over: Record<string, unknown>) => render(
  <StoreView
    catalog={EMPTY_CATALOG}
    result={[{ ...entry, installed: true, enabled: true, hasUI: false, ...over }]}
    totalKnown={1} installedCount={1} onInstalled={() => {}} reloadCatalog={() => {}}
    onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
    onAction={(() => {}) as never} onOpenSources={() => {}} />,
)

describe('APE-7 — installed-card update badge', () => {
  it('renders the Update badge, titled with the latest version, for an installed app that has one', () => {
    grid({ updateAvailable: true, latestVersion: '2.0.0' })
    const badge = screen.getByTitle('Update to v2.0.0 available')
    expect(badge).toBeInTheDocument()
    expect(badge.textContent).toContain('Update')
  })

  it('falls back to a generic title when the latest version is not known', () => {
    grid({ updateAvailable: true })
    expect(screen.getByTitle('Update available')).toBeInTheDocument()
    expect(screen.queryByTitle(/Update to v/)).toBeNull()
  })

  it('renders NO badge when no update is available — absent is not an empty chrome', () => {
    grid({ updateAvailable: false, latestVersion: undefined })
    expect(screen.queryByTitle(/Update.*available/)).toBeNull()
  })

  it('renders NO badge for an uninstalled catalog app, even with a newer version', () => {
    // The badge is an INSTALLED-card affordance; a store listing for a not-yet-installed app
    // must not claim an available update.
    grid({ installed: false, enabled: false, updateAvailable: true, latestVersion: '2.0.0' })
    expect(screen.queryByTitle(/Update.*available/)).toBeNull()
  })
})
