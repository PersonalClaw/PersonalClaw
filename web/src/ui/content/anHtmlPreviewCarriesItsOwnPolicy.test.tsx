import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ImageFilePreview, RawHtmlPreview } from './renderers'

// ── A preview loads nothing from another site on its own ────────────────────────────────────
//
// An HTML file's preview is its own document, in a sandboxed frame. It carries a policy of its own
// ahead of its content, so an image or a request it names is refused by the frame itself, not only
// through the policy it would inherit from the page. An image file's preview shows a picture from
// this gateway or from data the page holds, and an address on another site as a placeholder.

let made: Blob[] = []

beforeEach(() => {
  made = []
  URL.createObjectURL = vi.fn((blob: Blob) => {
    made.push(blob)
    return `blob:http://localhost/preview-${made.length}`
  })
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  vi.restoreAllMocks()
})

const policyOf = (html: string) =>
  /<meta http-equiv="Content-Security-Policy" content="([^"]*)">/.exec(html)?.[1] ?? ''

/** Every image source in *root* the browser would load from another origin. */
function offOrigin(root: HTMLElement): string[] {
  return Array.from(root.querySelectorAll('img'))
    .map((img) => img.getAttribute('src') ?? '')
    .filter((src) => {
      const u = new URL(src, window.location.href)
      return u.protocol !== 'data:' && u.protocol !== 'blob:' && u.origin !== window.location.origin
    })
}

describe('an HTML preview carries its own policy', () => {
  it('ahead of its content, refusing images and requests from anywhere else', async () => {
    const content = '<p>Report</p><img src="https://images.example.com/p.png"><script>fetch("https://x.example")</script>'
    render(<RawHtmlPreview content={content} mode="light" title="Report" />)
    expect(made).toHaveLength(1)
    const html = await made[0].text()
    const policy = policyOf(html)
    expect(policy).toContain("default-src 'none'")
    expect(policy).toContain('img-src data: blob:')
    expect(policy).toContain("connect-src 'none'")
    expect(policy).toContain("form-action 'none'")
    expect(policy).toContain("base-uri 'none'")
    expect(policy).toContain('font-src data:')
    expect(policy).not.toContain('https:')
    expect(html.indexOf('Content-Security-Policy')).toBeLessThan(html.indexOf('<p>Report</p>'))
    // The host of a link in it is looked up by nobody before it is opened.
    expect(html.indexOf('<meta http-equiv="x-dns-prefetch-control" content="off">')).toBe(0)
  })

  it('after a doctype, so the document keeps its mode', async () => {
    render(<RawHtmlPreview content={'<!DOCTYPE html><html><head><title>T</title></head><body>B</body></html>'} mode="light" title="T" />)
    const html = await made[0].text()
    expect(html.startsWith('<!DOCTYPE html><meta http-equiv="x-dns-prefetch-control" content="off"><meta http-equiv="Content-Security-Policy"')).toBe(true)
  })

  it('after a doctype that a comment or a processing instruction comes before', async () => {
    const content = '<!-- generated --><?xml version="1.0"?>\n<!DOCTYPE html><html><body>B</body></html>'
    render(<RawHtmlPreview content={content} mode="light" title="T" />)
    const html = await made[0].text()
    const doctype = html.indexOf('<!DOCTYPE html>')
    expect(doctype).toBeGreaterThanOrEqual(0)
    expect(html.indexOf('Content-Security-Policy')).toBeGreaterThan(doctype)
    expect(html.indexOf('Content-Security-Policy')).toBeLessThan(html.indexOf('<body>'))
  })
})

describe('the policy is in the document head, however its text begins', () => {
  it.each([
    ['a comment HTML ends at its first >', '<!--><img src="https://images.example.com/p.png">--><!doctype html><p>B</p>'],
    ['a processing instruction, which HTML ends at its first >', '<?x><img src="https://images.example.com/p.png">?><!doctype html><p>B</p>'],
    ['a character HTML reads as text', '\u000b<!doctype html><img src="https://images.example.com/p.png">'],
  ])('%s', async (_what, content) => {
    render(<RawHtmlPreview content={content} mode="light" title="T" />)
    const doc = new DOMParser().parseFromString(await made[0].text(), 'text/html')
    const policy = doc.querySelector('meta[http-equiv="Content-Security-Policy"]')
    expect(policy?.parentElement).toBe(doc.head)
    const img = doc.querySelector('img')
    if (img && policy) {
      expect(policy.compareDocumentPosition(img) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
  })
})

describe("an image file's preview shows only this gateway's or the page's own pictures", () => {
  it.each([
    ['an artifact image', '/api/artifacts/chart/raw'],
    ['a data image', 'data:image/png;base64,iVBORw0KGgo='],
  ])('%s is drawn', (_what, src) => {
    const { container } = render(<ImageFilePreview content={src} path="" mode="light" title="Image" />)
    // A path on this gateway is drawn as the whole address it parses to.
    const expected = src.startsWith('data:') ? src : new URL(src, window.location.href).href
    expect(container.querySelector('img')?.getAttribute('src')).toBe(expected)
  })

  it.each([
    ['https', 'https://images.example.com/p.png'],
    ['protocol-relative', '//images.example.com/p.png'],
    ['a backslash after the slash', '/\\images.example.com/p.png'],
    ['a tab after the slash', '/\t/images.example.com/p.png'],
  ])('an address on another site (%s) is a placeholder that loads it on a press', (_what, src) => {
    const { container } = render(<ImageFilePreview content={src} path="" mode="light" title="Image" />)
    expect(offOrigin(container)).toEqual([])
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByRole('button', { name: /load image from images\.example\.com/i })).toBeTruthy()
  })

  it('a path on this gateway that starts with another host is still a path on this gateway', () => {
    const { container } = render(<ImageFilePreview content={`${window.location.origin}//evil.example/x.png`} path="" mode="light" title="Image" />)
    expect(offOrigin(container)).toEqual([])
    expect(screen.queryByRole('button', { name: /evil\.example/i })).toBeNull()
  })

  it('a plain-http address is shown as its words, with no Load', () => {
    const { container } = render(<ImageFilePreview content="http://images.example.com/p.png" path="" mode="light" title="Image" />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('http://images.example.com/p.png')
    expect(screen.queryByRole('button', { name: /load image/i })).toBeNull()
  })
})
