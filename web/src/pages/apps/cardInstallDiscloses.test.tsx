// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { AppCatalogEntry } from '../../lib/api'

// ── The FASTEST install path has to disclose the MOST, not the least ─────────────────────────────
//
// `installConsent`'s own header comment promised that "installing from onboarding must disclose
// exactly what installing from the Store discloses — same bullets, same advisory rows, same
// 'Install anyway'". Two of the modal's four callers broke that promise: the Store CARD's footer
// Install and Manage Sources → Install rendered `ConsentModal` with the scan report and nothing
// else, so a one-click install from the card grid consented to the scanner's findings and never
// showed what the app is permitted to do or what it will run unattended.
//
// 🔑 THIS IS A CALLER TEST ON PURPOSE. The modal now requires `permissions`/`crons` as props, which
// makes forgetting them a type error — but a caller can still satisfy the type by passing
// `undefined` it never looked up. Only a driven render of the grid proves the card actually hands
// the catalog entry's grants to the consent surface. `installConsent.test.tsx` pins what the modal
// does with them; this pins that the card-grid and source-list paths supply them at all.

const blocked = {
  ok: false,
  needsConsent: true,
  scan: { verdict: 'warning', findings: [{ surface: 'script', severity: 'warning', rule: 'python_exec', path: 'server/provider.py', evidence: 'subprocess.run(["curl", "-s", url])' }], signature: null },
}

vi.mock('../../lib/api', () => ({
  api: {
    installApp: () => Promise.resolve(blocked),
    removeAppSource: () => Promise.resolve({}),
    addAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    addLocalAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    removeLocalAppSource: () => Promise.resolve({}),
  },
}))
vi.mock('../../lib/useGuardedInstall', () => ({
  useGuardedInstall: () => ({
    install: () => Promise.resolve(blocked),
    confirmInstall: () => Promise.resolve(blocked),
    reset: () => {},
    blocked,
    busy: false,
    error: null,
    fixPrompt: null,
  }),
  guardedFromApp: (r: unknown) => r,
  isBlockingResult: () => true,
  terminalRefusalReason: () => '',
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

// Imported after the mocks so the components bind them.
import { StoreView, SourcesPanel } from './AppsSection'

const SOURCE = 'https://github.com/acme/reporter.git'

const entry: AppCatalogEntry = {
  name: 'reporter', displayName: 'Reporter', description: 'reports', version: '1.0.0',
  icon: '', author: 'acme', source: SOURCE, sourceKind: 'git',
  isProvider: false, providerType: '', tags: [],
  permissions: { api: ['/api/knowledge'], cron: true, agent: true, network: false },
  crons: [{ name: 'nightly-digest', cron_expr: '23 * * * *', cadence: 'At 23 minutes past the hour', agent: 'researcher', message: 'summarise the day' }],
}

const catalog = {
  bundled: [], gitSources: [SOURCE], defaultGitSources: [], builtinGitSources: [],
  localSources: [], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [entry],
}

/** Everything the consent dialog says, whitespace-normalized. */
const dialogText = () =>
  (screen.getByRole('dialog').textContent ?? '').replace(/\s+/g, ' ')

async function openConsentFrom(button: HTMLElement) {
  fireEvent.click(button)
  await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
}

describe('a card-grid install discloses the grants at consent', () => {
  const grid = () => render(
    <StoreView catalog={catalog} result={[{ ...entry, installed: false, enabled: false, hasUI: false }]}
      totalKnown={1} installedCount={0} onInstalled={() => {}} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )

  it('shows the enforced permissions and the scheduled job, not just the scan', async () => {
    grid()
    await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    const text = dialogText()
    // The scan report is still there — this adds disclosure, it removes none.
    expect(text).toMatch(/Security scan: warning/)
    expect(text).toMatch(/python_exec/)
    // …and now so are the grants the card used to skip.
    expect(text).toMatch(/Permissions the gateway enforces/)
    expect(text).toMatch(/API: \/api\/knowledge/)
    expect(text).toMatch(/Run background agents/)
    expect(text).toMatch(/Scheduled jobs/)
    expect(text).toMatch(/nightly-digest/)
  })

  it('carries the app’s own network claim through, rather than a default', async () => {
    // The card's entry declares `network: false`. If the caller passed a hand-made or empty
    // permissions object instead of the entry's, this would read "not declared".
    grid()
    await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    expect(dialogText()).toMatch(/Network access: declared as denied/)
  })

  it('words the cron cadence instead of showing the crontab line', async () => {
    grid()
    await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    const text = dialogText()
    expect(text).toMatch(/At 23 minutes past the hour/)
    expect(text, 'the raw expression belongs in the tooltip, not the row').not.toMatch(/23 \* \* \* \*/)
    expect(screen.getByRole('dialog').querySelector('[title="cron: 23 * * * *"]')).toBeTruthy()
  })

  it('glosses the scanner rule in words a non-expert can act on', async () => {
    grid()
    await openConsentFrom(screen.getByRole('button', { name: /^Install$/ }))
    const text = dialogText()
    // "This code", not "The app": one gloss map serves both consent surfaces (#2535 wired the
    // skills marketplace in), so the sentence cannot name one of them. See scanFindings.ts.
    expect(text).toMatch(/This code runs an external program on your machine\./)
    expect(text, 'the map must not name one of its two surfaces').not.toMatch(/The app runs an external/)
    // The gloss complements the evidence; it does not replace the real argv.
    expect(text).toMatch(/subprocess\.run/)
  })
})

describe('a Manage Sources install discloses the grants for an indexed source', () => {
  it('resolves the catalog entry for the source URL and shows its grants', async () => {
    render(<SourcesPanel catalog={catalog} reloadCatalog={() => {}} onInstalled={() => {}} />)
    const row = screen.getByText(SOURCE).parentElement!
    await openConsentFrom(row.querySelector('button')!)
    const text = dialogText()
    expect(text).toMatch(/Permissions the gateway enforces/)
    expect(text).toMatch(/Scheduled jobs/)
    expect(text).toMatch(/nightly-digest/)
  })

  it('states that the grants are unknown for a source the catalog has not indexed', async () => {
    // An un-indexed source has no manifest until the install fetches it. Saying so is a
    // different disclosure from an empty bullet list, which would read as "grants nothing".
    const unindexed = 'https://github.com/acme/unknown.git'
    render(<SourcesPanel catalog={{ ...catalog, gitSources: [unindexed], gitApps: [] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    const row = screen.getByText(unindexed).parentElement!
    await openConsentFrom(row.querySelector('button')!)
    const text = dialogText()
    expect(text).toMatch(/could not read this app's declared permissions/)
    expect(text).not.toMatch(/granted no gateway capability/)
  })
})
