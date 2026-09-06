import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { SavedAgent } from '../../lib/api'

// ── The agent detail page must not call a MUTED agent "Active" (issue 414) ────────────────────────
//
// The routing-suppression store's agent identity is CASE-INSENSITIVE: every read and write funnels
// through `agents/routing.py`'s `canonical_agent`, so `GET /api/agents/routing/status` returns
// lowercased keys. This panel compared them to the RAW route name:
//
//     (s.muted || []).includes(agentName)
//
// Measured on a live gateway against the SHIPPED DEFAULT AGENT, whose name is mixed-case:
//
//     POST /api/agents/routing/dismiss {"agent":"PersonalClaw"} ×3
//        → {"agent":"personalclaw","count":3,"muted":true}
//     GET  /api/agents/routing/status  → muted: ["personalclaw"]
//     is_suppressed("PersonalClaw")    → True          ← the router IS refusing to suggest it
//     ['personalclaw'].includes('PersonalClaw') → false
//     the panel rendered               → "Active — eligible for auto-routing suggestions", 0 buttons
//
// So the page stated the opposite of the truth AND withheld the only per-agent undo, from one
// `includes`. `PersonalClaw` is a reachable routing candidate, not a curiosity: `eligible_candidates`
// excludes it only while it is the DEFAULT agent, so choosing any other default plus a `route_hints`
// line makes it suggestible — verified on the live config (`eligible: [('PersonalClaw', '', …)]`).
//
// 🪤 THE ASSERTION IS THE RENDERED CLAIM, not the comparison. A rail that greps for
// `canonicalAgentKey` passes on a panel that calls it and then ignores the result; the two drives
// below mount the real component against a real canonical `muted` payload.

const unmuted: string[] = []

function mockApi(muted: string[]) {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as Record<string, unknown>),
        routingStatus: () => Promise.resolve({ enabled: true, muted, dismissals: {} }),
        routingUnmute: (agent: string) => { unmuted.push(agent); return Promise.resolve({ ok: true, agent }) },
        agentMetadata: () => Promise.resolve({ content: '' }),
        mcpActive: () => Promise.resolve([]),
        agentHooks: () => Promise.resolve([]),
        modelsActive: () => Promise.resolve({ chat: [] }),
      },
    }
  })
}

const agent = (name: string): SavedAgent => ({ name, provider: 'claude', system_prompt: 'probe' }) as SavedAgent

beforeEach(() => { vi.resetModules(); unmuted.length = 0; sessionStorage.clear() })

async function mount(name: string, muted: string[]) {
  mockApi(muted)
  const { NativeAgentDetail } = await import('./AgentDetail')
  render(
    <NativeAgentDetail agent={agent(name)} isDefault={false} onSaved={vi.fn()} onDeleted={vi.fn()}
      onSetDefault={vi.fn()} editing={false} onEditingChange={vi.fn()} />,
  )
  // Routing status lives under the collapsed Advanced disclosure.
  fireEvent.click(await screen.findByRole('button', { name: /^Advanced$/ }))
}

describe('a muted agent reads as muted whatever case its name is in', () => {
  it('the lowercase case still works — the half AR2-8 shipped', async () => {
    await mount('zz414-probe-agent', ['zz414-probe-agent'])
    expect(await screen.findByText(/Muted — the auto-router stopped suggesting this agent/)).toBeTruthy()
    expect(await screen.findByRole('button', { name: /^Unmute$/ })).toBeTruthy()
  })

  it('a MIXED-CASE agent name matches its canonical store key', async () => {
    await mount('PersonalClaw', ['personalclaw'])
    expect(await screen.findByText(/Muted — the auto-router stopped suggesting this agent/),
      'the store holds "personalclaw"; this panel was handed "PersonalClaw"').toBeTruthy()
    expect(screen.queryByText(/Active — eligible for auto-routing/),
      'the panel must not state the opposite of is_suppressed').toBeNull()
    fireEvent.click(await screen.findByRole('button', { name: /^Unmute$/ }))
    // The backend canonicalises the write too, so sending the display name is correct and safe.
    await waitFor(() => expect(unmuted).toEqual(['PersonalClaw']))
  })

  it('an agent that is genuinely not muted still says so', async () => {
    await mount('PersonalClaw', ['some-other-agent'])
    expect(await screen.findByText(/Active — eligible for auto-routing/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Unmute$/ }), 'nothing to undo').toBeNull()
  })
})
