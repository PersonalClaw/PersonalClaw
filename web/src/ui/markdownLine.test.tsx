import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { MarkdownLine } from './MarkdownLine'
import { Markdown } from './Markdown'
import { HOSTILE, MD_LINE, inert, plain, rendersLine } from '../test/markdownSinks'

// ── MarkdownLine: the sink element, with the line rendered inside ─────────────────────────────
//
// A one-line field lives in a sink that cuts it (`truncate`, `line-clamp`) and often sits inside a
// click target. The line renders inside the sink element itself, so the sink's cut clips the
// rendered row; it draws no anchor and no control, so a row's button holds no control of its own;
// and where it cuts, its tooltip is the whole line as plain text.

/** Interactive elements inside `el` (not `el` itself). */
const interactiveInside = (el: Element) =>
  Array.from(el.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')).map((n) => n.tagName.toLowerCase())

describe('MarkdownLine', () => {
  it('is the sink element, with its class, and the line rendered inside', () => {
    const { container } = render(<MarkdownLine as="h3" className="truncate text-on-surface" text={MD_LINE} />)
    const sink = container.firstElementChild!
    expect(sink.tagName).toBe('H3')
    expect(sink.className).toBe('truncate text-on-surface')
    expect(rendersLine(sink)).toEqual([])
  })

  it('a sink that cuts the line has the whole line as plain text for its tooltip', () => {
    const { container } = render(<MarkdownLine className="line-clamp-2" text={MD_LINE} />)
    const title = container.firstElementChild!.getAttribute('title') ?? ''
    expect(title).toBe('Skip model_calls in export_digest() in published.py')
    expect(plain(title)).toEqual([])
  })

  it('a sink that does not cut has no tooltip, unless one is passed; false is none', () => {
    expect(render(<MarkdownLine text={MD_LINE} />).container.firstElementChild!.hasAttribute('title')).toBe(false)
    expect(render(<MarkdownLine text={MD_LINE} tooltip="The digest step" />).container.firstElementChild!.getAttribute('title')).toBe('The digest step')
    expect(render(<MarkdownLine className="truncate" text={MD_LINE} tooltip={false} />).container.firstElementChild!.hasAttribute('title')).toBe(false)
  })

  it('a leading mark is the text it is, in the row and in its tooltip', () => {
    const { container } = render(<MarkdownLine className="truncate" text="# of retries" />)
    expect(container.textContent).toBe('# of retries')
    expect(container.firstElementChild!.getAttribute('title')).toBe('# of retries')
  })

  it('hits mark the row, and the tooltip drops the marks', () => {
    const { container } = render(<MarkdownLine className="truncate" hits text="find `the <<digest>>` step" />)
    expect(container.querySelector('code mark')?.textContent).toBe('digest')
    expect(container.firstElementChild!.getAttribute('title')).toBe('find the digest step')
  })

  it('a literal << before a hit stays in the tooltip, and only the hit loses its marks', () => {
    const { container } = render(<MarkdownLine className="truncate" hits text="std::cout << x; <<printf>> works" />)
    expect(container.firstElementChild!.getAttribute('title')).toBe('std::cout << x; printf works')
  })

  it('nothing, or only space, draws the sink empty', () => {
    expect(render(<MarkdownLine as="li" text={null} />).container.innerHTML).toBe('<li></li>')
    expect(render(<MarkdownLine text="   " />).container.textContent).toBe('')
  })

  it('a hostile line is inert inside its sink', () => {
    const { container } = render(<MarkdownLine as="div" className="truncate" text={HOSTILE} />)
    expect(inert(container)).toEqual([])
  })
})

describe('a line inside a button holds no control of its own', () => {
  const LINKED = 'see [the docs](https://example.com/docs) and `make test`'

  it('MarkdownLine inside a button: no interactive descendant', () => {
    const { container } = render(<button type="button"><MarkdownLine text={LINKED} /></button>)
    expect(interactiveInside(container.querySelector('button')!)).toEqual([])
    expect(container.textContent).toBe('see the docs and make test')
  })

  it('plant: block-mode Markdown holding a link, inside a button, is caught', () => {
    const { container } = render(<button type="button"><Markdown>{LINKED}</Markdown></button>)
    expect(interactiveInside(container.querySelector('button')!)).toEqual(['a'])
  })
})
