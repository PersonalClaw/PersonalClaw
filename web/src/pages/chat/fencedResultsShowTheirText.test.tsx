import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolCard } from './ToolCard'
import type { ToolSegment } from './chatTypes'

// ── A tool card shows the result's text, not the model's safety markers ─────────────────────
//
// Measured before this: a web search card listed each of its ten hits as
// "<untrusted_content source=web_search> Changelog — … </untrusted_content> https://…". The
// markers tell the MODEL that the text is data; the result keeps them on its way there, and the
// card takes them off where the text is shown.

const fence = (text: string, source = 'web_search') =>
  `<untrusted_content source=${source}>\n${text}\n</untrusted_content>`

async function open(seg: ToolSegment) {
  render(<ToolCard seg={seg} />)
  await userEvent.setup().click(screen.getByRole('button'))
  return document.body.textContent ?? ''
}

describe('a fenced tool result', () => {
  it('lists each web search hit by its title, with no marker around it', async () => {
    const output = JSON.stringify({
      query: 'what changed in libfoo 2.4.1',
      results: [
        { title: fence('Changelog — libfoo 2.4 documentation'), url: 'https://docs.example.com/libfoo/changelog/', snippet: fence('Fixes a crash on empty feeds.') },
        { title: fence('Release notes <b>2.4.1</b>'), url: 'https://example.org/libfoo/releases/', snippet: fence('Two fixes.') },
      ],
    })
    const shown = await open({ kind: 'tool', id: 'tc-1', tool: 'web_search', input: '{"query": "what changed in libfoo 2.4.1"}', output, done: true })

    expect(shown).not.toContain('untrusted_content')
    expect(screen.getByText('Changelog — libfoo 2.4 documentation')).toBeTruthy()
    // A title's markup-like text is its text: shown as written, never made into markup.
    expect(screen.getByText('Release notes <b>2.4.1</b>')).toBeTruthy()
    expect(document.querySelector('b')).toBeNull()
    expect(shown).toContain('https://docs.example.com/libfoo/changelog/')
  })

  it('shows a fetched page as its text, with no marker around it', async () => {
    const output = fence('Changelog\n\nVersion 2.4.1 fixes a crash on empty feeds.', 'https://docs.example.com/libfoo/changelog/')
    const shown = await open({ kind: 'tool', id: 'tc-2', tool: 'web_fetch', input: '{"url": "https://docs.example.com/libfoo/changelog/"}', output, done: true })

    expect(shown).not.toContain('untrusted_content')
    expect(shown).toContain('Version 2.4.1 fixes a crash on empty feeds.')
  })

  it('shows any other tool’s fenced result as its text', async () => {
    const output = fence('Subject: Your talk is accepted\n\nPlease send the abstract by Friday.', 'mail')
    const shown = await open({ kind: 'tool', id: 'tc-3', tool: 'mail_read', output, done: true })

    expect(shown).not.toContain('untrusted_content')
    expect(shown).toContain('Please send the abstract by Friday.')
  })
})
