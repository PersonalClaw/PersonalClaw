// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { AppCatalogEntry, AppCatalog } from '../../lib/api'

// ── Provenance is on the card, as TEXT, BEFORE install (issue 2528) ───────────────────────────────
//
// The only pre-install signal that a card came from a local source used to be the source DIVIDER
// heading (a folder basename) and the Sources rail. Nothing on the card itself said where its bytes
// came from — which is why a remote copy standing in for a local bundle of the same name was
// invisible to the user and visible only to whoever read the catalog code.
//
// 🔑 A DRIVEN RENDER on purpose. `lib/provenance` is pure and pinned separately; only rendering the
// real grid proves the card reaches for it. And the assertion is on TEXT the user can read, not on
// a prop being passed — "the card names its origin" is not a claim a data test can make.

vi.mock('../../lib/api', () => ({
  api: {
    installApp: () => Promise.resolve({ ok: true }),
    removeAppSource: () => Promise.resolve({}),
    addAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    addLocalAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    removeLocalAppSource: () => Promise.resolve({}),
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
  terminalRefusalReason: () => '',
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

import { StoreView, SourcesPanel } from './AppsSection'

const LOCAL_DESC = 'Ask one question and walk away…'
const REMOTE_DESC = 'Unattended research campaigns as an agent tool…'

function entry(over: Partial<AppCatalogEntry> = {}): AppCatalogEntry {
  return {
    name: 'deep-research', displayName: 'Deep Research', description: LOCAL_DESC,
    version: '0.1.0', icon: '', author: 'PersonalClaw',
    source: '/srv/apps/deep-research', sourceKind: 'local',
    isProvider: false, providerType: '', tags: [],
    permissions: { network: false }, crons: [],
    ...over,
  }
}

const EMPTY: AppCatalog = {
  bundled: [], gitSources: [], defaultGitSources: [], builtinGitSources: [],
  localSources: ['/srv/apps'], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [],
}

function grid(e: AppCatalogEntry, catalog: AppCatalog = { ...EMPTY, localApps: [e] }) {
  return render(
    <StoreView catalog={catalog} result={[{ ...e, installed: false, enabled: false, hasUI: false }]}
      totalKnown={1} installedCount={0} onInstalled={() => {}} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )
}

describe('a Store card names where its bytes came from, before install', () => {
  it('says "local" for a bundle from a folder on this machine', () => {
    grid(entry())
    expect(screen.getByTestId('store-card-origin').textContent).toBe('local')
    // …and it explains what the word means, since "local" alone is not self-explaining.
    expect(screen.getByTestId('store-card-origin').getAttribute('title'))
      .toMatch(/folder on this machine/i)
  })

  it('says "git" for a bundle fetched from a remote', () => {
    const e = entry({
      description: REMOTE_DESC, sourceKind: 'git',
      source: 'https://github.com/PersonalClaw/PersonalClawApps.git',
      pointer: 'https://github.com/PersonalClaw/PersonalClawApps.git#deep-research',
      permissions: { network: true },
    })
    grid(e, { ...EMPTY, gitApps: [e] })
    expect(screen.getByTestId('store-card-origin').textContent).toBe('git')
    expect(screen.getByTestId('store-card-origin').getAttribute('title'))
      .toMatch(/network/i)
  })

  it('the two copies of one name are TOLD APART on the card, not only in the payload', () => {
    // The whole point of rendering provenance: the local and the remote copy of `deep-research`
    // are visually distinguishable now. Before, both cards read "Deep Research v0.1.0" with a
    // different description and nothing naming the origin.
    grid(entry())
    const local = screen.getByTestId('store-card-origin').textContent
    screen.getByText(new RegExp(LOCAL_DESC.slice(0, 20)))

    const remote = entry({ description: REMOTE_DESC, sourceKind: 'git', source: 'https://github.com/acme/apps.git' })
    grid(remote, { ...EMPTY, gitApps: [remote] })
    const labels = screen.getAllByTestId('store-card-origin').map((n) => n.textContent)
    expect(labels).toContain('git')
    expect(local).toBe('local')
  })

  it('says nothing at all when the origin is unknown — an absent fact is not a claim', () => {
    const e = entry({ sourceKind: '' as AppCatalogEntry['sourceKind'] })
    grid(e)
    expect(screen.queryByTestId('store-card-origin')).toBeNull()
  })
})

describe('the Store discloses which hosts a listing read contacts', () => {
  it('names the hosts when a network source is listed', () => {
    render(<SourcesPanel catalog={{ ...EMPTY, gitSources: ['https://github.com/acme/apps.git'], networkSources: ['github.com'] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    const text = screen.getByTestId('store-egress-disclosure').textContent ?? ''
    expect(text.replace(/\s+/g, ' ')).toMatch(/contacts github\.com/)
  })

  it('says nothing when every source is on this machine — no warning about nothing', () => {
    render(<SourcesPanel catalog={{ ...EMPTY, networkSources: [] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    expect(screen.queryByTestId('store-egress-disclosure')).toBeNull()
  })

  it('points at the off switch for a source with no row to remove', () => {
    const url = 'https://github.com/PersonalClaw/PersonalClawApps.git'
    render(<SourcesPanel catalog={{ ...EMPTY, gitSources: [url], defaultGitSources: [url], builtinGitSources: [url], networkSources: ['github.com'] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    // No remove button — the source is folded into every backend read, so a DELETE is a no-op…
    expect(screen.queryByRole('button', { name: /Remove source/i })).toBeNull()
    // …and instead of leaving that silent, the disclosure says where the switch IS.
    const text = (screen.getByTestId('store-egress-disclosure').textContent ?? '').replace(/\s+/g, ' ')
    expect(text).toMatch(/no remove button; turn it off in Settings → Apps/)
  })

  it('does not mention an off switch when no shipped source is listed', () => {
    // A user-added remote is removable, so a sentence about a missing remove button would be
    // describing a control the row actually has.
    render(<SourcesPanel catalog={{ ...EMPTY, gitSources: ['https://github.com/acme/apps.git'], networkSources: ['github.com'] }}
      reloadCatalog={() => {}} onInstalled={() => {}} />)
    expect(screen.getByRole('button', { name: /Remove source/i })).toBeTruthy()
    expect(screen.getByTestId('store-egress-disclosure').textContent).not.toMatch(/no remove button/)
  })
})
