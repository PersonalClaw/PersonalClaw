import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import type { ApprovalSegment } from './chatTypes'

// ── #541 clause 1 + #506 on the approval card ──────────────────────────────────────────
//
// TWO defects, one rule: a card that promises a persisted grant must actually make one, and
// risk must gate the control rather than tint a chip beside it.
//
// #541 — "This agent" promised *"Saved on this agent: every tool runs without asking, in this
// chat and future ones."* unconditionally. The backend degrades that grant to session scope
// for a reserved system agent, for a name with no profile, and for an ACP-bound chat (which
// never sets `session.agent`), and said so only to `logger.info`. The card had no notion of a
// non-persistable agent, so "future ones" was unfalsifiable from the outside.
//
// The fix chosen is the SECOND of the two the issue offered: the card is changed to say what
// the code does, NOT the write path changed to always persist. Persisting anyway would have to
// either overwrite a reserved agent's deliberately fixed config or invent a profile for a name
// that has none — so the backend is right and the sentence was wrong. The backend now reports
// the grant target at prompt time (`perm_meta.grant_agent`) and what actually happened at
// decision time (the approve response's `grant`), and the promise is derived from the former.
//
// #506 — the risk chip was "purely INFORMATIONAL" by its own comment, so widening to a
// standing grant cost exactly the same on `destructive` as on `safe`. A standing grant is what
// auto-approves every LATER call without asking, so that is the control risk has to gate.
//
// Asserted through the picker and the Allow control as a user reaches them — never by reading
// the source for a string, which passes with the control deleted.

const seg = (over: Partial<ApprovalSegment> = {}): ApprovalSegment => ({
  kind: 'approval', id: 'a1', tool: 'bash', ...over,
})

const scopeTab = (label: string) => screen.getByRole('radio', { name: label })
const maybeScopeTab = (label: string) => screen.queryByRole('radio', { name: label })
const allow = () => screen.getByRole('button', { name: /^Allow bash/ })

describe('#541 — the "This agent" promise names the agent the grant will be saved on', () => {
  it('names the resolved agent when the grant will persist', () => {
    const { container } = render(<ApprovalCard seg={seg({ grantAgent: 'researcher' })} onAct={() => {}} />)
    fireEvent.click(scopeTab('This agent'))
    expect(container.textContent).toContain('Saved on researcher')
    // The claim the whole issue is about, made only when it is true.
    expect(container.textContent).toContain('future ones')
  })

  it('does NOT promise future chats when the backend cannot persist the grant', () => {
    // `grantAgent: ''` is what the backend sends for a reserved system agent, an unknown
    // name, or an ACP-bound chat — the three cases that take the session-scope branch.
    const { container } = render(<ApprovalCard seg={seg({ grantAgent: '' })} onAct={() => {}} />)
    fireEvent.click(scopeTab('This agent'))
    expect(container.textContent).not.toMatch(/future ones/i)
    expect(container.textContent).not.toMatch(/Saved on/i)
    // And it says what WILL happen, since the grant still trusts this chat.
    expect(container.textContent).toMatch(/this chat only/i)
  })

  it('treats an absent grantAgent as non-persistable, not as persistable', () => {
    // An older session rehydrated by a build that never sent the field, or a config read that
    // failed. The safe direction for a PROMISE is to claim less, so absence must not fall
    // through to the "Saved on …" wording — which is exactly how the original bug read.
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    fireEvent.click(scopeTab('This agent'))
    expect(container.textContent).not.toMatch(/Saved on|future ones/i)
  })

  it('carries the honest promise into the Allow control\'s accessible name', () => {
    // The name is what a screen-reader user consents to, so the two must not diverge: a
    // truthful visible line beside an over-claiming accessible name is the same bug.
    render(<ApprovalCard seg={seg({ grantAgent: '' })} onAct={() => {}} />)
    fireEvent.click(scopeTab('This agent'))
    expect(allow().getAttribute('aria-label')).not.toMatch(/future ones/i)
    expect(allow().getAttribute('aria-label')).toMatch(/this chat only/i)
  })

  it('still posts trust_agent — the promise changed, not the action', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg({ grantAgent: '' })} onAct={onAct} />)
    fireEvent.click(scopeTab('This agent'))
    fireEvent.click(allow())
    // The backend is the authority on scope and records which outcome it produced
    // (`trust_agent` vs `trust_agent_session`). Narrowing the posted action in the client
    // would silently answer a different question than the one the user was asked.
    expect(onAct).toHaveBeenCalledWith('a1', 'trust_agent')
  })
})

