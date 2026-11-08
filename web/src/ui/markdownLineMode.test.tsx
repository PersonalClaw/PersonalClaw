import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { Markdown } from './Markdown'
import { HOSTILE, LEADING, MD_LINE, inert, rendersLine } from '../test/markdownSinks'

// ── A field written as one line renders as the line it is ────────────────────────────────────
//
// 🔴 Inline mode parsed block syntax and then flattened it, so a one-line field lost its leading
// characters: "# of retries" came out "of retries", "2024. Year review" as "Year review", "1) Fix"
// as "Fix", "> 5 files" as "5 files", and "[Bug]: crash" and "[^1]: see the note" as nothing at
// all. `line` mode turns each block construct off at parse time, by its micromark name; each plant
// below is one construct, so taking a name off the list reddens its own row. The control rows
// prove the same strings still flatten in an excerpt (inline mode), where a block is expected.
//
// And the excerpt itself: a task-list checkbox is a form control and a footnote is a `section`;
// in a row (often a button) neither may land, and a block under a `line-clamp` escapes it.

const BLOCKS = 'h1, h2, h3, h4, h5, h6, ul, ol, li, blockquote, hr, pre, table, section, p, div'

describe('line mode starts no block', () => {
  it.each(LEADING)('%s: the line renders as the text it is', (_construct, line) => {
    const { container } = render(<Markdown line>{line}</Markdown>)
    const sink = container.firstElementChild
    expect(sink?.tagName).toBe('SPAN')
    expect(sink?.querySelector(BLOCKS), 'a block was parsed out of the line').toBeNull()
    expect(container.textContent).toBe(line)
  })

  it.each(LEADING)('control — %s still flattens in an excerpt', (_construct, line) => {
    const { container } = render(<Markdown inline>{line}</Markdown>)
    expect(container.textContent).not.toBe(line)
  })

  it('an indented line is trimmed as any paragraph is, and draws no code (codeIndented)', () => {
    const line = '    four spaces in'
    expect(render(<Markdown line>{line}</Markdown>).container.querySelector('code')).toBeNull()
    expect(render(<Markdown inline>{line}</Markdown>).container.querySelector('code'), 'control').not.toBeNull()
  })

  it('renders a line written the way an agent writes it: two code spans and a bold run', () => {
    const { container } = render(<Markdown line>{MD_LINE}</Markdown>)
    expect(rendersLine(container.firstElementChild!)).toEqual([])
    expect(Array.from(container.querySelectorAll('code')).map((c) => c.textContent)).toEqual(['model_calls', 'export_digest()'])
    expect(container.querySelector('strong')?.textContent).toBe('published.py')
    expect(container.textContent).toBe('Skip model_calls in export_digest() in published.py')
  })

  it('a hostile line leaves no element, handler or anchor', () => {
    const { container } = render(<Markdown line>{HOSTILE}</Markdown>)
    expect(inert(container)).toEqual([])
    expect(container.textContent).toContain('<img src="https://images.example.com/p.png" onerror="alert(1)">')
    expect(container.textContent).toContain('open the report')
  })

  it('a line that opens with a block tag or a comment is no HTML block: its marks still render (htmlFlow)', () => {
    for (const line of ['<div> ships **now**', '<details> open **now**', '<!-- draft --> Ship **now**']) {
      const { container, unmount } = render(<Markdown line>{line}</Markdown>)
      expect(container.querySelector('strong')?.textContent, line).toBe('now')
      unmount()
    }
    expect(render(<Markdown line>{'<div> ships **now**'}</Markdown>).container.textContent).toBe('<div> ships now')
  })

  it('control: an excerpt still reads such a line as an HTML block, its marks unparsed', () => {
    expect(render(<Markdown inline>{'<div> ships **now**'}</Markdown>).container.querySelector('strong')).toBeNull()
  })
})

describe('an image in a row is its words, and nothing is fetched', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it.each([
    ['an https image', 'https://images.example.com/chart.png'],
    ['an artifact image', '/api/artifacts/sales-chart/raw?version=2'],
    ['a script scheme', 'javascript:alert(1)'],
  ])('%s, in inline and in line mode: its alt text, no img, no source, no request', (_what, src) => {
    const fetched = vi.spyOn(globalThis, 'fetch')
    for (const sink of [<Markdown inline>{`See ![the chart](${src}) here.`}</Markdown>, <Markdown line>{`See ![the chart](${src}) here.`}</Markdown>]) {
      const { container, unmount } = render(sink)
      expect(container.querySelector('img'), 'an image element in a row').toBeNull()
      expect(container.querySelector('[src], [srcset]'), 'a source in a row').toBeNull()
      expect(container.querySelector('button'), "a Load placeholder's control in a row").toBeNull()
      expect(container.textContent).toBe('See the chart here.')
      unmount()
    }
    expect(fetched).not.toHaveBeenCalled()
  })

  it('an image with no alt text draws nothing', () => {
    const { container } = render(<Markdown inline>{'Logo ![](https://images.example.com/logo.png) beside it'}</Markdown>)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toBe('Logo  beside it')
  })
})

