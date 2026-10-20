/**
 * The rig spike's harness route (#3887) exists only in a build that asks for it.
 *
 * `#/buddy-rig` mounts the spike's harness in place of the app, so the measurement window and the
 * owner's side-by-side watch see the two candidate extensions of the agent orb with nothing else
 * drawn. A release build is made without `VITE_BUDDY_RIG`, and the harness is imported only behind
 * that flag, so the bundler folds the branch away and emits no chunk of it: no navigation, no
 * palette entry and no URL reaches it there. The source assertions below hold that shape; the
 * record (`docs/design/buddy/decisions/rig.md`) says how a release build was checked for the chunk.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { BUDDY_RIG_ROUTE, isBuddyRigHash, rigViewOf } from './route'

describe('the harness route', () => {
  it.each([
    ['#/buddy-rig', true],
    ['#/buddy-rig?view=layered', true],
    ['#buddy-rig', true],
    ['#/buddy-rigs', false],
    ['#/chat', false],
    ['#/settings/buddy-rig', false],
    ['', false],
  ])('%s is the harness: %s', (hash, harness) => {
    expect(isBuddyRigHash(hash)).toBe(harness)
  })

  it('opens on both candidates side by side at 48 px, drawing every frame', () => {
    expect(rigViewOf('#/buddy-rig')).toEqual({ view: 'both', size: 48, fps: null })
  })

  it('reads the view, the size and the frame rate the measurement asks for', () => {
    expect(rigViewOf('#/buddy-rig?view=in-place&size=96')).toEqual({ view: 'in-place', size: 96, fps: null })
    expect(rigViewOf('#/buddy-rig?view=layered&size=18')).toEqual({ view: 'layered', size: 18, fps: null })
    expect(rigViewOf('#/buddy-rig?view=none')).toEqual({ view: 'none', size: 48, fps: null })
    expect(rigViewOf('#/buddy-rig?view=sheet')).toEqual({ view: 'sheet', size: 48, fps: null })
    expect(rigViewOf('#/buddy-rig?view=in-place&fps=30')).toEqual({ view: 'in-place', size: 48, fps: 30 })
  })

  it('falls back to the default for a view, a size or a rate it does not know', () => {
    expect(rigViewOf('#/buddy-rig?view=canvas&size=20&fps=0')).toEqual({ view: 'both', size: 48, fps: null })
    expect(rigViewOf('#/buddy-rig?fps=500')).toEqual({ view: 'both', size: 48, fps: null })
  })
})

describe('a release build carries none of it', () => {
  const main = readFileSync(resolve(__dirname, '../main.tsx'), 'utf8')
  const app = readFileSync(resolve(__dirname, '../app/App.tsx'), 'utf8')

  it('the entry imports the harness only behind the build flag', () => {
    expect(main).toMatch(
      /import\.meta\.env\.VITE_BUDDY_RIG === '1'\s*\?\s*lazy\(\(\) => import\('\.\/buddyRig\/BuddyRigHarness'\)/,
    )
  })

  it('nothing imports it statically, so nothing pulls it into the entry chunk', () => {
    expect(main).not.toMatch(/^import [^\n]*buddyRig/m)
    expect(app).not.toMatch(/buddyRig/)
  })

  it('the app shell offers no way to it: no nav tile, routable entry or shell route', () => {
    expect(app).not.toContain(BUDDY_RIG_ROUTE)
  })
})
