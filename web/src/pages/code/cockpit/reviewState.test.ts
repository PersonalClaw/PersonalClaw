import { describe, expect, it } from 'vitest'
import { centerFromQuery, centerPatch, fromReviewPath, isReviewPath, toReviewPath } from './reviewState'

// ── What a code cockpit keeps in its address ──────────────────────────────────────────────────
//
// The one thing its center shows (`file`, `diff` or `commit`), read from the address and written
// back as a path inside the workspace or a commit hash. The desktop's link parser holds a link to
// the same rules; the page holds the address to them again, since an address can be typed.

const WS = '/repo/feed'

describe('isReviewPath', () => {
  it('takes a path inside the workspace', () => {
    for (const p of ['src/a.py', 'a.py', 'src/.hidden/a..b.py', 'src/a b.py']) expect(isReviewPath(p), p).toBe(true)
  })

  it('refuses a path that leaves the workspace, or one with control characters', () => {
    for (const p of ['', '/etc/passwd', '~/.ssh/id_rsa', '../secrets', 'src/../../x', 'src/..', 'a\\b', 'C:/x',
      'src/a\u0000b', 'src/a\tb', 'src/a\nb', 'src/a\rb', 'src/a\u001Fb', 'src/a\u007Fb', 'src/a\u0085b', 'src/a\u009Fb',
      'src/a\u202Ab', 'src/a\u202Eb', 'src/a\u2066b', 'src/a\u2069b', 'a'.repeat(1025)]) {
      expect(isReviewPath(p), JSON.stringify(p)).toBe(false)
    }
    expect(isReviewPath('a'.repeat(1024))).toBe(true)
  })
})

describe('toReviewPath and fromReviewPath', () => {
  it('writes a workspace file as its path inside the workspace, and reads it back', () => {
    expect(toReviewPath(WS, `${WS}/src/a.py`)).toBe('src/a.py')
    expect(toReviewPath(`${WS}/`, `${WS}/src/a.py`)).toBe('src/a.py')
    expect(fromReviewPath(WS, 'src/a.py')).toBe(`${WS}/src/a.py`)
    expect(fromReviewPath(`${WS}/`, 'src/a.py')).toBe(`${WS}/src/a.py`)
  })

  it('finds the workspace in a resolved path too, as the tree names it', () => {
    expect(toReviewPath('/tmp/feed', '/private/tmp/feed/src/a.py')).toBe('src/a.py')
  })

  it('writes nothing for a file outside the workspace, or the workspace itself', () => {
    expect(toReviewPath(WS, '/elsewhere/a.py')).toBeNull()
    expect(toReviewPath(WS, WS)).toBeNull()
    expect(toReviewPath('', '/repo/feed/a.py')).toBeNull()
  })
})

describe('centerFromQuery', () => {
  it('reads the one thing the center shows', () => {
    expect(centerFromQuery({})).toBeNull()
    expect(centerFromQuery({ pane: 'changes', task: 't-1' })).toBeNull()
    expect(centerFromQuery({ file: 'src/a.py' })).toEqual({ kind: 'file', path: 'src/a.py' })
    expect(centerFromQuery({ diff: 'src/a.py' })).toEqual({ kind: 'diff', path: 'src/a.py' })
    expect(centerFromQuery({ commit: '0123abc' })).toEqual({ kind: 'commit', hash: '0123abc' })
  })

  it('refuses an address that names more than one: the center shows one at a time', () => {
    const named: Record<string, string>[] = [
      { file: 'a.py', diff: 'b.py' }, { file: 'a.py', commit: '0123abc' }, { diff: 'b.py', commit: '0123abc' },
      { file: 'a.py', diff: 'b.py', commit: '0123abc' },
    ]
    for (const q of named) expect(centerFromQuery(q), JSON.stringify(q)).toBe('invalid')
  })

  it('holds a commit to 7 to 40 lowercase hex characters', () => {
    for (const hash of ['012345', 'a'.repeat(41), '0123ABC', '0123abg', 'HEAD', 'main', '-0123abc', '0123abc~1', '0123abc^']) {
      expect(centerFromQuery({ commit: hash }), hash).toBe('invalid')
    }
    expect(centerFromQuery({ commit: '0123456' })).toEqual({ kind: 'commit', hash: '0123456' })
    expect(centerFromQuery({ commit: 'f'.repeat(40) })).toEqual({ kind: 'commit', hash: 'f'.repeat(40) })
  })

  it('refuses a file or a diff outside the workspace', () => {
    for (const key of ['file', 'diff']) {
      for (const p of ['/etc/passwd', '../x', 'a\\b', 'src/a\u0000b']) expect(centerFromQuery({ [key]: p }), `${key} ${p}`).toBe('invalid')
    }
  })
})

describe('centerPatch', () => {
  it('names what the center shows and drops the other two', () => {
    expect(centerPatch({ kind: 'diff', path: 'src/a.py' })).toEqual({ file: null, diff: 'src/a.py', commit: null })
    expect(centerPatch({ kind: 'commit', hash: '0123abc' })).toEqual({ file: null, diff: null, commit: '0123abc' })
    expect(centerPatch(null)).toEqual({ file: null, diff: null, commit: null })
  })
})