describe("an excerpt keeps blocks and controls out of its row", () => {
  it('a task-list item draws no checkbox', () => {
    const { container } = render(<Markdown inline>{'- [ ] buy milk\n- [x] pay rent'}</Markdown>)
    expect(container.querySelector('input')).toBeNull()
    expect(container.textContent).toContain('buy milk')
    expect(container.textContent).toContain('pay rent')
  })

  it('a footnote draws no section', () => {
    const { container } = render(<Markdown inline>{'Read the digest[^1] first.\n\n[^1]: It ships on Fridays.'}</Markdown>)
    expect(container.querySelector('section')).toBeNull()
    expect(container.textContent).toContain('Read the digest')
  })

  it('under a line-clamp, the flow stays inline', () => {
    const body = '## Plan\n\n- [ ] read the notes[^1]\n- ship it\n\n> quoted\n\n[^1]: The notes are short.'
    const { container } = render(<p className="line-clamp-2"><Markdown inline>{body}</Markdown></p>)
    const clamp = container.querySelector('p.line-clamp-2')!
    expect(clamp.querySelector(BLOCKS), 'a block escapes the clamp').toBeNull()
    expect(clamp.querySelector('input'), 'a control in the row').toBeNull()
  })
})

describe('breaks: text written as lines keeps its lines', () => {
  it('a soft line ending is a break with `breaks`', () => {
    const { container } = render(<Markdown breaks>{'one\ntwo'}</Markdown>)
    expect(container.querySelectorAll('br')).toHaveLength(1)
    // The break carries the line ending beside it, as a hard break does.
    expect(container.textContent?.replace(/\s+/g, ' ')).toBe('one two')
  })

  it('and none without it, as a markdown document reads', () => {
    expect(render(<Markdown>{'one\ntwo'}</Markdown>).container.querySelector('br')).toBeNull()
  })

  it('a code block keeps its own lines either way', () => {
    const { container } = render(<Markdown breaks>{'Run:\n\n```sh\nmake\nmake test\n```'}</Markdown>)
    expect(container.querySelector('pre br')).toBeNull()
    expect(container.querySelector('pre')?.textContent).toContain('make\nmake test')
  })
})

describe("hits: the search index's marks stay marked", () => {
  const marks = (el: Element) => Array.from(el.querySelectorAll('mark')).map((m) => m.textContent)

  it('a hit inside a code span is a mark inside the code', () => {
    const { container } = render(<Markdown line hits>{'run `make <<test>> now` here'}</Markdown>)
    expect(container.querySelector('code mark')?.textContent).toBe('test')
    expect(container.textContent).toBe('run make test now here')
  })

  it('a hit inside bold, and a hit across a bold run, are marked', () => {
    const { container } = render(<Markdown line hits>{'**big <<win>>** and <<**whole run**>> too'}</Markdown>)
    expect(container.querySelector('strong mark')?.textContent).toBe('win')
    expect(marks(container)).toEqual(['win', 'whole run'])
    expect(container.textContent).toBe('big win and whole run too')
  })

  it('an unpaired mark stays text', () => {
    const { container } = render(<Markdown line hits>{'a << b, and c > d'}</Markdown>)
    expect(container.querySelector('mark')).toBeNull()
    expect(container.textContent).toBe('a << b, and c > d')
  })

  it.each([
    ['a shift before a hit', 'std::cout << x; <<printf>> works', ['printf'], 'std::cout << x; printf works'],
    ['a heredoc before a hit', 'cat <<EOF then <<grep>>', ['grep'], 'cat <<EOF then grep'],
  ])('a literal << before a hit stays text, and marks only the hit: %s', (_what, line, hit, words) => {
    const { container } = render(<Markdown line hits>{line}</Markdown>)
    expect(marks(container)).toEqual(hit)
    expect(container.textContent).toBe(words)
  })

  it('a planted tag inside a hit stays text', () => {
    const { container } = render(<Markdown line hits>{'see <<<img src=x onerror=alert(1)>>> now'}</Markdown>)
    expect(inert(container)).toEqual([])
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>')
    expect(container.querySelector('mark')).not.toBeNull()
  })

  it('without `hits` the marks are the text they are', () => {
    const { container } = render(<Markdown line>{'a <<hit>> b'}</Markdown>)
    expect(container.querySelector('mark')).toBeNull()
  })
})
