import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { renderToolOutput } from './registry'
import type { ToolSegment } from '../chatTypes'

// ── A web search that fell back says so on its card ─────────────────────────────────────────────
//
// When the search provider a search was sent to fails (a refused key, a spent quota), the search is
// run again through the keyless engine. It used to come back as though the first provider had
// answered. The result now carries a `fallback` notice in PersonalClaw's words, which the agent
// reads, and the card shows it above the results.

afterEach(() => cleanup())

const NOTICE = 'The search through Brave Search failed, so these results come from DuckDuckGo instead.'

function seg(payload: unknown): ToolSegment {
  return {
    kind: 'tool', id: 't1', tool: 'web_search', input: JSON.stringify({ query: 'feedparser 6.0.12' }),
    output: JSON.stringify(payload), done: true,
  }
}

const RESULTS = [{ url: 'https://docs.example/changelog', title: 'Changelog' }]

describe('the web search card', () => {
  it('says where the results came from when the search fell back', () => {
    render(<>{renderToolOutput(seg({ results: RESULTS, provider: 'duckduckgo', fallback: { provider: 'brave', notice: NOTICE, reason: 'x' } }))}</>)
    const note = screen.getByRole('note')
    expect(note.textContent).toContain(NOTICE)
    expect(note.textContent).toContain('Settings › Search')
    expect(screen.getByText('Changelog')).toBeTruthy()
  })

  it('says nothing extra when the provider answered', () => {
    render(<>{renderToolOutput(seg({ results: RESULTS, provider: 'brave' }))}</>)
    expect(screen.queryByRole('note')).toBeNull()
    expect(screen.getByText('Changelog')).toBeTruthy()
  })
})
