/** Manage Sources briefly asserted the OPPOSITE of the fact it exists to disclose (#2629).
 *
 * The panel's two empty states — *"No git sources configured"* and *"No local sources"* — are
 * claims about CONFIGURATION, and they were rendered on a fact about LOADING. Both read from
 * `catalog?.gitSources ?? []`, so an in-flight read and a failed read collapse to the same `[]`
 * as a genuinely empty configuration. Absent and zero are different facts (the family this repo
 * has now hit repeatedly: `ledger.run_totals` reporting an absent cost as `0.0` in #2566,
 * `bundled_source_enabled` conflating an unreadable config with a configured `True`).
 *
 * 🔴 ON THIS PANEL IT IS NOT COSMETIC. Manage Sources is the egress-disclosure surface #2528
 * built: it is where a user decides whether a NETWORK app source stays enabled, and the
 * disclosure paragraph ("Reading these listings contacts github.com…") is itself gated on
 * `networkSources.length > 0` from the same absent object. So a user who opened the panel during
 * the fetch read "No git sources configured" with no egress notice, correctly concluded from what
 * was on screen that nothing reached the network, and closed a panel that then populated with the
 * sources that were there all along.
 *
 * 🪤 AND THE FAILED READ WAS THE WORSE HALF. Measured at the fixing commit's parent:
 * `StoreView` already took `catalogError` and rendered `LoadError` (`AppsSection.tsx:840`), while
 * `SourcesPanel` was handed `catalog` alone — so a 500 on the catalog endpoint was
 * INDISTINGUISHABLE here from a deliberately empty configuration, offered no retry, and the grid
 * and the panel beside it disagreed about the same failed request.
 *
 * Driven through the component, not read off the source: a source-text assertion would pass with
 * the guard deleted as long as the words still appeared somewhere in the file.
 *
 * THE VACUITY FLOOR RUNS BOTH WAYS, which is what makes this rail worth having:
 *  · unsettled  ⇒ NEITHER sentence is on screen, and a busy live region IS;
 *  · settled+empty ⇒ BOTH sentences ARE on screen (so the fix is not "delete the empty state");
 *  · failed ⇒ the error surface with a retry, and NEITHER sentence.
 * Drop the guard and case 1 reds; "fix" it by deleting the copy and case 2 reds.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { SourcesPanel } from './AppsSection'

vi.mock('../../lib/api', () => ({
  api: {
    removeAppSource: () => Promise.resolve({}),
    addAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    addLocalAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    removeLocalAppSource: () => Promise.resolve({}),
    installApp: () => Promise.resolve({ ok: true }),
  },
}))
vi.mock('../../lib/useGuardedInstall', () => ({
  useGuardedInstall: () => ({
    install: () => Promise.resolve({ ok: true }),
    confirmInstall: () => Promise.resolve({ ok: true }),
    reset: () => {}, blocked: null, busy: false, error: null, fixPrompt: null,
  }),
  guardedFromApp: (r: unknown) => r,
  isBlockingResult: () => false,
  terminalRefusalReason: () => null,
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

/** A catalog that HAS answered and is genuinely empty — the only state the two sentences
 *  are true in. Deliberately not `{}`: every list the panel reads is present and empty. */
const ANSWERED_EMPTY = {
  bundled: [], gitSources: [], defaultGitSources: [], builtinGitSources: [],
  localSources: [], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [],
  networkSources: [],
}

const GIT_EMPTY = /No git sources configured/
const LOCAL_EMPTY = /No local sources/

describe('Manage Sources states a configuration fact only once the catalog has answered', () => {
  it('🪤 says NEITHER "none configured" sentence while the catalog request is outstanding', () => {
    // `undefined` IS the in-flight state: `useQuery` returns `data: undefined` until the first
    // answer lands for a key, which is the whole window the report was filed about.
    render(<SourcesPanel catalog={undefined} reloadCatalog={() => {}} onInstalled={() => {}} />)
    expect(screen.queryByText(GIT_EMPTY), 'the git claim must not be made yet').toBeNull()
    expect(screen.queryByText(LOCAL_EMPTY), 'nor its local sibling').toBeNull()
    // …and the panel is not simply blank: a loading affordance is what replaces the claim, and
    // it is announced, so a screen-reader user is told "working" rather than told "none".
    const busy = screen.getByRole('status')
    expect(busy.getAttribute('aria-busy')).toBe('true')
    expect(busy.textContent).toContain('Loading app sources')
  })

  it('DOES say them once the catalog has answered and the answer is empty', async () => {
    // The vacuity floor for the assertion above. Without this, deleting the copy outright
    // would satisfy the in-flight test and read as a fix.
    render(<SourcesPanel catalog={ANSWERED_EMPTY} reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(GIT_EMPTY)).toBeTruthy())
    expect(screen.getByText(LOCAL_EMPTY)).toBeTruthy()
    expect(screen.queryByRole('status'), 'and it is no longer loading').toBeNull()
  })

  it('🔴 reports a FAILED catalog read as a failure, with a retry — not as "none configured"', async () => {
    const reload = vi.fn()
    render(<SourcesPanel catalog={undefined} catalogError={new Error('boom')}
      reloadCatalog={reload} onInstalled={() => {}} />)
    // `role="alert"`, because a load failure is unrequested bad news — the distinction
    // `LoadError` draws against `EmptyState`, which deliberately has no live region.
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent).toContain("Couldn't load your app sources")
    expect(screen.queryByText(GIT_EMPTY), 'a 500 is not an empty configuration').toBeNull()
    expect(screen.queryByText(LOCAL_EMPTY)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    expect(reload, 'and the failure is recoverable from the panel itself').toHaveBeenCalled()
  })

  it('still lists real sources, so the guard did not swallow the answered case', async () => {
    const url = 'https://github.com/acme/cool-app.git'
    render(<SourcesPanel catalog={{ ...ANSWERED_EMPTY, gitSources: [url], networkSources: ['github.com'] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(url)).toBeTruthy())
    expect(screen.queryByText(GIT_EMPTY)).toBeNull()
    // The egress disclosure is the reason this panel's honesty matters — assert it arrives
    // WITH the rows rather than a beat after them.
    expect(screen.getByTestId('store-egress-disclosure').textContent).toContain('github.com')
  })
})
