import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Markdown, parseInline, plainLine } from './Markdown'

// ── The fast path draws exactly what the parse would ─────────────────────────────────────────
//
// Inline and line modes sit in long lists (the task list, the rail, the palette), and most of
// what they show is plain words. A string with nothing markdown acts on renders as itself, with no
// parse: these hold the fast path to the parse's own DOM for the demo home's task titles and a
// corpus of plain sentences, and plant a string the fast path must hand to the parser.

const DEMO_TASKS = join(process.cwd(), '..', 'src', 'personalclaw', 'tests_fixtures', 'demo-home', 'tasks')
const TITLES: string[] = readdirSync(DEMO_TASKS)
  .filter((f) => f.endsWith('.json'))
  .map((f) => String(JSON.parse(readFileSync(join(DEMO_TASKS, f), 'utf8')).title ?? ''))
  .filter(Boolean)

const SENTENCES = [
  'Ship the weekly digest on Friday',
  "Pick next week's five candidates from the queue",
  'Raise the disk-usage alert threshold to 85%',
  'Meet at 10:30 in room 4 (the big one)',
  'Is the backup restorable?',
  'Mixed case, commas, and full stops.',
  'Ünïcödé titles stay as they are',
]

describe('the fast path', () => {
  it('has the demo home to read', () => {
    expect(TITLES.length).toBeGreaterThanOrEqual(10)
  })

  it.each([...TITLES, ...SENTENCES].map((t) => [t]))('"%s" takes the fast path and draws what the parse draws', (text) => {
    expect(plainLine(text)).toBe(true)
    for (const mode of [{ line: true }, { line: false }]) {
      const fast = render(mode.line ? <Markdown line>{text}</Markdown> : <Markdown inline>{text}</Markdown>).container.innerHTML
      const parsed = render(<span>{parseInline(text, mode)}</span>).container.innerHTML
      expect(fast).toBe(parsed)
    }
  })

  it.each([
    ['a backtick', 'Use `make test` here'],
    ['bold', 'A **firm** date'],
    ['a link', 'See [the notes](https://example.com)'],
    ['a tag', 'Press <kbd>K</kbd>'],
    ['an entity', 'Tom &amp; Jerry'],
    ['a web address', 'Read https://example.com first'],
    ['an email address', 'Write to owner@example.com'],
    ['a leading number', '2024. Year review'],
    ['a leading dash', '- draft'],
    ['space at an end', ' padded '],
    ['a line ending', 'one\ntwo'],
    ['a hit mark', 'find <<this>>'],
  ])('plant: %s goes through the parser', (_what, text) => {
    expect(plainLine(text)).toBe(false)
  })

  it('plant: a backtick parsed renders its code', () => {
    const { container } = render(<Markdown line>{'Use `make test` here'}</Markdown>)
    expect(container.querySelector('code')?.textContent).toBe('make test')
  })
})
