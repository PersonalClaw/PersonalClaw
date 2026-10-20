import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { Markdown } from './Markdown'
import type { MemoryCitation } from '../pages/chat/chatTypes'

// Inline `[Memory N]` citation chips. The renderer
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

// A lesson recalled into the turn is cited as `[Lesson N]` and opens THAT lesson in Memory. The
// reply could only say "Source: [Learned corrections]" before: the block gave a lesson nothing
// to cite. Numbered apart from the episodes, so `[Lesson 1]` and `[Memory 1]` are two sources.
describe('Markdown lesson citations', () => {
  const rule = 'carrier-webhooks dedupes in Postgres, the LRU is only a fast path.'
  const cites: MemoryCitation[] = [
    { n: 1, id: '42', preview: 'deployed billing on friday' },
    { kind: 'lesson', n: 1, id: rule, preview: rule },
  ]

  it('renders a resolvable [Lesson N] as a chip that opens the lesson', () => {
    const { getByRole } = render(
      <Markdown citations={cites}>{'Dedupe keys live in Postgres [Lesson 1].'}</Markdown>,
    )
    const link = getByRole('link', { name: /Lesson 1/ })
    expect(link).toHaveAttribute(
      'href', `#/settings/memory?tab=studio&sel=${encodeURIComponent(`lesson:${rule}`)}`)
    expect(link).toHaveAttribute('title', rule)
  })

  it('keeps a lesson and an episode with the same number apart', () => {
    const { getByRole } = render(
      <Markdown citations={cites}>{'Shipped [Memory 1], and dedupes in Postgres [Lesson 1].'}</Markdown>,
    )
    expect(getByRole('link', { name: /Memory 1/ })).toHaveAttribute(
      'href', '#/settings/memory?tab=studio&sel=epi%3A42')
    expect(getByRole('link', { name: /Lesson 1/ }).getAttribute('href')).toContain('lesson%3A')
  })

  it('leaves a [Lesson N] the manifest does not hold as plain text', () => {
    const { container, queryByRole } = render(
      <Markdown citations={cites}>{'See [Lesson 4].'}</Markdown>,
    )
    expect(queryByRole('link', { name: /Lesson/ })).toBeNull()
    expect(container.textContent).toContain('[Lesson 4]')
  })
})
