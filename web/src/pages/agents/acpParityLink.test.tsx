/** The discovered-agents inspector must link the ACP parity statement (AAP-10, SC #7).
 *
 * The parity doc (`docs/agents/acp-parity.md`) says, per provider, what is at parity, what the
 * host compensates for, and what is a protocol/CLI constraint. Its whole purpose is that a user
 * DECIDING which agent to bind can see those boundaries — and that decision is made in the agents
 * UI, not in a repository README. AAP-10's audit was `partial` on exactly this: the READMEs linked
 * the doc, the discovered-agent panel did not, so the last few metres to the reader were missing.
 *
 * This pins the UI half: the read-only inspector for a provider-run agent carries an EXTERNAL link
 * to the parity doc, provider-aware in its label. It renders the pure component directly — no API,
 * router, or context — so it is a faithful check of what the panel actually shows.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { DiscoveredAgentDetail } from './AgentDetail'
import { repoDocUrl } from '../../lib/repoDocs'
import type { DiscoveredAgent } from '../../lib/api'

afterEach(cleanup)

const agent: DiscoveredAgent = {
  id: 'claude-code:reviewer',
  name: 'reviewer',
  runtime: 'claude-code',
  description: 'Reviews diffs.',
  provider_agent: 'reviewer',
  reasoning_effort: '',
  models: [],
}

describe('the discovered-agent inspector links the ACP parity doc', () => {
  it('renders an external link to docs/agents/acp-parity.md', () => {
    render(<DiscoveredAgentDetail agent={agent} providerId="claude-code" />)
    const link = screen.getByRole('link', { name: /parity/i }) as HTMLAnchorElement
    // The reader-facing deep link, not a roadmap record: it resolves to the tree doc,
    // composed from the app's one canonical blob root (lib/repoDocs).
    expect(link.getAttribute('href')).toBe(repoDocUrl('docs/agents/acp-parity.md'))
    // EXTERNAL: opens off-app, and must carry the reverse-tabnabbing guard.
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel') ?? '').toContain('noopener')
    expect(link.getAttribute('rel') ?? '').toContain('noreferrer')
  })

  it('names the provider so the notes read as that runtime’s boundary', () => {
    render(<DiscoveredAgentDetail agent={agent} providerId="claude-code" />)
    // providerMeta('claude-code') → "Claude Code"; the label ties the doc to THIS runtime.
    expect(screen.getByRole('link', { name: /Claude Code/i })).toBeTruthy()
    expect(screen.getByText(/Capability parity/i)).toBeTruthy()
  })
})
