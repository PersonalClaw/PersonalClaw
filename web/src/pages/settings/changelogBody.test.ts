import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { changelogBody, plainHeadline, updateEntries } from './UpdatesPanel'

// ── The panel told the reader how the panel works ─────────────────────────────────────────────────
//
// `/api/changelog` serves CHANGELOG.md verbatim — **255,413 characters** — and `#/settings/updates`
// rendered all of it inside a card already headed "Changelog · What's changed recently". Measured from
// the live DOM, the first four blocks in that card were:
//
//   H1  "Changelog"                                     ← a SECOND <h1>, nested inside an <h2> section
//   P   "All notable changes to PersonalClaw are recorded here. The format follows Keep a Changelog…"
//   P   "The in-app Updates panel reads this file (`GET /api/changelog`) to show \"what's new.\""
//   H2  "Unreleased"                                    ← a SIBLING of the panel's own sections
//
// So: the title duplicated 30px below itself, a note about the file format, and a sentence explaining
// to the user how the surface they are looking at is implemented. The document's front matter is
// written for CONTRIBUTORS; the endpoint is right to serve the file whole, and deciding what "what's
// changed recently" means is the panel's job.
//
// Page heading census, before → after: **h1 × 2 ("Updates", "Changelog") → h1 × 1**, and the outline
// becomes h1 Updates › h2 Changelog › h3 Unreleased › h4 Added instead of putting a release on the same
// rung as the page's own furniture.
//
// 🔑 WHAT THIS DOES NOT DO, on purpose: it does not cap the history. 1,992 DOM nodes and 43,947px of
// scroll in a 384px box is 80% of the page's total nodes — real, and a cap is a product decision (how
// many releases, and where "the rest" lives), so it is logged rather than guessed at here.

const PANEL = join(process.cwd(), 'src', 'pages', 'settings', 'UpdatesPanel.tsx')

const DOC = [
  '# Changelog',
  '',
  'All notable changes to PersonalClaw are recorded here. The format follows',
  '[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).',
  '',
  'The in-app Updates panel reads this file (`GET /api/changelog`) to show "what\'s new."',
  '',
  '## [Unreleased]',
  '',
  '### Added',
  '',
  '- A thing.',
  '',
  '## [0.1.3] - 2026-08-01',
  '',
  '### Fixed',
  '',
  '- Another thing.',
].join('\n')

