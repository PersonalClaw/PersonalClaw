import { describe, it, expect } from 'vitest'
import { provenance } from './provenance'
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
