import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { Markdown } from './Markdown'
import type { MemoryCitation } from '../pages/chat/chatTypes'

// Inline `[Memory N]` citation chips (MEMORY-GRAPH-AND-VAULT §5.4). The renderer
// turns a `[Memory N]` token into a deep-link to the cited episode when the turn's
// manifest resolves N → a record id; otherwise it degrades to plain text so a
// hallucinated or id-less citation is never a broken link.
describe('Markdown memory citations', () => {
  const cites: MemoryCitation[] = [
    { n: 1, id: '42', preview: 'deployed billing on friday' },
    { n: 2, id: '7', preview: 'runbook path' },
  ]

  it('renders a resolvable [Memory N] as a deep-link chip', () => {
    const { getByRole } = render(
      <Markdown citations={cites}>{'You shipped it [Memory 1] last week.'}</Markdown>,
    )
    const link = getByRole('link', { name: /Memory 1/ })
    expect(link).toHaveAttribute('href', '#/settings/memory?tab=studio&sel=epi%3A42')
    expect(link).toHaveAttribute('title', 'deployed billing on friday')
  })

  it('leaves an unresolvable [Memory N] as plain text (no link)', () => {
    const { container, queryByRole } = render(
      <Markdown citations={cites}>{'Nothing matches [Memory 9] here.'}</Markdown>,
    )
    expect(queryByRole('link')).toBeNull()
    expect(container.textContent).toContain('[Memory 9]')
  })

  it('leaves a citation with no record id as plain text', () => {
    const { container, queryByRole } = render(
      <Markdown citations={[{ n: 1, id: null }]}>{'See [Memory 1].'}</Markdown>,
    )
    expect(queryByRole('link')).toBeNull()
    expect(container.textContent).toContain('[Memory 1]')
  })

  it('does not touch [Memory N] tokens when no manifest is supplied', () => {
    const { container, queryByRole } = render(<Markdown>{'Plain [Memory 1] token.'}</Markdown>)
    expect(queryByRole('link')).toBeNull()
    expect(container.textContent).toContain('[Memory 1]')
  })
})
