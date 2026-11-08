import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { EMBEDDED_FORMATTING, Markdown } from './Markdown'
import { previewLine, previewText } from '../lib/previewText'
import { HOSTILE, INLINE_CONSTRUCTS, LEADING, MD_LINE, plain } from '../test/markdownSinks'

// ── A line's tooltip reads what its row draws ────────────────────────────────────────────────
//
// A one-line field is drawn by `<Markdown line>` and named, in its tooltip and in any accessible
// name built from it, by `previewLine`. The two are held to the same words here, for every string
// this release's fixtures use, so a row and its name never disagree. A divergence is fixed in
// `lib/previewText`, never by bending the renderer.

const DEMO_TASKS = join(process.cwd(), '..', 'src', 'personalclaw', 'tests_fixtures', 'demo-home', 'tasks')
const TITLES = readdirSync(DEMO_TASKS).filter((f) => f.endsWith('.json'))
  .map((f) => String(JSON.parse(readFileSync(join(DEMO_TASKS, f), 'utf8')).title ?? '')).filter(Boolean)

const FIXTURES: string[] = [
  MD_LINE,
  HOSTILE,
  ...LEADING.map(([, line]) => line),
  ...TITLES,
  'Fix the `__init__.py` import in **core**',
  'Read [the notes](https://example.com/notes) before *Friday*',
  'Run mcp__github__create_issue for **triage**',
]

/** What a reader sees of the row, one line as the truncate draws it. */
const drawn = (text: string) =>
  (render(<Markdown line>{text}</Markdown>).container.textContent ?? '').replace(/\s+/g, ' ').trim()

describe('previewLine reads what Markdown line draws', () => {
  it.each(FIXTURES.map((s) => [s]))('%s', (text) => {
    expect(previewLine(text)).toBe(drawn(text))
  })

  it('and its result is plain: no mark left for an attribute', () => {
    // (A leading "#" or ">" in a line is its text, not a mark: those lines are held above.)
    for (const text of [MD_LINE, ...TITLES]) {
      expect(plain(previewLine(text)), text).toEqual([])
    }
  })

  it.each(INLINE_CONSTRUCTS)('%s: the row draws the words, and both previews read them', (_what, md, words) => {
    expect(drawn(md)).toBe(words)
    expect(previewLine(md)).toBe(words)
    // One inline pass, two entry points: a body's preview and a line's agree on every inline construct.
    expect(previewText(md)).toBe(previewLine(md))
  })

  // previewText keeps its own copy of the formatting tags: one row per tag the renderer keeps holds
  // the copy to the list, so a tag added to one and not the other turns its row red.
  it.each(EMBEDDED_FORMATTING.map((tag) => [tag]))('<%s>: the row and both previews read its words', (tag) => {
    const md = tag === 'br' ? 'Done<br>Next' : `A <${tag}>word</${tag}> here`
    expect(previewLine(md)).toBe(drawn(md))
    expect(previewText(md)).toBe(previewLine(md))
  })

  it("a body's preview still drops its block marks (previewText)", () => {
    expect(previewText('# Heading\n\n- item')).toBe('Heading item')
    expect(previewLine('# Heading')).toBe('# Heading')
  })
})
