import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { previewLine, previewText } from './previewText'
import { INLINE_CONSTRUCTS } from '../test/markdownSinks'

// ── Issues 618 + 631: previews are plain text; thumbnails are not names ───────────
//
// A one-line row preview and an accessible name both need markdown MARKS GONE, not
// rendered: `**bold**` announced literally, `##` painted raw in a truncated digest
// row (618), and an artifact card's clipped <pre> walking 600 chars of raw markdown
// into the accessible tree (631 — the card's NAME was fixed separately by giving the
// TileButton an aria-label; the excerpt itself is a decorative thumbnail and must be
// aria-hidden). previewText is the family's single strip point; these rails pin its
// behavior and both consumers.

describe('previewText strips marks a one-line preview must not leak', () => {
  it('drops emphasis, heading, list, quote, link and fence marks but keeps the words', () => {
    const md = [
      '# RAIDZ2 vs dRAID',
      '',
      '**Recommendation: use RAIDZ2.** At 6–12 disks *modestly* faster.',
      '> quoted line',
      '- a bullet',
      '2. numbered',
      '[a link](https://example.test/x) and `code`',
      '```ts',
      'const x = 1',
      '```',
    ].join('\n')
    const out = previewText(md)
    expect(out).toContain('RAIDZ2 vs dRAID')
    expect(out).toContain('Recommendation: use RAIDZ2.')
    expect(out).toContain('a link and code')
    expect(out).toContain('const x = 1')
    for (const mark of ['**', '##', '# ', '> ', '- ', '](', '```', '`', '*']) {
      expect(out, `mark ${JSON.stringify(mark)} must be stripped`).not.toContain(mark)
    }
  })

  it('collapses to one line and honors the cap with an ellipsis', () => {
    expect(previewText('a\n\nb\n c')).toBe('a b c')
    const long = previewText('word '.repeat(50), 55)
    expect(long.length).toBeLessThanOrEqual(55)
    expect(long.endsWith('…')).toBe(true)
  })

  it('is safe on empty and null-ish input', () => {
    expect(previewText('')).toBe('')
    expect(previewText(null)).toBe('')
    expect(previewText(undefined)).toBe('')
  })
})

describe('every inline construct reads as the words the renderer draws', () => {
  // 🔴 Two of these were wrong in every body preview (the Inbox row, the session map): a code span's
  // underscores were read as bold ("`__init__.py`" previewed "init.py"), and a link whose address
  // held parentheses kept a ")" ("[the thread](javascript:alert(1))" previewed "the thread)"). The
  // rest left their marks: an entity, an escape, an autolink's brackets, a formatting tag, a
  // strikethrough's tildes, inline math's dollars.
  it.each(INLINE_CONSTRUCTS)('%s', (_what, md, words) => {
    expect(previewText(md)).toBe(words)
  })

  it("a code span's text is code: no emphasis rule reads it", () => {
    expect(previewText('Fix the `__init__.py` import')).toBe('Fix the __init__.py import')
    expect(previewText('Keep `*args` and `**kwargs`')).toBe('Keep *args and **kwargs')
  })

  it("a link's address may hold one level of parentheses", () => {
    expect(previewText('Reply to [the thread](javascript:alert(1)) today')).toBe('Reply to the thread today')
    expect(previewText('See [the page](https://example.com/wiki/Foo_(bar)) here')).toBe('See the page here')
  })
})

describe('both leak surfaces route through the strip point (source-level)', () => {
  const inbox = readFileSync(resolve(__dirname, '../pages/inbox/InboxPage.tsx'), 'utf8')
  const card = readFileSync(resolve(__dirname, '../pages/artifacts/ArtifactCard.tsx'), 'utf8')

  it('the inbox row preview renders its marks inline, and its label strips them', () => {
    expect(inbox).toContain("import { previewText } from '../../lib/previewText'")
    // The visual one-liner is an excerpt: the marks render, inline, inside the row's truncate…
    expect(inbox).toMatch(/<p[^>]*truncate[^>]*><Markdown inline>\{it\.message\}<\/Markdown><\/p>/)
    // …and the announced name's message part is the stripped text.
    expect(inbox).toMatch(/rowSubject\(\[[^\]]*previewText\(it\.message\)\]/)
  })

  it('the artifact excerpt is a decorative thumbnail, hidden from the accessible tree', () => {
    const excerpt = card.slice(card.indexOf('function ExcerptPreview'))
    expect(excerpt.slice(0, 600)).toContain('aria-hidden')
  })
})

describe('a cap never splits a character', () => {
  it('backs off a cut that would leave half of a surrogate pair', () => {
    expect(previewLine('ab😀cd', 4)).toBe('ab…')
    expect(previewText('ab😀cd', 4)).toBe('ab…')
    expect(previewLine('ab😀cd', 5)).toBe('ab😀…')
  })
})

describe('a nested HTML comment is removed whole', () => {
  // Removing the inner comment of "<!<!-- x -->-- y -->" leaves a whole comment behind it, so the
  // preview removes comments until none is left. (The row draws those leftovers as the text they
  // are, since its parse saw one comment and text around it; a preview never shows a comment.)
  it.each([
    ['Ship <!<!-- x -->-- y --> it', 'Ship it'],
    ['Ship <!<!<!-- x -->-- y -->-- z --> it', 'Ship it'],
  ])('%s', (md, words) => {
    for (const preview of [previewText(md), previewLine(md)]) {
      expect(preview).toBe(words)
      expect(preview).not.toContain('<!--')
    }
  })
})

describe('comment removal is one linear pass', () => {
  it('a long text of comment markers previews in under 250 ms', () => {
    const text = '<!'.repeat(700) + '<!-- -->' + '-- -->'.repeat(700) + ' ' + 'x<!--'.repeat(6000)
    const started = performance.now()
    previewText(text)
    expect(performance.now() - started).toBeLessThan(250)
  })
})
