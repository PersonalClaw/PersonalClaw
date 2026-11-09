import { describe, it, expect } from 'vitest'
import { sanitizeInlineHtml } from './sanitize'

// ── A preview keeps no aria-modal attribute from its markup ─────────────────────────────────
//
// Document and SVG previews drop `aria-modal` from their markup, in both profiles. Every other
// `aria-*` attribute is kept as written.

describe('a preview keeps no aria-modal attribute from its markup', () => {
  it('a document keeps its role and its label, without aria-modal', () => {
    const out = sanitizeInlineHtml('<div role="region" aria-modal="true" aria-label="l">w</div>', 'document')
    expect(out).toBe('<div role="region" aria-label="l">w</div>')
  })

  it('an SVG keeps its role and its label, without aria-modal', () => {
    const out = sanitizeInlineHtml(
      '<svg xmlns="http://www.w3.org/2000/svg" role="region" aria-modal="true" aria-label="l" viewBox="0 0 2 2"><g aria-modal="true"/></svg>',
      'svg',
    )
    expect(out).not.toMatch(/aria-modal/i)
    expect(out).toContain('role="region"')
    expect(out).toContain('aria-label="l"')
  })

  it('aria-modal in another case is dropped too', () => {
    const out = sanitizeInlineHtml('<svg xmlns="http://www.w3.org/2000/svg" ARIA-MODAL="true" viewBox="0 0 2 2"/>', 'svg')
    expect(out).not.toMatch(/aria-modal/i)
    expect(out).toContain('viewBox="0 0 2 2"')
  })

  it('the other aria and data attributes are kept as written', () => {
    const html = '<p aria-label="l" aria-hidden="true" aria-describedby="d" aria-expanded="false" data-k="v">w</p>'
    expect(sanitizeInlineHtml(html, 'document')).toBe(html)
  })
})
