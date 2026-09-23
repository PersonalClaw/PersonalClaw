// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { AppCatalogEntry, AppCatalog } from '../../lib/api'

// ── ET-5: a registry listing's provenance, ON THE CARD ────────────────────────────────────────────
//
// `storeCardProvenance.test.tsx` next door pins the ORIGIN chip — "git", these bytes came over the
// network. That is a different fact from the one a community listing raises, which is *who listed
// this, and has anyone looked at it?* The index has published `maintainer` / `last_validated` /
// `last_scan_verdict` since ET-3 and the catalog dropped all three, so the answer appeared nowhere:
// measured against the live registry before this landed, 4/4 listings publish all three fields and
// 0/4 reached the frontend.
//
// 🔑 A DRIVEN RENDER, for the same reason as its sibling: `lib/provenance` is pure and pinned
// separately, so only rendering the real grid proves the CARD reaches for it. And the assertions are
// on text a user can read plus DOCUMENT ORDER — "the card says community-listed before it says
// clean" is a claim about the rendered tree, not about a prop.

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

import { StoreView } from './AppsSection'

/** The live registry's own payload shape (`PersonalClaw/registry` @ c0b35b6c8), so the fixture and
 *  the wire agree on more than this file's opinion. */
function listed(over: Partial<AppCatalogEntry> = {}): AppCatalogEntry {
  return {
    name: 'channel-null', displayName: 'Channel Null', description: 'A channel that goes nowhere.',
    version: '0.1.0', icon: '', author: '',
    source: 'https://github.com/PersonalClaw/registry.git', sourceKind: 'git',
    pointer: 'https://github.com/PersonalClaw/channel-null',
    isProvider: false, providerType: '', tags: [],
    permissions: {}, crons: [], consentKnown: false,
    maintainer: 'keyurgolani',
    lastValidated: '2026-09-07T12:19:15Z',
    lastScanVerdict: 'clean',
    ...over,
  }
}

