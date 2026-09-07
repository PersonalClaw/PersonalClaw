import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A successful local-source add left the panel saying "No local sources" (#2627) ────────────────
//
// The add WORKED: the POST persisted, the API listed the source, the Store grid showed its apps.
// Only the Manage Sources panel disagreed — it still read the empty state with the input's
// placeholder restored, so the one surface a user watches to confirm the action reported the
// opposite of what happened. The committed app-bundle harness already flagged it
// (`listedInPanel: false`, both bundles).
//
// 🔑 IT IS NOT A MISSING REFETCH. `addLocalSource` already called `reloadCatalog()`, and the
// panel's list already reads live off that key. The problem is what the shared cache does in
// between: `invalidateKeys` deliberately KEEPS the stale value so an open panel doesn't blank, so
// the panel keeps painting the pre-write array until the re-read lands. And the re-read is
// `GET /api/apps/catalog` — the most expensive request in the app (registry scans plus a shallow
// clone per git source). Slow, and the panel contradicts itself for seconds; failed, and it holds
// the pre-write value indefinitely with nothing on screen saying so.
//
// 🪤 SO THE TEST DRIVES THE SLOW PATH ON PURPOSE. `appCatalog` resolves once for the initial
// mount and then never again, which is exactly the state the old code was indistinguishable from
// success in. A test whose second catalog read resolves promptly passes with or without the fix.

const PANEL_TITLE = 'Manage Sources'
const NEW_PATH = '/srv/harness-sources/spec-builder'

const CATALOG = {
  bundled: [], gitSources: [], localSources: [],
  localApps: [], remoteApps: [], gitApps: [],
}

/** Resolves the FIRST catalog read and hangs every one after it. */
function slowCatalog() {
  let calls = 0
  return () => {
    calls += 1
    return calls === 1 ? Promise.resolve(CATALOG) : new Promise(() => {})
  }
}

function mockApi(over: Record<string, unknown> = {}) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      apps: () => Promise.resolve([]),
      appCatalog: slowCatalog(),
      // The endpoint already returns the authoritative post-write list; the panel used to
      // discard it and wait for the catalog instead.
      addLocalAppSource: (p: string) => Promise.resolve({ ok: true, sources: [p] }),
      ...over,
    },
  }))
}

async function openSourcesPanel() {
  const { AppsSection } = await import('./AppsSection')
  render(<AppsSection query={{ view: 'store' }} setQuery={() => {}} navigate={() => {}} />)
  await waitFor(() => expect(screen.getByRole('navigation', { name: 'Categories and sources' })).toBeTruthy())
  // Several controls open this panel (the header action, the rail's "Add source", the empty
  // state's link) — take the header action, the same control the committed harness prefers.
  const openers = screen.getAllByRole('button', { name: new RegExp(PANEL_TITLE, 'i') })
  await userEvent.click(openers[0])
  await waitFor(() => expect(screen.getByPlaceholderText(/a directory of app subdirs/)).toBeTruthy())
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('the Manage Sources panel re-reads after a successful local-source add', () => {
  it('lists the path it just accepted, without waiting on the catalog re-read', async () => {
    mockApi()
    await openSourcesPanel()
    expect(screen.getByText(/No local sources/), 'the precondition').toBeTruthy()

    const input = screen.getByPlaceholderText(/a directory of app subdirs/)
    await userEvent.type(input, NEW_PATH)
    // The Local sources section's own Add button — the git section has one too and sits
    // earlier in the DOM, so anchor on the input's row (same reasoning the harness uses).
    const addButtons = screen.getAllByRole('button', { name: /^Add$/ })
    await userEvent.click(addButtons[addButtons.length - 1])

    await waitFor(() => expect(screen.getByText(NEW_PATH), 'the added path is listed').toBeTruthy())
    expect(screen.queryByText(/No local sources/), 'and the empty state is gone').toBeNull()
  })

  it('a FAILED add still reports the failure and does not fabricate a row', async () => {
    // The vacuity half. Painting the write's answer must be conditional on the write, or the
    // panel would cheerfully list a path the server refused.
    mockApi({ addLocalAppSource: () => Promise.reject(new Error('not a directory')) })
    await openSourcesPanel()
    const input = screen.getByPlaceholderText(/a directory of app subdirs/)
    await userEvent.type(input, NEW_PATH)
    const addButtons = screen.getAllByRole('button', { name: /^Add$/ })
    await userEvent.click(addButtons[addButtons.length - 1])

    await waitFor(() => expect(screen.getByText(/not a directory/)).toBeTruthy())
    expect(screen.queryByText(NEW_PATH), 'no row for a refused path').toBeNull()
    expect(screen.getByText(/No local sources/), 'still empty, honestly').toBeTruthy()
  })
})
