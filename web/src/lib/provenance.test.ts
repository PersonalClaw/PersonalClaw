import { describe, it, expect } from 'vitest'
import { provenance, registryListing } from './provenance'
import { dayStamp } from './epoch'
import { catalogApps } from './appCatalog'
import type { AppCatalogEntry } from './api'

// ── The provenance vocabulary, and its one refusal ────────────────────────────────────────────────
//
// Issues 2528 + 2514 are one fact with several representations, and the surfaces disagreed. This pins
// the SHARED reading. The `null` return is the load-bearing part: an origin the backend could not
// resolve renders NOTHING. Both fallbacks are defects — `built-in` was 2514 (claiming shipped-with-
// the-product for an app I installed), and `local` would be the same mistake pointed the other way.

describe('provenance() reads a sourceKind into words', () => {
  it('says built-in for the two shipped kinds', () => {
    expect(provenance({ sourceKind: 'native' })?.label).toBe('built-in')
    expect(provenance({ sourceKind: 'bundled' })?.label).toBe('built-in')
  })

  it('tells a first-party dir, a user dir and a remote apart', () => {
    expect(provenance({ sourceKind: 'first-party' })?.label).toBe('first-party')
    expect(provenance({ sourceKind: 'local' })?.label).toBe('local')
    expect(provenance({ sourceKind: 'git' })?.label).toBe('git')
  })

  it('the locked platform provider is its own word, not "built-in"', () => {
    // Otherwise an uninstallable core capability and an ordinary bundled app read alike.
    expect(provenance({ locked: true })?.label).toBe('platform')
    expect(provenance({ sourceKind: 'local', locked: true })?.label).toBe('platform')
  })

  it('returns null — not a guess — for an origin it does not know', () => {
    expect(provenance({})).toBeNull()
    expect(provenance({ sourceKind: '' })).toBeNull()
    expect(provenance({ sourceKind: null })).toBeNull()
    expect(provenance({ sourceKind: 'something-new' })).toBeNull()
  })

  it('every label carries a title that explains what the word means', () => {
    for (const kind of ['native', 'bundled', 'first-party', 'local', 'git']) {
      const p = provenance({ sourceKind: kind })
      expect(p, kind).not.toBeNull()
      expect(p!.title.length, kind).toBeGreaterThan(20)
    }
    expect(provenance({ locked: true })!.title.length).toBeGreaterThan(20)
  })

  it('a remote and a local copy of one name are never given the same word', () => {
    // The rendering half of the 2528 fix: two cards can now be told apart on screen.
    expect(provenance({ sourceKind: 'git' })!.label)
      .not.toBe(provenance({ sourceKind: 'local' })!.label)
  })
})

// ── The one catalog merge ─────────────────────────────────────────────────────────────────────────
//
// Three consumers used to concatenate the payload's four app lists in three DIFFERENT orders, so one
// payload gave three answers to "which copy of this app am I looking at?". The backend now resolves
// collisions before serialising, and this is the single flattening every consumer shares.

const app = (name: string, over: Partial<AppCatalogEntry> = {}): AppCatalogEntry => ({
  name, displayName: name, description: '', version: '1.0.0', icon: '', author: '',
  source: `/srv/${name}`, sourceKind: 'local', isProvider: false, providerType: '', tags: [],
  ...over,
})

describe('catalogApps() flattens the four lists once, in the backend order', () => {
  it('keeps every app across all four lists', () => {
    const names = catalogApps({
      bundled: [app('a')], localApps: [app('b')], remoteApps: [app('c')], gitApps: [app('d')],
    }).map((e) => e.name)
    expect(names).toEqual(['a', 'b', 'c', 'd'])
  })

  it('tolerates a null catalog and missing lists', () => {
    expect(catalogApps(null)).toEqual([])
    expect(catalogApps(undefined)).toEqual([])
    expect(catalogApps({ bundled: [app('a')] }).map((e) => e.name)).toEqual(['a'])
  })

  it('if a name ever reaches two lists again, every consumer at least agrees which one wins', () => {
    // A belt-and-braces floor, not the fix: the payload should carry each name once. What this
    // pins is that the floor is DETERMINISTIC and follows the backend precedence order, so a
    // regression upstream degrades to "one answer" rather than to "three answers".
    const merged = catalogApps({
      localApps: [app('deep-research', { description: 'on disk', sourceKind: 'local' })],
      gitApps: [app('deep-research', { description: 'remote', sourceKind: 'git' })],
    })
    expect(merged).toHaveLength(1)
    expect(merged[0].description).toBe('on disk')
    expect(merged[0].sourceKind).toBe('local')
  })
})

// ── ET-5: a registry listing's own claims, and the register they are allowed to use ───────────────
//
// The atom's declared RISK is TRUST-WASHING, not a missing field. Three fields exist and are
// populated upstream (4/4 live listings); the failure mode this pins is the copy — a month-old
// third-party `last_scan_verdict: "clean"` rendered as reassurance reads as *PersonalClaw checked
// this for you*, which it is not on three counts (whose check, of what, and what actually gates
// the install). So these assertions are about WORDS, deliberately, and they are the acceptance
// criteria for the done-when clause "reads as community-listed not endorsed".
//
// The real payload, so the fixture and the wire agree on more than this file's opinion.
const LIVE = { maintainer: 'keyurgolani', lastValidated: '2026-09-07T12:19:15Z', lastScanVerdict: 'clean' }
// Non-null by construction: every case below publishes at least one of the three, so a `null`
// here is itself the failure. The "published nothing" case calls `registryListing` directly.
const listing = (over: Partial<typeof LIVE> = {}) =>
  registryListing({ ...LIVE, ...over, day: dayStamp })!

