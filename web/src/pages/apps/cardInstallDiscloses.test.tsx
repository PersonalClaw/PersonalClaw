// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { AppCatalogEntry, AppDisclosure, AppInstallResult } from '../../lib/api'

// ── The FASTEST install path has to disclose the MOST, not the least ─────────────────────────────
//
// The Store CARD's footer Install and Manage Sources → Install are the two one-click paths. They
// used to consent over the scanner's findings alone — and on a clean scan over nothing at all —
// never showing what the app is permitted to do or what it will run unattended.
//
// 🔑 THIS IS A CALLER TEST ON PURPOSE. Both paths now open the one consent dialog, which discloses
// the SERVER'S reading of the source it is handed (`POST /api/apps/preview`). So the caller's whole
// job is to hand it the right source — a card that reviewed the wrong one would show a true
// disclosure of the wrong app. `installConsent.test.tsx` pins what the dialog does with a review;
// this pins that the card grid and the source list ask for the review of the app on screen.

const previewApp = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    previewApp: (...a: unknown[]) => previewApp(...a),
    installApp: () => Promise.reject(new Error('nothing is confirmed in this file')),
    removeAppSource: () => Promise.resolve({}),
    addAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    addLocalAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    removeLocalAppSource: () => Promise.resolve({}),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

// Imported after the mocks so the components bind them.
import { StoreView, SourcesPanel } from './AppsSection'

const SOURCE = 'https://github.com/acme/reporter.git'

const GRANTS: AppDisclosure = {
  permissions: { api: ['/api/knowledge'], cron: true, agent: true, network: false },
  crons: [{
    name: 'nightly-digest', cron_expr: '23 * * * *', cadence: 'At 23 minutes past the hour',
    agent: 'researcher', message: 'summarise the day', scheduled: true,
  }],
  pythonDependencies: [], hasUI: false, uiComponents: '', hasBackend: false, onInstall: '', onUpdate: '', mcpServers: [],
}

/** The server's review of whatever source it was asked about. */
function reviewOf(name: string, displayName: string, disclosure: AppDisclosure): AppInstallResult {
  return {
    ok: false, name, error: '', needs_consent: true,
    scan: {
      verdict: 'warning', tier: 'community', signature: null,
      findings: [{ surface: 'script', severity: 'warning', rule: 'python_exec', path: 'server/provider.py', evidence: 'subprocess.run(["curl", "-s", url])' }],
    },
    displayName, version: '1.0.0', disclosure, previous: null, consent: 'r'.repeat(64),
  }
}

const entry: AppCatalogEntry = {
  name: 'reporter', displayName: 'Reporter', description: 'reports', version: '1.0.0',
  icon: '', author: 'acme', source: SOURCE, sourceKind: 'git',
  isProvider: false, providerType: '', tags: [], consentKnown: true, ...GRANTS,
}

const catalog = {
  bundled: [], gitSources: [SOURCE], defaultGitSources: [], builtinGitSources: [],
  localSources: [], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [entry],
}

beforeEach(() => {
  previewApp.mockReset()
  previewApp.mockResolvedValue(reviewOf('reporter', 'Reporter', GRANTS))
})

/** Everything the consent dialog says once the review is in, whitespace-normalized. */
async function openConsentFrom(button: HTMLElement): Promise<string> {
  fireEvent.click(button)
  await waitFor(() => expect(screen.getByRole('dialog').textContent).toMatch(/Security scan:/))
  return (screen.getByRole('dialog').textContent ?? '').replace(/\s+/g, ' ')
}

