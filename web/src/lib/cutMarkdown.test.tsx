import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { cutMarkdown, cutMarkdownLine } from './cutMarkdown'
import { Markdown } from '../ui/Markdown'
import { MD_BLOCK, MD_LINE } from '../test/markdownSinks'

// ── A cut never leaves a construct open ──────────────────────────────────────────────────────
//
// A plain `slice` of markdown can stop inside a fence (everything after it becomes code), a code
// span or an emphasis (its marks show). Each cut below is rendered, at every length, and read as a
// reader sees it: no literal backtick or asterisk, and the cut's "…" never inside code.

const LENGTHS = (text: string) => Array.from({ length: text.length + 2 }, (_, i) => i + 1)

/** What a reader would see wrong in `el`. */
function leftOpen(el: Element): string[] {
  const problems: string[] = []
  const text = el.textContent ?? ''
  if (text.includes('`')) problems.push('a literal backtick')
  if (text.includes('*')) problems.push('a literal asterisk')
  if (Array.from(el.querySelectorAll('pre, code')).some((c) => (c.textContent ?? '').includes('…'))) problems.push('the cut inside code')
  return problems
}

describe('cutMarkdown cuts a body at a block boundary', () => {
  it.each(LENGTHS(MD_BLOCK).map((n) => [n]))('at %i characters, nothing is left open', (n) => {
    const out = cutMarkdown(MD_BLOCK, n)
    const fences = out.split('\n').filter((l) => /^ {0,3}(```|~~~)/.test(l)).length
    expect(fences % 2, `an open fence in ${JSON.stringify(out)}`).toBe(0)
    const { container } = render(<Markdown>{out}</Markdown>)
    expect(leftOpen(container), JSON.stringify(out)).toEqual([])
  })

  it('a body that fits is returned as it is', () => {
    expect(cutMarkdown(MD_BLOCK, MD_BLOCK.length)).toBe(MD_BLOCK)
  })

  it('keeps whole blocks: the heading and the list, then the cut', () => {
    const out = cutMarkdown(MD_BLOCK, MD_BLOCK.indexOf('```') + 2)
    expect(out).toBe('## Plan\n\n- read `notes.md` first\n- **ship** the digest\n\n…')
  })
})

describe('cutMarkdownLine closes what the cut left open', () => {
  it.each(LENGTHS(MD_LINE).map((n) => [n]))('the line at %i characters renders with no mark showing', (n) => {
    const out = cutMarkdownLine(MD_LINE, n)
    const { container } = render(<Markdown line>{out}</Markdown>)
    expect(leftOpen(container), JSON.stringify(out)).toEqual([])
  })

  it.each(LENGTHS(MD_BLOCK).map((n) => [n]))('the body as an excerpt at %i characters renders with no mark showing', (n) => {
    const out = cutMarkdownLine(MD_BLOCK, n)
    const { container } = render(<Markdown inline>{out}</Markdown>)
    expect(leftOpen(container), JSON.stringify(out)).toEqual([])
  })

  it('a line a writer already cut is repaired: "**Key poin…"', () => {
    const out = cutMarkdownLine('**Key poin…', 240)
    expect(out).toBe('**Key poin**…')
    const { container } = render(<Markdown line>{out}</Markdown>)
    expect(container.querySelector('strong')?.textContent).toBe('Key poin')
    expect(container.textContent).toBe('Key poin…')
  })

  it('a link the cut halved keeps its words', () => {
    expect(cutMarkdownLine('Read [the release notes](https://example.com/notes) today', 40)).toBe('Read the release notes…')
  })

  it('a "*" between spaces is no emphasis, and is not closed', () => {
    expect(cutMarkdownLine('5 * 3 is fifteen, not sixteen', 12)).toBe('5 * 3 is…')
  })

  it('a line that fits is returned as it is', () => {
    expect(cutMarkdownLine(MD_LINE, MD_LINE.length)).toBe(MD_LINE)
  })
})

describe('a cut the line makes itself invents no mark and drops no character', () => {
  const drawn = (md: string) => render(<Markdown line>{md}</Markdown>).container

  it.each([
    ['a "*" the whole line never closes', 'Pass *args to the helper and then more words follow', 20, 'Pass *args to the…'],
    ['a "_" the whole line never closes', 'Rename _private_helper in utils and then ship it', 30, 'Rename _private_helper in…'],
  ])('%s is literal: it stays, and nothing turns italic', (_what, line, max, words) => {
    const el = drawn(cutMarkdownLine(line, max))
    expect(el.querySelector('em, strong')).toBeNull()
    expect(el.textContent).toBe(words)
  })

  it('a literal mark at the end of a word is kept', () => {
    expect(drawn(cutMarkdownLine('Only $9.99* this week for everyone', 12)).textContent).toBe('Only $9.99*…')
  })

  it.each(Array.from({ length: 10 }, (_, i) => [i + 14]))('an escaped mark is kept, and no backslash is left, at %i', (n) => {
    const el = drawn(cutMarkdownLine('Five \\*stars\\* exactly and more words', n))
    expect(el.textContent).not.toContain('\\')
    expect(el.querySelector('em, strong')).toBeNull()
  })

  it('a mark inside an open code span is code, and is kept', () => {
    expect(drawn(cutMarkdownLine('Glob `src/**` everywhere in tree', 12)).querySelector('code')?.textContent).toBe('src/*')
  })

  it('a cut never splits a character', () => {
    expect(cutMarkdownLine('ab😀cd', 4)).toBe('ab…')
  })

  it.each(['__firmlyfixed__', '_looselyheld_', '~~oldplanhere~~', '~gonething~'].flatMap((t) => LENGTHS(t).map((n) => [t, n])))(
    '%s at %i draws no "_" or "~"', (token, n) => {
      expect(drawn(cutMarkdownLine(token as string, n as number)).textContent).not.toMatch(/[_~]/)
    })

  it('a line of nothing but space cuts to nothing', () => {
    expect(cutMarkdownLine('     ', 2)).toBe('')
    expect(cutMarkdown('     ', 2)).toBe('')
  })
})

describe("a body's cut ends on words, never on a bare block marker", () => {
  it.each(Array.from({ length: 7 }, (_, i) => [i + 16]))('a list cut at %i', (n) => {
    const out = cutMarkdown('- first item\n- second item that is long', n)
    expect(out, JSON.stringify(out)).not.toMatch(/(?:^|\n)[ \t]*(?:[-*+>]|#{1,6}|\d+[.)])[ \t]*…$/)
    expect(out.startsWith('- first item')).toBe(true)
  })
})
