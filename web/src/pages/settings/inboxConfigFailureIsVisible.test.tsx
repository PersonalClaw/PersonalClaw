import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ComponentType } from 'react'

// ── #532 row 19: a failed config read may not render two OFF switches ────────────────────────────
//
// `InboxSettingsPanel` exists TWICE (`pages/inbox/` is the drawer, `pages/settings/` the canonical
// page), and both read `inbox.enabled` + `inbox.engagement_ranking_enabled` from
// `GET /api/config`. Both caught the rejection with
//
//     .catch(() => { setEngagementOn(false); setSourcesOn(false) })
//
// and in this component `null` means *not read yet* while `false` means *read, and the setting is
// off* — that is why the switches are `disabled={sourcesOn === null}`. So a failed config read
// rendered **two live OFF switches with no failure anywhere on screen**: the panel asserting a
// configuration it had never read, on the two flags that decide whether the inbox collects anything
// at all. The settings copy's own comment called that fallback deliberate, which is what kept it
// alive through two passes over this issue.
//
// 🪤 WHY THIS RAIL IS BEHAVIOURAL AND NOT A ROW IN THE SWALLOW BUDGET, which is what #532's last
// instruction asked for. `ui/loadErrorState.test.tsx`'s census counts a fabricated `[]`/`null`/
// `''`/`{…}` and has NO true/false member (`SWALLOW_SHAPE`), so it scores this drawer **0** — a
// fabricated boolean is invisible to it. Its ratchet is exact (`census < budget` reds, "fixing one
// ratchets the number down"), so a budget row of `1` against a census of `0` is `0 < 1` = RED: the
// entry would have broken the suite while protecting nothing. The contract the fix actually
// establishes is the one asserted below.
//
// ARIA has no third value for a switch (`aria-checked` is true/false; `mixed` is not valid on
// `switch`), so "we could not read this" is carried by `disabled` PLUS the failure band — not by the
// checked state. Hence both halves are asserted together, and the resolving control below is what
// keeps that from being satisfied by a panel that renders nothing at all.

const patched: Array<[string, unknown]> = []

const CONFIG_OK = { inbox: { enabled: true, engagement_ranking_enabled: false }, proactive: {} }

function mockApi(personalclawConfig: () => Promise<unknown>) {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as Record<string, unknown>),
        inboxSettings: () => Promise.resolve({ auto_cleanup_enabled: true, retention_days: 90 }),
        personalclawConfig,
        // `{ rules, unreadable }` (api.ts) — NOT a bare array. `TriageRulesCard` renders
        // `data.unreadable.length` unguarded, so a wrong shape here throws inside the settings
        // copy's subtree and unmounts the panel under test before a single assertion runs.
        approvalRules: () => Promise.resolve({ rules: [], unreadable: [] }),
        proactiveStatus: () => Promise.resolve({}),
        restartInbox: () => Promise.resolve({}),
        patchConfig: (path: string, value: unknown) => {
          patched.push([path, value])
          return Promise.resolve({})
        },
      },
    }
  })
}

/** Both copies, each imported through its own path so the mock above applies to both. */
const COPIES: Array<[string, () => Promise<{ InboxSettingsPanel: ComponentType }>]> = [
  ['pages/settings (the canonical page)', () => import('./InboxSettingsPanel')],
  ['pages/inbox (the drawer copy)', () => import('../inbox/InboxSettingsPanel')],
]

beforeEach(() => {
  vi.resetModules()
  patched.length = 0
  sessionStorage.clear()
})

describe.each(COPIES)('%s: a failed config read is reported, never rendered as OFF', (_name, load) => {
  it('leaves both switches unavailable and puts the rejection on screen', async () => {
    mockApi(() => Promise.reject(new Error('config read failed: 503')))
    const { InboxSettingsPanel } = await load()
    render(<InboxSettingsPanel />)

    // The failure is visible, in the server's own words, in a live region.
    const band = await screen.findByText(/Couldn't read your inbox configuration/)
    expect(band.textContent, "the server's own message reaches the user").toContain('config read failed: 503')
    expect(band.closest('[role="alert"]'), 'and it is announced, not just painted').not.toBeNull()

    // And neither switch is presented as a saved setting.
    for (const label of ['Poll message sources', 'Engagement ranking']) {
      const sw = await screen.findByRole('switch', { name: label })
      expect(sw, `${label} must not be operable off an unread config`).toBeDisabled()
      fireEvent.click(sw)
    }
    await waitFor(() => expect(patched, 'a disabled switch writes nothing').toEqual([]))
  })

  it('and when the read succeeds the switches are live (the control that keeps this non-vacuous)', async () => {
    mockApi(() => Promise.resolve(CONFIG_OK))
    const { InboxSettingsPanel } = await load()
    render(<InboxSettingsPanel />)

    const sources = await screen.findByRole('switch', { name: 'Poll message sources' })
    await waitFor(() => expect(sources, 'a resolved config unlocks the switch').toBeEnabled())
    expect(sources, 'and shows what the config actually said').toHaveAttribute('aria-checked', 'true')
    expect(screen.queryByText(/Couldn't read your inbox configuration/), 'no failure to report').toBeNull()

    fireEvent.click(sources)
    await waitFor(() => expect(patched).toContainEqual(['inbox.enabled', false]))

    const ranking = await screen.findByRole('switch', { name: 'Engagement ranking' })
    expect(ranking).toBeEnabled()
    expect(ranking).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(ranking)
    await waitFor(() => expect(patched).toContainEqual(['inbox.engagement_ranking_enabled', true]))
  })
})