describe('the changelog card renders the changelog, not the file', () => {
  it('drops the document title and the contributor preamble', () => {
    const out = changelogBody(DOC)
    expect(out.startsWith('### [Unreleased]'), `starts with: ${out.slice(0, 40)}`).toBe(true)
    expect(out, 'the duplicated title must be gone').not.toMatch(/^#+ Changelog$/m)
    expect(out, 'and the format note with it').not.toMatch(/Keep a Changelog/)
  })

  it('never shows the reader how the panel is implemented', () => {
    // The sharpest line in the preamble, and the reason this is a defect rather than a tidy-up.
    expect(changelogBody(DOC)).not.toMatch(/in-app Updates panel reads this file/)
    expect(changelogBody(DOC), 'nor the endpoint it calls').not.toMatch(/GET \/api\/changelog/)
  })

  it('demotes every heading by one, so a release is not a peer of the page furniture', () => {
    const out = changelogBody(DOC).split('\n')
    expect(out.filter((l) => l.startsWith('### [')).length, 'the two releases become h3').toBe(2)
    expect(out.filter((l) => l === '#### Added' || l === '#### Fixed').length, 'and their groups h4').toBe(2)
    expect(out.some((l) => /^#{1,2} /.test(l)), 'nothing may render as h1 or h2 inside the card').toBe(false)
  })

  it('leaves headings inside fenced code alone — asserted synthetically, because none exist today', () => {
    // 🪤 The real CHANGELOG.md has 2 fence markers and ZERO `#` lines inside them, so a fence-unaware
    // implementation would pass every test written against the real document. A guard whose strictness
    // is never exercised is a guard that is only claimed.
    const withFence = ['## [1.0.0]', '', '```bash', '# not a heading — a shell comment', 'ls -la', '```', '',
      '### Added', '- x'].join('\n')
    const out = changelogBody(withFence).split('\n')
    expect(out, 'the shell comment must survive verbatim').toContain('# not a heading — a shell comment')
    expect(out, 'while the real heading is demoted').toContain('#### Added')
  })

  it('returns the document UNCHANGED when it has no release heading', () => {
    // Hiding everything because a parse found nothing is the worse failure: an empty "what's new" reads
    // as "nothing has changed".
    const odd = '# Changelog\n\nSomething, but no release headings.\n'
    expect(changelogBody(odd)).toBe(odd)
  })

  it('handles the deepest heading the document could gain, and preserves the trailing newline', () => {
    // Not a claim that running it twice is a no-op (it is not — that would defeat demotion); a claim
    // that a 5-level heading does not silently vanish, and that the text is otherwise byte-preserved.
    // The trailing `\n` is part of that: this assertion caught the first version dropping it.
    expect(changelogBody('## a\n##### e\n')).toBe('### a\n###### e\n')
    expect(changelogBody('## a\n###### six\n'), 'h6 has nowhere to go — left as it is')
      .toBe('### a\n###### six\n')
  })

  it('the card renders through it, and the raw document is no longer passed', () => {
    const src = readFileSync(PANEL, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'the card must render the transformed body').toMatch(/<Markdown>\{changelogBody\(changelog\)\}<\/Markdown>/)
    expect(src, 'the raw document must not be rendered again').not.toMatch(/<Markdown>\{changelog\}<\/Markdown>/)
    expect(src, 'and the empty state still answers for an absent changelog').toMatch(/No changelog available\./)
  })
})

// ── A headline-only CHANGELOG: the bold marks nothing, and the blank lines make gaps ──────────────
//
// Every entry is one line, `- **<headline>**`. Measured on `#/settings/updates` with that file: 926 of
// 962 items rendered bold (weight 600) under an `Added` heading drawn at 500, and the gap between two
// items was 6px in one section and 36px in the next — the sections that separate entries with blank
// lines parse as loose lists, one paragraph per item.

describe('the changelog card shows headline-only entries as one plain, even list', () => {
  it('an entry that is one bold span is shown plain', () => {
    expect(plainHeadline('- **Settings → Security sets how long a sign-in lasts.**'))
      .toBe('- Settings → Security sets how long a sign-in lasts.')
    expect(plainHeadline('- **`inbound` is renamed `external_access`**')).toBe('- `inbound` is renamed `external_access`')
  })

  it('bold inside the sentence stays, and so does a headline with text after it', () => {
    // A span that closes only at the end, with balanced bold inside it: one headline.
    expect(plainHeadline('- **A → **B** C**')).toBe('- A → **B** C')
    // The first span closes after A: this line is a headline plus text, not one span.
    expect(plainHeadline('- **A** and **B**')).toBe('- **A** and **B**')
    // An entry whose bold picks out a word keeps it.
    expect(plainHeadline('- The **YOLO mode** toggle applies at once.')).toBe('- The **YOLO mode** toggle applies at once.')
    expect(plainHeadline('- **Never closes')).toBe('- **Never closes')
  })

  it('a blank line between two entries is dropped; one beside a heading is kept', () => {
    const md = ['## [Unreleased]', '', '### Added', '', '- **One.**', '', '- **Two.**', '', '', '- **Three.**', '',
      '### Fixed', '', '- **Four.**'].join('\n')
    expect(changelogBody(md)).toBe(['### [Unreleased]', '', '#### Added', '', '- One.', '- Two.', '- Three.', '',
      '#### Fixed', '', '- Four.'].join('\n'))
  })

  it('leaves bullets, bold and blank lines inside fenced code alone', () => {
    const md = ['## [1.0.0]', '', '```diff', '- **kept**', '', '- **kept too**', '```', '', '- **Shown plain.**'].join('\n')
    expect(changelogBody(md).split('\n')).toEqual(['### [1.0.0]', '', '```diff', '- **kept**', '', '- **kept too**', '```', '',
      '- Shown plain.'])
  })

  it('the real CHANGELOG reaches the card with every entry plain and no gap between entries', () => {
    const out = changelogBody(readFileSync(join(process.cwd(), '..', 'CHANGELOG.md'), 'utf8')).split('\n')
    const entries = out.filter((l) => l.startsWith('- '))
    expect(entries.length, 'the card must still list the entries').toBeGreaterThan(100)
    expect(entries.filter((l) => plainHeadline(l) !== l), 'an entry still rendered as one bold span').toEqual([])
    const gaps = out.filter((l, i) => !l.trim() && out[i - 1]?.startsWith('- ') && out[i + 1]?.startsWith('- '))
    expect(gaps.length, 'a blank line between two entries makes the list loose').toBe(0)
  })

  it('the entries an update brings are its added entry lines, shown plain', () => {
    const changes = ['## [0.2.1] — 2026-10-01', '', '### Added', '', '- **A new thing.**', '- **Another.**'].join('\n')
    expect(updateEntries(changes)).toBe('- A new thing.\n- Another.')
    expect(updateEntries('## [0.2.1] — 2026-10-01\n\n### Added'), 'a diff that added no entry lists nothing').toBe('')
    expect(updateEntries(''), 'no diff, nothing').toBe('')
  })
})
