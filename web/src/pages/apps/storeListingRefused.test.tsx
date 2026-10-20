// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import type { AppCatalogEntry, AppInstallResult } from '../../lib/api'

// ── A registry listing the gateway will not fetch says so, and offers no Install ──────────────
//
// A listing names where its app downloads from, and that is someone else's data. When it names
// this computer, a private network or the metadata service (`apps/catalog.py`), the card is still
// shown, carrying the server's sentence, so a registry's apps do not silently vanish. What must
// not survive is the Install: not on the card, not in its menu, not in the detail panel.
//
// And a listing the Store CAN install sends back which registry listed it (`listedBy`), so the
// gateway holds that fetch to the listing rules even when it has not read the index itself yet.

const previewApp = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    previewApp: (...a: unknown[]) => previewApp(...a),
    installApp: () => Promise.reject(new Error('nothing is confirmed in this file')),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

// Imported after the mocks so the components bind them.
import { StoreView, StoreDetailPanel } from './AppsSection'

const REGISTRY = 'https://github.com/acme/registry.git'
const REFUSED =
  'Not installable: this listing downloads from this computer (127.0.0.1). A registry listing '
  + 'can only download from a public https:// address. If 127.0.0.1 is yours, add it to Allowed '
  + 'hosts in Settings → Security → Network egress.'

const listing = (over: Partial<AppCatalogEntry>): AppCatalogEntry => ({
  name: 'x', displayName: 'X', description: 'an app', version: '1.0.0', icon: '', author: 'acme',
  source: REGISTRY, sourceKind: 'git', isProvider: false, providerType: '', tags: [],
  consentKnown: false, ...over,
})

const evil = listing({
  name: 'evil', displayName: 'Evil Tool', pointer: 'https://127.0.0.1/evil.git', listedBy: REGISTRY,
  refused: REFUSED,
})
const fine = listing({
  name: 'fine', displayName: 'Fine Tool', pointer: 'https://apps.example/fine.git', listedBy: REGISTRY,
})

const catalog = {
  bundled: [], gitSources: [REGISTRY], defaultGitSources: [], builtinGitSources: [],
  localSources: [], firstPartySources: [], localApps: [], remoteApps: [evil, fine], gitApps: [],
}

const asItem = (e: AppCatalogEntry) => ({ ...e, installed: false, enabled: false, hasUI: false })

function grid() {
  return render(
    <StoreView catalog={catalog} result={[asItem(evil), asItem(fine)]}
      totalKnown={2} installedCount={0} onInstalled={() => {}} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )
}

/** The card whose name link reads `name`. */
function card(name: string): HTMLElement {
  const link = screen.getByRole('button', { name: `${name} — details` })
  const el = link.closest('.group')
  expect(el, `no card for ${name}`).not.toBeNull()
  return el as HTMLElement
}

beforeEach(() => {
  previewApp.mockReset()
  previewApp.mockReturnValue(new Promise<AppInstallResult>(() => {}))  // the review never lands
})

describe('a refused registry listing', () => {
  it('shows the server’s sentence on its card and offers no Install there', () => {
    grid()
    const refused = card('Evil Tool')
    expect(within(refused).getByRole('note').textContent).toBe(REFUSED)
    expect(within(refused).queryByRole('button', { name: /install/i })).toBeNull()
    // The installable neighbour still offers it, so the grid is not simply missing the control.
    expect(within(card('Fine Tool')).getByRole('button', { name: /install/i })).toBeTruthy()
  })

  it('has no Install in its right-click menu', async () => {
    grid()
    fireEvent.contextMenu(card('Evil Tool'))
    const menu = await screen.findByRole('menu')
    expect(within(menu).getByRole('menuitem', { name: /details/i })).toBeTruthy()
    expect(within(menu).queryByRole('menuitem', { name: /install/i })).toBeNull()
  })

  it('has no Install in its detail panel either, only the reason', () => {
    render(<StoreDetailPanel item={asItem(evil)} onInstalled={() => {}} />)
    expect(screen.getByRole('note').textContent).toBe(REFUSED)
    expect(screen.queryByRole('button', { name: /install/i })).toBeNull()
    expect(screen.queryByText(/choose Install/)).toBeNull()
  })
})

describe('an installable registry listing', () => {
  it('tells the review which registry listed it', async () => {
    grid()
    fireEvent.click(within(card('Fine Tool')).getByRole('button', { name: /install/i }))
    await waitFor(() => expect(previewApp).toHaveBeenCalledTimes(1))
    expect(previewApp).toHaveBeenCalledWith('https://apps.example/fine.git', undefined, REGISTRY)
  })

  it('does the same from its detail panel', async () => {
    render(<StoreDetailPanel item={asItem(fine)} onInstalled={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /install/i }))
    await waitFor(() => expect(previewApp).toHaveBeenCalledTimes(1))
    expect(previewApp).toHaveBeenCalledWith('https://apps.example/fine.git', undefined, REGISTRY)
  })
})