describe('a card-grid install discloses the grants at consent', () => {
  const grid = () => render(
    <StoreView catalog={catalog} result={[{ ...entry, installed: false, enabled: false, hasUI: false }]}
      totalKnown={1} installedCount={0} onInstalled={() => {}} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )

  it('reviews THIS card’s source and shows the enforced permissions and the job, not just the scan', async () => {
    grid()
    const text = await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    expect(previewApp).toHaveBeenCalledWith(SOURCE, undefined)
    // The scan report is there…
    expect(text).toMatch(/Security scan: warning/)
    expect(text).toMatch(/python_exec/)
    // …and so are the grants the card used to skip.
    expect(text).toMatch(/Permissions the gateway enforces/)
    expect(text).toMatch(/API: \/api\/knowledge/)
    expect(text).toMatch(/Run background agents/)
    expect(text).toMatch(/Scheduled jobs/)
    expect(text).toMatch(/nightly-digest/)
  })

  it('carries the app’s own network claim through, rather than a default', async () => {
    // The review declares `network: false`. A dialog that rendered a hand-made or empty
    // permissions object instead would read "not declared".
    grid()
    expect(await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))).toMatch(/Network access: declared as denied/)
  })

  it('words the cron cadence instead of showing the crontab line', async () => {
    grid()
    const text = await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    expect(text).toMatch(/At 23 minutes past the hour/)
    expect(text, 'the raw expression belongs in the tooltip, not the row').not.toMatch(/23 \* \* \* \*/)
    expect(screen.getByRole('dialog').querySelector('[title="cron: 23 * * * *"]')).toBeTruthy()
  })

  it('glosses the scanner rule in words a non-expert can act on', async () => {
    grid()
    const text = await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    // "This code", not "The app": one gloss map serves both consent surfaces (#2535 wired the
    // skills marketplace in), so the sentence cannot name one of them. See scanFindings.ts.
    expect(text).toMatch(/This code runs an external program on your machine\./)
    expect(text, 'the map must not name one of its two surfaces').not.toMatch(/The app runs an external/)
    // The gloss complements the evidence; it does not replace the real argv.
    expect(text).toMatch(/subprocess\.run/)
  })
})

describe('a Manage Sources install discloses the grants for the source it installs', () => {
  it('reviews the indexed source and shows its grants', async () => {
    render(<SourcesPanel catalog={catalog} reloadCatalog={() => {}} onInstalled={() => {}} />)
    const row = screen.getByText(SOURCE).parentElement!
    const text = await openConsentFrom(row.querySelector('button')!)
    expect(previewApp).toHaveBeenCalledWith(SOURCE, undefined)
    expect(text).toMatch(/Permissions the gateway enforces/)
    expect(text).toMatch(/Scheduled jobs/)
    expect(text).toMatch(/nightly-digest/)
  })

  it('discloses a source the catalog never indexed just as fully — from the server’s reading', async () => {
    // An un-indexed source has no catalog row, so nothing in the Store knew its grants. It used to
    // install behind a dialog that said so; the review reads the manifest itself, so there is
    // nothing left unknown at the moment of consent.
    const unindexed = 'https://github.com/acme/unknown.git'
    previewApp.mockResolvedValue(reviewOf('unknown-app', 'Unknown App', {
      ...GRANTS, permissions: { storage: true }, crons: [],
    }))
    render(<SourcesPanel catalog={{ ...catalog, gitSources: [unindexed], gitApps: [] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    const row = screen.getByText(unindexed).parentElement!
    const text = await openConsentFrom(row.querySelector('button')!)
    expect(previewApp).toHaveBeenCalledWith(unindexed, undefined)
    expect(text).toMatch(/Storage/)
    expect(text).not.toMatch(/not known|could not read/)
    // Titled with the name the review read, not the URL the row showed.
    expect(screen.getByRole('dialog', { name: 'Install Unknown App' })).toBeTruthy()
  })

  it('titles a registry’s own row with the registry, never with the first app it lists', async () => {
    // A registry-listed row carries the REGISTRY as its `source` and installs from its own
    // `pointer`. Matching the source row on `source` titled "Install channel-null" for a click on
    // the registry itself — measured in the browser. Until the review answers, the row's URL is
    // the only true name there is.
    const registry = 'https://github.com/acme/registry.git'
    const listed: AppCatalogEntry = {
      ...entry, name: 'channel-null', displayName: 'Null Channel', source: registry,
      pointer: 'https://github.com/acme/channel-null', consentKnown: false,
    }
    previewApp.mockReturnValue(new Promise(() => {}))  // the review is still on its way
    render(<SourcesPanel catalog={{ ...catalog, gitSources: [registry], gitApps: [listed] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    fireEvent.click(screen.getByText(registry).parentElement!.querySelector('button')!)
    expect(await screen.findByRole('dialog', { name: `Install ${registry}` })).toBeTruthy()
    expect(previewApp).toHaveBeenCalledWith(registry, undefined)
    expect(screen.queryByRole('dialog', { name: /Null Channel/ })).toBeNull()
  })
})
