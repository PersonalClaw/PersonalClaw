import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { DocumentPreview, SvgPreview } from './renderers'
import { copyDocumentHtml, exportDocumentHtml } from './exporters'

// ── Document and SVG previews drop class attributes ────────────────────────────────────────
//
// A document or SVG preview keeps no class attribute in its markup: only the wrapper the
// preview itself renders has one. A document's export and its copied HTML hold none either.

const NAMES = 'doc p-l flex'

/** Every element inside the preview's wrapper (the wrapper itself excluded). */
function content(container: HTMLElement): Element[] {
  const wrapper = container.firstElementChild
  expect(wrapper, 'the preview rendered its wrapper').not.toBeNull()
  return Array.from(wrapper!.querySelectorAll('*'))
}

const DOC = `<div class="${NAMES}"><h2 class="${NAMES}">Notes</h2><p class="flex">Body text</p>`
  + `<svg class="${NAMES}" viewBox="0 0 4 4"><rect class="flex" width="4" height="4"/></svg></div>`
const SVG = `<svg xmlns="http://www.w3.org/2000/svg" class="${NAMES}" viewBox="0 0 10 10">`
  + `<g class="${NAMES}"><rect class="flex" width="10" height="10" fill="#123456"/></g></svg>`

describe('a document preview keeps no class attribute', () => {
  it('renders the markup’s elements without their classes', () => {
    const { container } = render(<DocumentPreview content={DOC} mode="light" title="Notes" />)
    const els = content(container)
    expect(container.firstElementChild, 'the preview’s own wrapper keeps its classes').toHaveClass('doc')
    // Control: the markup itself survived, so an empty preview cannot pass.
    expect(els.map((e) => e.tagName.toLowerCase())).toEqual(['div', 'h2', 'p', 'svg', 'rect'])
    expect(container.textContent).toContain('Body text')
    for (const el of els) expect(el.hasAttribute('class'), `<${el.tagName.toLowerCase()}>`).toBe(false)
  })
})

describe('an SVG preview keeps no class attribute', () => {
  it('renders the SVG’s elements without their classes', () => {
    const { container } = render(<SvgPreview content={SVG} mode="light" title="A drawing" />)
    const els = content(container)
    expect(els.map((e) => e.tagName.toLowerCase())).toEqual(['svg', 'g', 'rect'])
    expect(els[2].getAttribute('fill')).toBe('#123456')
    for (const el of els) expect(el.hasAttribute('class'), `<${el.tagName.toLowerCase()}>`).toBe(false)
  })
})

describe('a document’s exports keep no class attribute', () => {
  it('the exported file holds the document without its classes', async () => {
    const blobs: Blob[] = []
    const prev = [URL.createObjectURL, URL.revokeObjectURL] as const
    URL.createObjectURL = vi.fn((b: Blob) => { blobs.push(b); return 'blob:captured' }) as unknown as typeof URL.createObjectURL
    URL.revokeObjectURL = (() => {}) as unknown as typeof URL.revokeObjectURL
    try {
      exportDocumentHtml(DOC, 'Notes')
    } finally {
      URL.createObjectURL = prev[0]
      URL.revokeObjectURL = prev[1]
    }
    expect(blobs, 'exportDocumentHtml produced one download').toHaveLength(1)
    const html = await blobs[0].text()
    expect(html).toContain('<main>')
    const main = html.slice(html.indexOf('<main>'), html.indexOf('</main>'))
    expect(main).toContain('Body text')
    expect(main).not.toMatch(/\sclass=/)
  })

  it('the copied HTML holds the document without its classes', async () => {
    const write = vi.fn(async (_text: string) => {})
    const before = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true })
    try {
      await copyDocumentHtml(DOC)
    } finally {
      if (before) Object.defineProperty(navigator, 'clipboard', before)
      else delete (navigator as { clipboard?: unknown }).clipboard
    }
    const copied = write.mock.calls[0]?.[0] ?? ''
    expect(copied).toContain('Body text')
    expect(copied).not.toMatch(/\sclass=/)
  })
})
