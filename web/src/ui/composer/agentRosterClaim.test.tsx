import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AgentPill } from './controls'
import type { ComposerData } from './types'

// ── "No agents available" is a claim about the user's SETUP, not a status line ─────────────────────
//
// `AgentPill` printed it on a single condition:
//
//     {total === 0 && <div …>No agents available</div>}
//
// and `total` is `data?.agents.length + …data?.discovered`. `useComposerData` seeds every list to
// `[]` and readies through `Promise.allSettled`, so THREE different states all arrive as `[]`:
//
//   1. the reads have not returned yet          → unknown
//   2. `api.agents()` REJECTED                  → unknown (and `ready` is true anyway!)
//   3. the reads succeeded, roster is empty      → the claim is true and useful
//
// 🔑 ONLY THE THIRD IS A FACT. The other two rendered a confident sentence about the user's own
// install on the strength of a request that never landed — and on an unreachable gateway that is the
// COMMON case, not the edge one. The user cannot tell it from a real diagnosis, so it sends them
// hunting for a missing agent that was never missing.
//
// 🪤 `ready` alone is NOT enough to separate them, which is the subtle part. `Promise.allSettled`
// resolves on a rejected leg, so `setReady(true)` runs even when the agents read failed. `agentsErr`
// is what carries state 2. A fix that only checked `ready` would still print "No agents available"
// on every failed read.
//
// 🪤 `ready` DEFAULTS TRUE in the component. Hosts that pass fixed lists (the goal composer, the
// sibling pill tests) never set it; defaulting false would pin them on "Loading agents…" forever.
//
// BRANCH TABLE — why only this one pill changed. Checked all five empty-branches in controls.tsx:
//   AgentPill `total === 0`            → THE DEFECT (above).
//   ModelPill native path              → renders no empty text at all; the always-valid "Auto" row
//                                        is the honest answer for an unread model list. Not a claim.
//   ModelPill ACP `acpModels.length`   → only reachable once `discovered` HAS been read and contains
//                                        the agent, so it cannot fire on an unread state.
//   ApprovalPill                       → a static local const, no read.
//   ReasoningPill `!efforts.length`    → returns null. Says nothing. Already correct.

const base: ComposerData = { agents: [], providers: [], discovered: {}, models: [] }

function openPicker() {
  return userEvent.click(screen.getByRole('button', { name: /^Agent/ }))
}

describe('the agent picker never states an emptiness it could not read', () => {
  it('an in-flight read says it is loading, NOT that no agents exist', async () => {
    render(<AgentPill data={{ ...base, ready: false }} value="" onSelect={vi.fn()} />)
    await openPicker()
    expect(screen.getByText(/Loading agents/i)).toBeInTheDocument()
    expect(screen.queryByText('No agents available'), 'an unread roster is not an empty one').toBeNull()
  })

  it('a FAILED read says so and offers a retry — even though `ready` is true', async () => {
    // 🪤 `ready: true` here is not a mistake in the fixture; it is the production shape.
    // `Promise.allSettled` readies on a rejected leg, which is exactly why `agentsErr` is needed.
    const retry = vi.fn()
    render(<AgentPill data={{ ...base, ready: true, agentsErr: new Error('gateway down'), retry }} value="" onSelect={vi.fn()} />)
    await openPicker()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText(/Couldn’t load your agents/i)).toBeInTheDocument()
    expect(screen.queryByText('No agents available'), 'a failed read must not claim the setup is empty').toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /Try again/i }))
    expect(retry, 'the retry must be wired, or the honest message is a dead end').toHaveBeenCalledTimes(1)
  })

  it('a GENUINELY empty roster still says "No agents available" — the true claim survives', async () => {
    // The other half. Suppressing the sentence unconditionally would pass both tests above while
    // deleting a real and useful signal for a user who really has no agents installed.
    render(<AgentPill data={{ ...base, ready: true, agentsErr: null }} value="" onSelect={vi.fn()} />)
    await openPicker()
    expect(screen.getByText('No agents available')).toBeInTheDocument()
  })

  it('a populated roster shows the agents and none of the three empty messages', async () => {
    // Guards against the assertions above passing because the picker never renders rows at all.
    const data = { ...base, ready: true, agents: [{ name: 'researcher', description: 'd' }] } as unknown as ComposerData
    render(<AgentPill data={data} value="" onSelect={vi.fn()} />)
    await openPicker()
    expect(screen.getByText('researcher')).toBeInTheDocument()
    expect(screen.queryByText('No agents available')).toBeNull()
    expect(screen.queryByText(/Loading agents/i)).toBeNull()
    expect(screen.queryByText(/Couldn’t load your agents/i)).toBeNull()
  })

  it('a host that passes no `ready` is NOT stuck on loading', async () => {
    // The goal composer and the sibling pill tests construct `data` by hand. Defaulting `ready`
    // to false would have shown them "Loading agents…" permanently.
    render(<AgentPill data={base} value="" onSelect={vi.fn()} />)
    await openPicker()
    expect(screen.queryByText(/Loading agents/i)).toBeNull()
    expect(screen.getByText('No agents available')).toBeInTheDocument()
  })
})

describe('the hook reports the failure it used to swallow', () => {
  const src = () => readFileSync(join(process.cwd(), 'src/lib/useComposerData.ts'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('a rejected agents read sets agentsErr', () => {
    // The load-bearing half: without this the DOM tests above could pass on a fixture that
    // production never produces.
    expect(src(), 'the rejected leg must be recorded, not dropped').toMatch(/setAgentsErr\(ag\.reason/)
    expect(src(), 'and it must be exposed to the picker').toMatch(/return \{[^}]*agentsErr/)
  })

  it('a successful retry clears a previous failure', () => {
    // Otherwise the error line would outlive the error, and the picker would keep denying a roster
    // it had just read successfully.
    expect(src()).toMatch(/setAgentsErr\(null\)/)
  })
})
