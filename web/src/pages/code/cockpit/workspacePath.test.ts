/** The one reading of a path inside a code workspace (`workspacePath`), and what an address open
 *  leaves alone. `aCockpitPathIsReadFromItsWorkspaceRoot.test.ts` holds each site to it. */
import { describe, expect, it } from 'vitest'
import { inWorkspace, takesTheWheel, underWorkspace, workspaceRelative } from './workspacePath'

describe('workspaceRelative', () => {
  it('strips the workspace root as a prefix, with its trailing slash', () => {
    expect(workspaceRelative('/work/feed', '/work/feed/src/feed/a.py')).toBe('src/feed/a.py')
    expect(workspaceRelative('/work/feed/', '/work/feed/a.py')).toBe('a.py')
    expect(workspaceRelative('/work/feed', '/work/feed')).toBe('')
  })

  it('reads the resolved forms macOS names a path by, either way round', () => {
    expect(workspaceRelative('/tmp/feed', '/private/tmp/feed/a.py')).toBe('a.py')
    expect(workspaceRelative('/private/var/feed', '/var/feed/a.py')).toBe('a.py')
  })

  it('reads a resolved root a read gave, when it names the same folder', () => {
    expect(workspaceRelative('/Users/me/feed', '/Volumes/data/feed/src/a.py', '/Volumes/data/feed')).toBe('src/a.py')
    // A repository root above the workspace is not the workspace.
    expect(workspaceRelative('/Users/me/feed', '/Volumes/data/a.py', '/Volumes/data')).toBeNull()
  })

  it('is null for a path outside it, a sibling whose name starts with its own included', () => {
    expect(workspaceRelative('/work/feed', '/work/feedback/x')).toBeNull()
    expect(workspaceRelative('/work/feed', '/elsewhere/feed/x')).toBeNull()
    expect(workspaceRelative('', '/work/feed/x')).toBeNull()
  })
})

describe('underWorkspace and inWorkspace', () => {
  it('a sibling whose name starts with the workspace\'s is not under it', () => {
    expect(underWorkspace('/work/feed', '/work/feedback/x')).toBe(false)
    expect(underWorkspace('/work/feed', '/private/work/feed/x')).toBe(true)
  })

  it('re-roots a resolved path under the workspace as it is named, and leaves an outside one', () => {
    expect(inWorkspace('/tmp/feed', '/private/tmp/feed/src/feed/a.py')).toBe('/tmp/feed/src/feed/a.py')
    expect(inWorkspace('/tmp/feed', '/tmp/feedback/a.py')).toBe('/tmp/feedback/a.py')
  })
})

describe('takesTheWheel', () => {
  it('an open the user made stops following the worker; the worker\'s own and an address\'s do not', () => {
    expect(takesTheWheel({ path: '/work/feed/a.py' })).toBe(true)
    expect(takesTheWheel({ path: '/work/feed/a.py', _follow: true })).toBe(false)
    expect(takesTheWheel({ path: '/work/feed/a.py', _restore: true })).toBe(false)
    expect(takesTheWheel(undefined)).toBe(true)
  })
})
