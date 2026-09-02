import { describe, it, expect, vi } from 'vitest'
import { sourceGroup, localSourceLabel, type StoreItem } from './AppsSection'

// ── WT-10: a local source is a FOLDER NAME in the Store, never an absolute path ──
//
// The Store groups apps by where they came from. For a git source the heading is the
// URL; for a local source it used to be the raw `key` — the absolute directory path.
// Run the app from a git worktree and that path was the worktree checkout, so the
// Store printed "/Users/…/worktrees/ux-inspect/src/…" (uppercased by the divider) as a
// section title: a dev/console artifact leaking into product chrome (DESIGN-TENETS
// tenet 1, companion-not-console). The heading is now the folder name; the full path
// stays the grouping KEY, so the source filter/URL state (`ssrc=local:<path>`) is
// unchanged. Both the divider heading AND the Sources rail/filter read this one label,
// so pinning `sourceGroup` covers every surface it feeds.

// AppsSection pulls the whole apps module graph at import; mock the same runtime deps
// the sibling `sourceLabels.test.tsx` does so importing the pure helpers is side-effect
// free (these mocks are never called here — `sourceGroup`/`localSourceLabel` are pure).
vi.mock('../../lib/api', () => ({ api: {} }))
vi.mock('../../lib/useGuardedInstall', () => ({
  useGuardedInstall: () => ({}),
  guardedFromApp: (r: unknown) => r,
  isBlockingResult: () => false,
  terminalRefusalReason: () => null,
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

const base: StoreItem = {
  name: 'ledger', displayName: 'Ledger', description: '', version: '1.0.0',
  icon: '', author: '', source: '', sourceKind: 'local',
  isProvider: false, providerType: '', tags: [],
  installed: true, enabled: true, hasUI: true,
}
const mk = (over: Partial<StoreItem>): StoreItem => ({ ...base, ...over })

describe('localSourceLabel turns a filesystem path into a human folder name', () => {
  it('uses the last path segment, not the whole absolute path', () => {
    expect(localSourceLabel('/Users/me/projects/cool-app')).toBe('cool-app')
    expect(localSourceLabel('/srv/apps')).toBe('apps')
  })
  it('ignores a trailing slash', () => {
    expect(localSourceLabel('/Users/me/projects/cool-app/')).toBe('cool-app')
  })
  it('handles Windows separators', () => {
    expect(localSourceLabel('C:\\Users\\me\\apps')).toBe('apps')
  })
  it('never returns a value containing a path separator for a real folder', () => {
    const leaky = '/Users/golani/PersonalProjects/PersonalClaw/.worktrees/ux-inspect/src/personalclaw/apps'
    expect(localSourceLabel(leaky)).toBe('apps')
    expect(localSourceLabel(leaky)).not.toContain('/')
  })
  it('falls back to the input when there is no segment to show', () => {
    expect(localSourceLabel('/')).toBe('/')
  })
})

describe('sourceGroup labels a local source by folder name, keyed by full path', () => {
  it('a worktree-checkout source does not leak the absolute path into the heading (WT-10)', () => {
    const worktree = '/Users/golani/PersonalProjects/PersonalClaw/.worktrees/ux-inspect/src/personalclaw/apps'
    const g = sourceGroup(mk({ source: `${worktree}/ledger`, sourceKind: 'local', origin: 'local' }), [])
    // Heading is human, and carries no filesystem path.
    expect(g.label).toBe('apps')
    expect(g.label).not.toContain('/')
    expect(g.label.startsWith('/Users')).toBe(false)
    // …but the grouping key still keys on the folded path, so the source filter is stable.
    expect(g.key).toBe(`local:${worktree}`)
  })

  it('folds an app up to its registered local source and labels it by that folder', () => {
    // Registered source `/srv/apps`; the installed app records its own subdir.
    const g = sourceGroup(mk({ source: '/srv/apps/ledger', sourceKind: 'local', origin: 'local' }), ['/srv/apps'])
    expect(g.key).toBe('local:/srv/apps')
    expect(g.label).toBe('apps')
  })

  // ── Regression: the other branches are untouched — only local paths changed. ──
  it('still shows a git URL verbatim as its own heading', () => {
    const url = 'https://github.com/acme/cool-app.git'
    const g = sourceGroup(mk({ source: url, sourceKind: 'git', origin: 'external', installed: true }), [])
    expect(g.key).toBe(`git:${url}`)
    expect(g.label).toBe(url)
  })

  it('still folds bundled/native apps into one "Built-in" group', () => {
    expect(sourceGroup(mk({ native: true, source: '/anywhere/on/disk' }), []).label).toBe('Built-in')
    expect(sourceGroup(mk({ sourceKind: 'bundled', installed: false, source: '' }), []).label).toBe('Built-in')
  })
})
