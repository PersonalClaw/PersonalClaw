import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'

// ── A diagram keeps no aria-modal attribute from its labels ─────────────────────────────────
//
// A rendered diagram's SVG, its root and its HTML labels included, keeps no `aria-modal`
// attribute. Every other `aria-*` attribute is kept.

const SVG = '<svg xmlns="http://www.w3.org/2000/svg" aria-modal="true" aria-label="l" viewBox="0 0 10 10">'
  + '<foreignObject width="10" height="10"><div xmlns="http://www.w3.org/1999/xhtml">'
  + '<span aria-modal="true" aria-label="l">A</span></div></foreignObject></svg>'

vi.mock('mermaid', () => ({
  default: { initialize: vi.fn(), render: vi.fn(async () => ({ svg: SVG })) },
}))

import { MermaidBlock } from './MermaidBlock'

describe('a diagram keeps no aria-modal attribute from its labels', () => {
  it('renders the diagram without aria-modal, on its root or in a label, and its label sanitizer forbids it', async () => {
    const { container } = render(<MermaidBlock code="graph TD; A" />)
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    // Control: the rendered SVG and its label are on the page, with their other attributes.
    expect(container.querySelectorAll('[aria-label="l"]')).toHaveLength(2)
    expect(container.textContent).toContain('A')
    expect(container.querySelectorAll('[aria-modal]')).toHaveLength(0)
    const { default: mermaid } = await import('mermaid')
    expect(mermaid.initialize).toHaveBeenCalledWith(expect.objectContaining({
      securityLevel: 'strict',
      dompurifyConfig: { FORBID_TAGS: ['style'], FORBID_ATTR: ['aria-modal'] },
    }))
  })
})
