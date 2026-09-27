import { describe, expect, it } from 'vitest'
import {
  basedOn, differencePatch, documentText, isStaleWrite, mergeRecord, mergeText, presentSecrets, rebaseList, sameDocument,
} from './staleWrite'

// ── Re-applying a refused change onto what is stored now ─────────────────────────────────────
//
// A save refused as stale (`409 stale_write`) keeps the user's change; "Reload and reapply" puts it
// back on top of the document another tab saved. These pin the merges that decide what that
// means — and, as important, when they refuse to decide (`null`), because a merge that picked a
// side where both changed the same part would be the silent overwrite this whole contract removes.

describe('the header and the refusal', () => {
  it('names the base revision in If-Match, quoted', () => {
    expect(basedOn('a1b2')).toEqual({ 'If-Match': '"a1b2"' })
  })

  it('recognises the refusal by its code, not its sentence', () => {
    expect(isStaleWrite(Object.assign(new Error('anything'), { code: 'stale_write' }))).toBe(true)
    expect(isStaleWrite(Object.assign(new Error('changed since this copy was read'), { code: 'bad_request' }))).toBe(false)
    expect(isStaleWrite('stale_write')).toBe(false)
  })
})

describe('mergeText', () => {
  const base = ['# Skill', 'one', 'two', 'three', 'four'].join('\n')

  it('keeps both edits when they touch different lines', () => {
    const mine = base.replace('one', 'ONE')
    const theirs = base.replace('four', 'FOUR')
    expect(mergeText(base, mine, theirs)).toBe(['# Skill', 'ONE', 'two', 'three', 'FOUR'].join('\n'))
  })

  it('keeps an insertion from each side', () => {
    const mine = `${base}\nfive`
    const theirs = base.replace('# Skill', '# Skill\nintro')
    expect(mergeText(base, mine, theirs)).toBe(['# Skill', 'intro', 'one', 'two', 'three', 'four', 'five'].join('\n'))
  })

  it('refuses to choose when both changed the same line differently', () => {
    expect(mergeText(base, base.replace('two', 'mine'), base.replace('two', 'theirs'))).toBeNull()
  })

  it('keeps edits of neighbouring lines — each line has one author, so there is one outcome', () => {
    expect(mergeText(base, base.replace('two', 'TWO'), base.replace('three', 'THREE')))
      .toBe(['# Skill', 'one', 'TWO', 'THREE', 'four'].join('\n'))
  })

  it('keeps my edit of the last line when the other side appended after it', () => {
    // The shape a server-side appender produces (an accepted instruction, a workflow run's note):
    // the user edits what they see while a line lands after it.
    const note = 'Always cite sources.'
    expect(mergeText(note, 'Always cite your sources.', `${note}\nPrefer primary sources.`))
      .toBe('Always cite your sources.\nPrefer primary sources.')
  })

  it('refuses two different insertions at one point — which comes first is a choice', () => {
    expect(mergeText(base, `${base}\nmine`, `${base}\ntheirs`)).toBeNull()
  })

  it('refuses an insertion inside lines the other side replaced', () => {
    expect(mergeText(base, base.replace('two\nthree', 'TWO-THREE'), base.replace('two\nthree', 'two\nnew\nthree'))).toBeNull()
  })

  it('the same edit on both sides is one edit', () => {
    const both = base.replace('two', 'TWO')
    expect(mergeText(base, both, both)).toBe(both)
  })

  it('no edit of mine leaves theirs exactly', () => {
    const theirs = base.replace('three', '3')
    expect(mergeText(base, base, theirs)).toBe(theirs)
  })
})

describe('mergeRecord', () => {
  const base = { name: 'Ada', model: 'a', skills: ['s1', 's2'], prompt: 'x\ny\nz' }

  it('takes the fields I changed and theirs for every other one', () => {
    expect(mergeRecord(base, { ...base, model: 'b' }, { ...base, name: 'Grace' }))
      .toEqual({ ...base, model: 'b', name: 'Grace' })
  })

  it('treats a list of names as a set: my addition joins theirs, my removal leaves it', () => {
    const mine = { ...base, skills: ['s1', 's3'] }       // removed s2, added s3
    const theirs = { ...base, skills: ['s1', 's2', 's4'] } // added s4
    expect(mergeRecord(base, mine, theirs)?.skills).toEqual(['s1', 's4', 's3'])
  })

  it('merges a text field by line', () => {
    const mine = { ...base, prompt: 'X\ny\nz' }
    const theirs = { ...base, prompt: 'x\ny\nZ' }
    expect(mergeRecord(base, mine, theirs)?.prompt).toBe('X\ny\nZ')
  })

  it('refuses a field both sides changed to different values', () => {
    expect(mergeRecord(base, { ...base, model: 'b' }, { ...base, model: 'c' })).toBeNull()
  })

  it('a field I deleted stays deleted when they left it alone', () => {
    const { model: _gone, ...mine } = base
    expect(mergeRecord(base, mine as typeof base, { ...base, name: 'Grace' })).toEqual({ ...mine, name: 'Grace' })
  })
})

describe('rebaseList', () => {
  it('re-applies names in and out onto a list another tab changed', () => {
    const edit = rebaseList(['a', 'b'], ['a', 'c'])
    expect(edit(['a', 'b', 'd'])).toEqual(['a', 'd', 'c'])
  })

  it('never duplicates a name the other tab added too', () => {
    expect(rebaseList(['a'], ['a', 'b'])(['a', 'b'])).toEqual(['a', 'b'])
  })
})

describe('showing the difference', () => {
  it('compares content, not key order', () => {
    expect(sameDocument({ a: 1, b: [2] }, { b: [2], a: 1 })).toBe(true)
    expect(documentText({ b: 1, a: 2 })).toBe('{\n  "a": 2,\n  "b": 1\n}')
  })

  it('marks what went and what came, and nothing when they read the same', () => {
    const patch = differencePatch(['alpha', 'beta'], ['alpha', 'gamma'])
    expect(patch).toContain('-  "beta"')
    expect(patch).toContain('+  "gamma"')
    expect(differencePatch('same', 'same')).toBe('')
  })

  it('an empty document has no lines — no lone "-" against a first note', () => {
    expect(differencePatch('', 'Use for incident retros.')).toBe('+Use for incident retros.')
    expect(differencePatch('Seeded.', '')).toBe('-Seeded.')
  })

  it('shows a stored secret as saved and never shows a typed one', () => {
    const present = presentSecrets((k) => k === 'api_key' || k === 'token', ['api_key'])
    // The form holds a stored secret BLANK, meaning "keep it" — the review read it as emptied.
    expect(present({ endpoint: 'e1', api_key: '', token: 'sk-typed-now' })).toEqual({
      endpoint: 'e1', api_key: '(saved, unchanged)', token: '(new value, hidden)',
    })
    expect(present({ token: '' }), 'a secret field with nothing stored and nothing typed').toEqual({ token: '' })
  })

  it('collapses a long unchanged run', () => {
    const before = Array.from({ length: 30 }, (_, i) => `line ${i}`).join('\n')
    const after = before.replace('line 15', 'LINE 15')
    const patch = differencePatch(before, after).split('\n')
    expect(patch).toContain('-line 15')
    expect(patch).toContain('+LINE 15')
    expect(patch.filter((l) => l === '…').length).toBe(2)
    expect(patch.length).toBeLessThan(12)
  })
})
