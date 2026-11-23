/** A cockpit path is read from its workspace's root, not from the last folder of the same name.
 *
 *  Six places in the code pages turned an absolute path into a workspace path by searching for
 *  `/<workspace folder name>/` and taking the LAST match. Take the workspace `/work/feed` and the
 *  changed file `/work/feed/src/feed/a.py`: the match landed on `src/feed/`, the path became `a.py`,
 *  and the cockpit opened `/work/feed/a.py`. Any Python src layout (`feed/src/feed/`) or Go
 *  `cmd/<name>/` folder hit it. Now one helper (`cockpit/workspacePath`) strips the workspace root
 *  as a prefix, with its trailing slash, in the forms it is named by (as written, resolved, and with
 *  macOS's `/private`), and a path under no form of it is outside the workspace.
 */
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../../test/sourceTree'
import { changeDiff, changePath } from './cockpit/files/changes'
import { toReviewPath } from './cockpit/reviewState'
import { resolveTouchedPath } from './cockpit/cockpitShared'

const WS = '/work/feed'
const NESTED = '/work/feed/src/feed/a.py'

describe('a workspace whose name repeats below it', () => {
  it('🔴 the Changes panel opens the changed file as itself', () => {
    expect(changePath(WS, NESTED)).toBe('src/feed/a.py')
    expect(changeDiff(WS, NESTED, 'M').path).toBe(NESTED)
  })

  it('🔴 the Changes panel reads git\'s resolved path (macOS /private) as the same file', () => {
    expect(changePath('/tmp/feed', '/private/tmp/feed/src/feed/a.py')).toBe('src/feed/a.py')
  })

  it('🔴 the review address names the file as itself, from its resolved path too', () => {
    expect(toReviewPath(WS, NESTED)).toBe('src/feed/a.py')
    expect(toReviewPath('/tmp/feed', '/private/tmp/feed/src/feed/a.py')).toBe('src/feed/a.py')
  })

  it('🔴 a worker\'s resolved path opens the file as itself (follow the worker, files touched)', () => {
    expect(resolveTouchedPath('/private/tmp/feed/src/feed/a.py', '/tmp/feed')).toEqual({
      abs: '/tmp/feed/src/feed/a.py', rel: 'src/feed/a.py',
    })
  })

  it('a sibling folder whose name starts with the workspace\'s is outside it', () => {
    expect(toReviewPath(WS, '/work/feedback/x.py')).toBeNull()
    expect(resolveTouchedPath('/work/feedback/x.py', WS)).toBeNull()
  })
})

describe('no code page finds the workspace by the last folder of its name', () => {
  it('🔴 every site goes through the one helper', () => {
    const root = join(__dirname)
    const files = filesUnder(root, (name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))
    expect(files.length, 'the walk found the code pages').toBeGreaterThan(20)
    const searching = files.filter((f) => /\.lastIndexOf\((marker|`\/\$\{)/.test(readSource(f)))
    expect(searching.map((f) => f.slice(root.length + 1))).toEqual([])
  })
})