describe('a registry listing describes itself without borrowing our voice', () => {
  it('leads with the non-endorsement, before any reassurance', () => {
    const l = listing()!
    // Job 1. A reader who stops after four words must have read the disclaimer. The headline is
    // rendered FIRST, and it contains no verdict at all — so this is not merely "the words appear
    // somewhere", it is "the words appear before the good news".
    expect(l.headline).toMatch(/community-listed/i)
    expect(l.headline).toMatch(/not endorsed/i)
    expect(l.headline).not.toMatch(/clean|verified|safe|scanned/i)
  })

  it('attributes the verdict to the registry, never to PersonalClaw', () => {
    // Job 2. "registry check: clean" is a report of someone else's finding. A bare "Verified" or
    // "Scanned" — with no owner named — is the trust-wash, because the only voice a reader can
    // assign an unattributed claim to is the product's.
    const l = listing()!
    expect(l.detail).toMatch(/registry check: clean/)
    expect(l.detail).not.toMatch(/\bverified\b/i)
    // …and it never says PersonalClaw did anything except the thing PersonalClaw does do.
    expect(l.detail.replace(/rescanned when you install/, '')).not.toMatch(/personalclaw/i)
  })

  it('points at the gate that actually runs, so a stale verdict is harmless', () => {
    // Job 3. This is what makes showing a month-old verdict safe at all: the sentence tells the
    // reader the check that matters has not happened yet. Unconditional — present even when the
    // listing published no verdict to be stale about.
    expect(listing().detail).toMatch(/rescanned when you install/)
    expect(listing({ lastScanVerdict: '', lastValidated: '' }).detail).toMatch(/rescanned when you install/)
  })

  it('carries all three published facts', () => {
    const l = listing()!
    expect(l.detail).toMatch(/by keyurgolani/)
    expect(l.detail).toMatch(/clean/)
    // The DAY, not the instant: `12:19` would imply a precision "when was this listing last
    // looked at" does not have. Locale-resolved, so the assertion is on the year, not a format.
    expect(l.detail).toMatch(/2026/)
    expect(l.detail).not.toMatch(/12:19/)
  })

  it('a non-clean verdict does not get the clean register', () => {
    // A grey "flagged" sitting exactly where a grey "clean" sat is the same defect pointing the
    // other way — it launders a finding into a neutral line. `clean` is what the caller styles on.
    expect(listing().clean).toBe(true)
    expect(listing({ lastScanVerdict: 'flagged' }).clean).toBe(false)
    expect(listing({ lastScanVerdict: 'flagged' }).detail).toMatch(/registry check: flagged/)
  })

  it('treats an ABSENT verdict as un-vouched-for, not as clean', () => {
    // 🔴 The defaulting direction is the whole decision. A listing that published no verdict has
    // had nothing said about it; styling that as passing would make a silence into a claim.
    expect(listing({ lastScanVerdict: '' }).clean).toBe(false)
  })

  it('omits a field the listing never published, rather than inventing one', () => {
    // "unknown" on a card reads as a finding ABOUT the app. Absent reads as absent.
    const l = listing({ maintainer: '' })!
    expect(l.detail).not.toMatch(/unknown|n\/a|—/i)
    expect(l.detail).not.toMatch(/\bby\b/)
    expect(l.detail).toMatch(/registry check: clean/)
  })

  it('dates a listing that was checked but got no verdict, without implying it passed', () => {
    const l = listing({ lastScanVerdict: '' })!
    expect(l.detail).toMatch(/last checked/)
    // "last checked" claims a look happened. It must not claim an outcome.
    expect(l.detail).not.toMatch(/clean|passed|ok\b/i)
  })

  it('says NOTHING for a card with no listing behind it — the local/first-party clause', () => {
    // 🔑 This `null` is what keeps dir-scanned cards unchanged WITHOUT the caller testing what
    // kind of card it holds: only the registry path populates these fields, so a bundled or
    // local bundle arrives here empty and gets nothing back.
    expect(registryListing({ day: dayStamp })).toBeNull()
    expect(registryListing({ maintainer: '', lastValidated: '', lastScanVerdict: '', day: dayStamp })).toBeNull()
    // Whitespace is not a publication either.
    expect(registryListing({ maintainer: '  ', lastScanVerdict: ' ', day: dayStamp })).toBeNull()
  })

  it('survives an unreadable timestamp without printing a broken date', () => {
    // `dayStamp` renders '' for junk (the `epoch` module's contract), so the date clause is
    // dropped rather than becoming "Invalid Date" on a Store card.
    const l = listing({ lastValidated: 'not-a-date' })!
    expect(l.detail).not.toMatch(/invalid/i)
    expect(l.detail).toMatch(/registry check: clean/)
    expect(l.detail).toMatch(/rescanned when you install/)
  })

  it('spells out all three reasons the verdict is not a guarantee, for the hover', () => {
    const t = listing().title
    expect(t).toMatch(/does not review, endorse or vouch/i)   // not our assessment
    expect(t).toMatch(/the LISTING/)                          // not of the bytes
    expect(t).toMatch(/own scan .* when you install/i)         // not the gate
  })
})
