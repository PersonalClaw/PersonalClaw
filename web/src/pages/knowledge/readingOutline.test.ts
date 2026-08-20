import { describe, expect, it } from 'vitest'
import { parseOutline } from './readingOutline'

// ── The outline's key, and the two ways a slug-keyed one breaks (KL-16) ──────────────────
//
// A table of contents normally keys each row on a slug of the rendered heading text. Both
// failures below are cheap to hit in a real saved article, and both are silent — the outline
// renders, the rows look right, and clicking one goes to the wrong place:
//
//   DUPLICATE TITLES  two `## Setup` sections mint ONE id twice, so the second row scrolls to
//                     the first section forever. The assertion for this is NOT "two rows
//                     exist" — a slug-keyed parser also returns two rows. It is that their
//                     KEYS DIFFER while their TEXT IS IDENTICAL, which is the only shape that
//                     distinguishes the two implementations.
//   INLINE MARKUP     `## The `config` file` renders as three children, so a text-derived key
//                     depends on how the renderer splits nodes. Here the display text is
//                     flattened for reading and the key does not move, because the key was
//                     never derived from the text.
//
// 🪤 AND THE PARSER'S OWN MOST LIKELY BUG: a `# comment` line inside a fenced shell block is
// not a heading. Every negative case below carries a positive control in the same test, so an
// empty return cannot make an exclusion pass by being vacuous.

describe('the key is the source offset, not a slug of the text', () => {
  const DUPLICATE = [
    '# Guide',
    '',
    '## Setup',
    '',
    'Install it.',
    '',
    '## Usage',
    '',
    '## Setup',
    '',
    'Set it up again, for the other platform.',
  ].join('\n')

  it('two headings with the SAME words get two DIFFERENT keys', () => {
    const setup = parseOutline(DUPLICATE).filter((e) => e.text === 'Setup')

    // Vacuity: the document really does contain the collision a slug would suffer.
    expect(setup).toHaveLength(2)
    expect(new Set(setup.map((e) => e.text)).size, 'the two headings are textually identical').toBe(1)

    // The finding: identical text, distinct identity.
    expect(new Set(setup.map((e) => e.offset)).size, 'and their keys are distinct').toBe(2)
    expect(setup[0].offset).toBe(DUPLICATE.indexOf('## Setup'))
    expect(setup[1].offset).toBe(DUPLICATE.lastIndexOf('## Setup'))
  })

  it('every entry in the document has a unique key', () => {
    const all = parseOutline(DUPLICATE)
    expect(all).toHaveLength(4)
    expect(new Set(all.map((e) => e.offset)).size).toBe(4)
  })

  it('the key is the index of the `#` itself, even when the heading is indented', () => {
    const md = 'Intro.\n\n   ### Indented three\n'
    const [entry] = parseOutline(md)
    expect(entry, 'a heading indented 0-3 spaces is still a heading').toBeTruthy()
    expect(entry.offset).toBe(md.indexOf('###'))
    expect(md[entry.offset]).toBe('#')
  })
})

describe('inline markup changes the text, never the key', () => {
  it('flattens code spans, emphasis and links for display', () => {
    expect(parseOutline('## The `config` file')[0].text).toBe('The config file')
    expect(parseOutline('## The **config** file')[0].text).toBe('The config file')
    expect(parseOutline('## The _config_ file')[0].text).toBe('The config file')
    expect(parseOutline('## The [config](https://x.test/c) file')[0].text).toBe('The config file')
    expect(parseOutline('## ~~The~~ config file')[0].text).toBe('The config file')
  })

  it('and the key is identical across all of those spellings', () => {
    const keys = [
      '## The `config` file',
      '## The **config** file',
      '## The config file',
      '## The [config](https://x.test/c) file',
    ].map((md) => parseOutline(md)[0].offset)

    // Vacuity: the parse produced an entry for each spelling in the first place.
    expect(keys).toHaveLength(4)
    expect(new Set(keys), 'one key for four renderings of the same heading').toEqual(new Set([0]))
  })

  it('strips a closing `#` sequence but keeps a sharp that belongs to the word', () => {
    expect(parseOutline('## Setup ##')[0].text).toBe('Setup')
    expect(parseOutline('## Notes on C#')[0].text).toBe('Notes on C#')
  })
})