/** A DIR-SCANNED bundle: no index listing behind it, so none of the three fields exists. */
function scanned(over: Partial<AppCatalogEntry> = {}): AppCatalogEntry {
  return {
    name: 'deep-research', displayName: 'Deep Research', description: 'Ask one question and walk away…',
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

function grid(e: AppCatalogEntry, catalog: AppCatalog, installed = false) {
  return render(
    <StoreView catalog={catalog} result={[{ ...e, installed, enabled: false, hasUI: false }]}
      totalKnown={1} installedCount={installed ? 1 : 0} onInstalled={() => {}} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )
}

const asListing = (e: AppCatalogEntry, installed = false) =>
  grid(e, { ...EMPTY, gitApps: [e] }, installed)

describe('a registry-listed card shows who listed it and what the index said', () => {
  it('names the maintainer, the check and its date', () => {
    asListing(listed())
    const text = screen.getByTestId('store-card-listing').textContent ?? ''
    expect(text).toMatch(/by keyurgolani/)
    expect(text).toMatch(/registry check: clean/)
    expect(text).toMatch(/2026/)
  })

  it('reads as community-listed, NOT endorsed — and says so FIRST', () => {
    // The done-when clause, and the atom's declared risk. Order is the control: a card that leads
    // with "clean" has trust-washed a third party's month-old check into our own verdict, even
    // though every field renders. So this asserts the tree, not just the strings.
    asListing(listed())
    const block = screen.getByTestId('store-card-listing')
    const text = block.textContent ?? ''
    expect(text).toMatch(/Community-listed, not endorsed/)

    const headline = screen.getByTestId('store-card-listing-headline')
    const detail = screen.getByTestId('store-card-listing-detail')
    // `compareDocumentPosition` → 4 means "headline precedes detail" (FOLLOWING).
    expect(headline.compareDocumentPosition(detail) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // …and the reassurance is nowhere in the headline itself.
    expect(headline.textContent).not.toMatch(/clean/i)
  })

  it('points at the install-time scan, so the stale verdict is not the last word', () => {
    // The gate still runs at install (`app_manager.install`). A card that showed a verdict without
    // saying that would be presenting the registry's check AS the gate.
    asListing(listed())
    expect(screen.getByTestId('store-card-listing').textContent)
      .toMatch(/rescanned when you install/)
  })

  it('explains, on hover, all three reasons the verdict is not a guarantee', () => {
    asListing(listed())
    const title = screen.getByTestId('store-card-listing').getAttribute('title') ?? ''
    expect(title).toMatch(/does not review, endorse or vouch/i)
    expect(title).toMatch(/runs its own scan/i)
  })

  it('does not render a non-clean verdict in the clean register', () => {
    // A grey "flagged" exactly where a grey "clean" sat launders a finding into a neutral line.
    asListing(listed())
    const cleanInk = screen.getByTestId('store-card-listing-detail').className
    asListing(listed({ lastScanVerdict: 'flagged' }))
    const flagged = screen.getAllByTestId('store-card-listing-detail').pop()!
    expect(flagged.textContent).toMatch(/registry check: flagged/)
    expect(flagged.className).not.toBe(cleanInk)
    expect(flagged.className).toMatch(/warn/)
  })

  it('omits a fact the listing never published instead of inventing one', () => {
    asListing(listed({ maintainer: '' }))
    const text = screen.getByTestId('store-card-listing').textContent ?? ''
    expect(text).not.toMatch(/unknown|n\/a/i)
    expect(text).toMatch(/registry check: clean/)
  })
})

describe('local and first-party cards are unchanged', () => {
  it('a dir-scanned LOCAL bundle grows no provenance line', () => {
    // 🔑 A done-when clause in its own right. This is the test that fails if a scan path starts
    // populating the three fields, or if the card grows an `if` that guesses at listing-ness.
    grid(scanned(), { ...EMPTY, localApps: [scanned()] })
    expect(screen.queryByTestId('store-card-listing')).toBeNull()
    // Positive control: the card DID render, so the absence above is a measurement and not an
    // empty tree. Its origin chip is untouched by this change.
    expect(screen.getByTestId('store-card-origin').textContent).toBe('local')
  })

  it('a FIRST-PARTY bundle grows no provenance line either', () => {
    const e = scanned({ sourceKind: 'first-party', source: '/ws/apps/deep-research' })
    grid(e, { ...EMPTY, localApps: [e] })
    expect(screen.queryByTestId('store-card-listing')).toBeNull()
    expect(screen.getByTestId('store-card-origin').textContent).toBe('first-party')
  })

  it('a BUNDLED card grows no provenance line either', () => {
    const e = scanned({ sourceKind: 'bundled', source: '/pkg/apps/deep-research' })
    grid(e, { ...EMPTY, bundled: [e] })
    expect(screen.queryByTestId('store-card-listing')).toBeNull()
    expect(screen.getByTestId('store-card-origin').textContent).toBe('built-in')
  })

  it('a registry card with no published provenance also says nothing', () => {
    // Registry-sourced is not by itself a reason to render the line — with nothing published
    // there is nothing to attribute, and a bare disclaimer over no facts is noise.
    asListing(listed({ maintainer: '', lastValidated: '', lastScanVerdict: '' }))
    expect(screen.queryByTestId('store-card-listing')).toBeNull()
    expect(screen.getByTestId('store-card-origin').textContent).toBe('git')
  })
})

describe('once installed, the listing claim steps aside', () => {
  it('an INSTALLED app shows no listing line', () => {
    // The app's real scan verdict is on the record by then; a month-old listing claim beside it
    // would be the weaker of two facts competing for the same glance. Same pre-install-only rule
    // the origin chip already follows.
    asListing(listed(), true)
    expect(screen.queryByTestId('store-card-listing')).toBeNull()
  })
})
