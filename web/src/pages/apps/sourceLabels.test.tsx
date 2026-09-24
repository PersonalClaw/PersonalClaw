import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { SourcesPanel } from './AppsSection'

// ── A shipped default source has to LOOK like one, and the removable one has to be removable ──
//
// ET-4 ships the curated registry as a *seeded* default: a real row in `app-sources.json`, so the
// user can delete it for good. The bundled apps repo is the other kind — folded into every backend
// read, so its DELETE is a no-op by construction. Two kinds of default, one list, and the only
// place the difference is visible is here.
//
// Driven, not read off the source:
//  · both defaults carry a "Default" label, so a user can tell a shipped source from one they typed;
//  · the SEEDED default keeps its remove control and calls the API with its own URL;
//  · the BUNDLED default has NO remove control — a button whose backend silently does nothing is
//    worse than no button (the pre-ET-4 behaviour: click Remove, watch the row stay);
//  · a user-added source is unlabelled and removable, so the "Default" label is not painted on
//    every row (a label everything carries is a label that says nothing).

const removeAppSource = vi.fn((_url: string) => Promise.resolve({}))

vi.mock('../../lib/api', () => ({
  api: {
    removeAppSource: (url: string) => removeAppSource(url),
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
    reset: () => {},
    blocked: null,
    busy: false,
    error: null,
    fixPrompt: null,
  }),
  guardedFromApp: (r: unknown) => r,
  isBlockingResult: () => false,
  terminalRefusalReason: () => null,
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

const BUNDLED = 'https://github.com/PersonalClaw/PersonalClawApps.git'
const REGISTRY = 'https://github.com/PersonalClaw/registry.git'
const USER_ADDED = 'https://github.com/acme/cool-app.git'

const catalog = {
  bundled: [],
  gitSources: [BUNDLED, REGISTRY, USER_ADDED],
  defaultGitSources: [BUNDLED, REGISTRY],
  builtinGitSources: [BUNDLED],
  localSources: [],
  firstPartySources: [],
  localApps: [],
  remoteApps: [],
  gitApps: [],
}

/** The row element for a source URL — the labels and controls scoped to that source. */
function rowFor(url: string): HTMLElement {
  const cell = screen.getByText(url)
  const row = cell.parentElement
  if (!row) throw new Error(`no row for ${url}`)
  return row
}

describe('the Store source list labels shipped defaults', () => {
  beforeEach(() => removeAppSource.mockClear())

  it('labels both kinds of shipped default and nothing else', async () => {
    render(<SourcesPanel catalog={catalog} reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(REGISTRY)).toBeTruthy())
    expect(rowFor(BUNDLED).textContent).toContain('Default')
    expect(rowFor(REGISTRY).textContent).toContain('Default')
    expect(rowFor(USER_ADDED).textContent).not.toContain('Default')
  })

  it('keeps the seeded default removable and removes it by its own URL', async () => {
    render(<SourcesPanel catalog={catalog} reloadCatalog={() => {}} onInstalled={() => {}} />)
    const row = await waitFor(() => rowFor(REGISTRY))
    const remove = row.querySelector('[aria-label="Remove source"]') as HTMLElement | null
    expect(remove).toBeTruthy()
    fireEvent.click(remove!)
    await waitFor(() => expect(removeAppSource).toHaveBeenCalledWith(REGISTRY))
  })

  it('offers no remove control on the bundled default, whose DELETE is a no-op', async () => {
    render(<SourcesPanel catalog={catalog} reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(BUNDLED)).toBeTruthy())
    expect(rowFor(BUNDLED).querySelector('[aria-label="Remove source"]')).toBeNull()
    // …and the control still exists for the rows that CAN be removed, so this is not a
    // vacuous pass from the selector matching nothing anywhere.
    expect(rowFor(USER_ADDED).querySelector('[aria-label="Remove source"]')).toBeTruthy()
  })

  it('treats a backend with no labels as "nothing is a default"', async () => {
    // An older gateway (or a failed read) sends no label arrays. Falling back to "everything
    // is a default" would hide every remove control at once.
    render(<SourcesPanel catalog={{ ...catalog, defaultGitSources: undefined, builtinGitSources: undefined }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(REGISTRY)).toBeTruthy())
    expect(rowFor(REGISTRY).textContent).not.toContain('Default')
    expect(rowFor(BUNDLED).querySelector('[aria-label="Remove source"]')).toBeTruthy()
  })
})

// ── #408: the source that failed has to be NAMED, on its own row ──
//
// One unreachable source used to make the Store spin for ~2 minutes with no error and
// nothing identifying the culprit, so the natural read was "the Store is broken" rather
// than "remove that one". The backend now reports `unavailableSources`; this is the half
// that makes it visible. Driven per row, because a banner that says "1 source unavailable"
// without saying WHICH is the same dead end in a shorter sentence.
describe('the Store source list names a source it could not read', () => {
  it('badges only the unreachable source, and says it will be retried', async () => {
    render(<SourcesPanel catalog={{ ...catalog, unavailableSources: [{ source: USER_ADDED, reason: 'unreachable' }] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(USER_ADDED)).toBeTruthy())
    const badge = rowFor(USER_ADDED).querySelector('[data-testid="store-source-unavailable"]')
    expect(badge).toBeTruthy()
    expect(badge!.textContent).toContain('Unavailable')
    // A backoff, not a death sentence — the tooltip must not read as "this source is dead".
    expect(badge!.getAttribute('title')).toContain('retried')
    // …and the badge is not painted on every row.
    expect(rowFor(BUNDLED).querySelector('[data-testid="store-source-unavailable"]')).toBeNull()
  })

  it('distinguishes a source cut off by the scan budget from one that failed', async () => {
    render(<SourcesPanel catalog={{ ...catalog, unavailableSources: [{ source: REGISTRY, reason: 'budget' }] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(REGISTRY)).toBeTruthy())
    const badge = rowFor(REGISTRY).querySelector('[data-testid="store-source-unavailable"]')
    expect(badge!.textContent).toContain('Skipped')
    expect(badge!.getAttribute('title')).toContain('ran out of time')
  })

  it('badges nothing when every source answered', async () => {
    render(<SourcesPanel catalog={catalog} reloadCatalog={() => {}} onInstalled={() => {}} />)
    await waitFor(() => expect(screen.getByText(USER_ADDED)).toBeTruthy())
    expect(screen.queryByTestId('store-source-unavailable')).toBeNull()
  })
})