describe('#506 — risk gates how far the answer may reach', () => {
  it('withholds the standing-grant scopes on a destructive call', () => {
    render(<ApprovalCard seg={seg({ risk: 'destructive', grantAgent: 'researcher' })} onAct={() => {}} />)
    // Allow-once is always available — the floor never costs more, which is what keeps this
    // a gate rather than a prompt people learn to click through.
    expect(scopeTab('Just this once')).toBeTruthy()
    expect(maybeScopeTab('This chat')).toBeNull()
    expect(maybeScopeTab('This agent')).toBeNull()
  })

  it('offers them after an explicit unlock, and the unlock states the consequence', () => {
    const onAct = vi.fn()
    const { container } = render(
      <ApprovalCard seg={seg({ risk: 'destructive', grantAgent: 'researcher' })} onAct={onAct} />,
    )
    const unlock = screen.getByRole('checkbox', { name: /standing grant/i })
    // What the user is agreeing to BEFORE they can agree to it: future destructive calls
    // stop asking. That sentence is the whole point of the extra rung.
    expect(container.textContent).toMatch(/without asking/i)
    fireEvent.click(unlock)
    fireEvent.click(scopeTab('This agent'))
    fireEvent.click(allow())
    expect(onAct).toHaveBeenCalledWith('a1', 'trust_agent')
  })

  it('leaves the cheap tiers exactly as they were (the proportionality floor)', () => {
    for (const risk of ['safe', 'caution'] as const) {
      const { unmount } = render(<ApprovalCard seg={seg({ risk, grantAgent: 'researcher' })} onAct={() => {}} />)
      // No unlock, no extra step: 86 of 92 tools must not get more expensive to answer.
      expect(screen.queryByRole('checkbox', { name: /standing grant/i })).toBeNull()
      expect(scopeTab('This chat')).toBeTruthy()
      expect(scopeTab('This agent')).toBeTruthy()
      unmount()
    }
  })

  it('an undeclared risk keeps the scopes — the card gates destructive only', () => {
    // Deliberate, and matched to the route, which also gates destructive only. Withholding on
    // an absent tier would make every legacy transcript row and every risk-less external tool
    // harder to answer than a `bash` call.
    render(<ApprovalCard seg={seg({ grantAgent: 'researcher' })} onAct={() => {}} />)
    expect(scopeTab('This chat')).toBeTruthy()
  })

  it('a destructive call is still approvable with one click at the narrowest scope', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg({ risk: 'destructive' })} onAct={onAct} />)
    // The anti-outage property, stated as a test: the gate never blocks ANSWERING the prompt,
    // only widening it. A permission surface the user cannot answer is the worse failure.
    fireEvent.click(allow())
    expect(onAct).toHaveBeenCalledWith('a1', 'approved')
  })

  it('narrowing back after an unlock does not keep the broad scope selected', () => {
    // The scope the card posts must be the one the label says. If unticking left `agent`
    // selected while the option was withdrawn, Allow would post a standing grant the user can
    // no longer see — a silent widening, which is the failure this whole PR is about.
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg({ risk: 'destructive', grantAgent: 'researcher' })} onAct={onAct} />)
    const unlock = screen.getByRole('checkbox', { name: /standing grant/i })
    fireEvent.click(unlock)
    fireEvent.click(scopeTab('This agent'))
    fireEvent.click(unlock)  // change of mind
    fireEvent.click(allow())
    expect(onAct).toHaveBeenCalledWith('a1', 'approved')
  })
})
