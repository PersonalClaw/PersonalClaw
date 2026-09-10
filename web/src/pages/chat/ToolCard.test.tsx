import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToolCard } from './ToolCard'
import type { ToolSegment, AgentError } from './chatTypes'

// PLATFORM-LEGIBILITY §2 — the FE half of the AgentError envelope. A failed tool call can
// carry the coded WHAT/WHY/FIX envelope on its segment (`agentError`, shape at
// ToolCard.tsx:80-107); the card must SHOW it — the three labeled lines, the stable code,
// and the did-you-mean pills — so the same failure the model reads is legible to the user.
// This is the render observability the backend seam produces evidence for; without it the
// envelope reaches the wire and dies unshown.

const envelope: AgentError = {
  code: 'ERR_ACTION_PROVIDER_FAILED',
  what: "action provider 'webhook' failed: RuntimeError: connection refused",
  why: 'the provider raised an exception instead of returning a result',
  fix: "check the action config against the provider's expected fields, or inspect the provider's logs",
  suggestions: ['create-task', 'run-script'],
}

const seg = (over: Partial<ToolSegment> = {}): ToolSegment => ({
  kind: 'tool', id: 't1', tool: 'webhook', done: true, ok: false, ...over,
})

/** The envelope lives in the EXPANDED body, behind the disclosure — open it as a user does. */
const expand = () => fireEvent.click(screen.getByRole('button', { name: /expand details/i }))

describe('ToolCard — the AgentError envelope render', () => {
  it('shows the code, the What/Why/Fix lines and the suggestion pills when expanded', () => {
    render(<ToolCard seg={seg({ agentError: envelope })} />)

    // Gated behind the disclosure: nothing of the envelope is on screen until it is opened.
    expect(screen.queryByText(envelope.why)).toBeNull()

    expand()

    // The stable code an agent (and the user) branch on.
    expect(screen.getByText('ERR_ACTION_PROVIDER_FAILED')).toBeInTheDocument()
    // The three labeled lines — labels AND their concrete values.
    expect(screen.getByText('What')).toBeInTheDocument()
    expect(screen.getByText(envelope.what)).toBeInTheDocument()
    expect(screen.getByText('Why')).toBeInTheDocument()
    expect(screen.getByText(envelope.why)).toBeInTheDocument()
    expect(screen.getByText('Fix')).toBeInTheDocument()
    expect(screen.getByText(envelope.fix)).toBeInTheDocument()
    // The did-you-mean pills — each suggestion is its own branchable chip.
    expect(screen.getByText(/did you mean/i)).toBeInTheDocument()
    for (const s of envelope.suggestions!) {
      expect(screen.getByText(s)).toBeInTheDocument()
    }
  })

  it('omits the envelope block entirely when the segment carries no agentError', () => {
    // The vacuity guard: the block is CONDITIONAL on `agentError`, not always painted. A card
    // for a failed call with no envelope must not invent What/Why/Fix or a code.
    render(<ToolCard seg={seg()} />)
    expand()
    expect(screen.queryByText('ERR_ACTION_PROVIDER_FAILED')).toBeNull()
    expect(screen.queryByText(envelope.why)).toBeNull()
    expect(screen.queryByText('Why')).toBeNull()
  })

  it('renders the What/Why/Fix lines but no pill row when there are no suggestions', () => {
    // The pill row is gated on a non-empty suggestions list (ToolCard.tsx:104) — an envelope
    // without did-you-mean candidates still shows its three lines, and shows no empty header.
    const { suggestions: _drop, ...noSuggest } = envelope
    render(<ToolCard seg={seg({ agentError: noSuggest })} />)
    expand()
    expect(screen.getByText(envelope.what)).toBeInTheDocument()
    expect(screen.getByText(envelope.fix)).toBeInTheDocument()
    expect(screen.queryByText(/did you mean/i)).toBeNull()
  })
})