describe('a `#` inside code is not a heading', () => {
  const FENCED = [
    '# Install',
    '',
    '```bash',
    '# clone the repo first',
    'git clone https://x.test/r',
    '## not a heading either',
    '```',
    '',
    '## Configure',
  ].join('\n')

  it('skips backtick-fenced lines while still finding the real headings', () => {
    const entries = parseOutline(FENCED)
    // Positive control first: without it, a parser returning [] would pass the exclusion.
    expect(entries.map((e) => e.text)).toEqual(['Install', 'Configure'])
    expect(entries.some((e) => e.text.includes('clone'))).toBe(false)
  })

  it('skips tilde fences too, and a longer fence closes only on a matching one', () => {
    const md = [
      '# Real',
      '',
      '~~~~',
      '# still code',
      '~~~',      // shorter than the opener — does NOT close it
      '# also still code',
      '~~~~',
      '',
      '## Real again',
    ].join('\n')
    expect(parseOutline(md).map((e) => e.text)).toEqual(['Real', 'Real again'])
  })

  it('an unclosed fence swallows the rest of the document', () => {
    const md = '# Real\n\n```\n# not a heading\n'
    const entries = parseOutline(md)
    expect(entries.map((e) => e.text)).toEqual(['Real'])
  })

  it('four spaces of indent is code, not a heading', () => {
    const md = '# Real\n\nRun:\n\n    # sudo make install\n\n## Real again'
    expect(parseOutline(md).map((e) => e.text)).toEqual(['Real', 'Real again'])
  })
})

describe('depth is relative to the shallowest heading present', () => {
  it('a document whose top level is `###` renders flat', () => {
    const md = ['### Overview', '', '#### Detail', '', '### Next', '', '##### Deep'].join('\n')
    const entries = parseOutline(md)

    // Vacuity: the raw levels really are 3/4/3/5 — nothing here starts at `#`.
    expect(md.match(/^#+/gm)!.map((h) => h.length)).toEqual([3, 4, 3, 5])
    expect(entries.map((e) => e.depth)).toEqual([0, 1, 0, 2])
  })

  it('and the same shape starting at `#` gives the same depths', () => {
    const md = ['# Overview', '', '## Detail', '', '# Next', '', '### Deep'].join('\n')
    expect(parseOutline(md).map((e) => e.depth)).toEqual([0, 1, 0, 2])
  })

  it('a lone deep heading is depth 0 — one level in from nothing is meaningless', () => {
    expect(parseOutline('###### Footnote').map((e) => e.depth)).toEqual([0])
  })
})

describe('what is not a heading', () => {
  it('an empty or heading-less body returns nothing', () => {
    expect(parseOutline('')).toEqual([])
    expect(parseOutline('Just prose, over\ntwo lines.\n\nAnd a second paragraph.')).toEqual([])
  })

  it('a marker with no space, and seven markers, are both prose', () => {
    // Positive control: the same document's real heading IS found.
    const md = '# Real\n\n#hashtag\n\n####### seven\n'
    expect(parseOutline(md).map((e) => e.text)).toEqual(['Real'])
  })

  it('a bare marker line is kept for document order, with no text to show', () => {
    // `##` alone is a legal empty heading and DOES render as an <h2>, so dropping it would
    // slip the nth-entry ⇄ nth-node mapping by one. The panel is what declines to draw it.
    const entries = parseOutline('# Real\n\n##\n\n## After')
    expect(entries.map((e) => e.text)).toEqual(['Real', '', 'After'])
    expect(entries.map((e) => e.depth)).toEqual([0, 1, 1])
  })
})
