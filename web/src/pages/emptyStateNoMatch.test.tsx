import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Box, Search, Users } from 'lucide-react'
import { EmptyState } from '../ui/ListScaffold'
import { ArtifactGrid } from './artifacts/ArtifactGrid'

// ── "You have none" is not "none match" ─────────────────────────────────────
//
// A list body that is empty is TWO different situations, and they need different
// words. Three surfaces got it wrong in two different ways:
//
//   · artifacts — ArtifactGrid receives the ALREADY-FILTERED list, so it could not tell the
//     cases apart and told a user with a full library "No artifacts / Ask the agent to save
//     one" when they mistyped a search. (Verified live before the fix.)
//   · agents — filters by `match(q)` first, then rendered "No native agents" + a create button.
//   · projects — distinguished them, but rendered the no-match case as a bare centered <p>,
//     skipping the shared icon/title/hint rhythm.
//
// Canonical shape, already used by apps / knowledge / prompts / inbox / triggers:
//   title={q ? 'No matching X' : 'No X'}, through EmptyState, with the create-first advice
//   reserved for the genuinely-empty case where it is ACTIONABLE.
//
// These assertions are what a regression trips on: the create affordance must NOT appear when
// the list is merely filtered, because it answers a question the user did not ask.

describe('ArtifactGrid empty states', () => {
  it('offers the create path only when the library is genuinely empty', () => {
    render(<ArtifactGrid artifacts={[]} onOpen={() => {}} />)
    expect(screen.getByText('No artifacts')).toBeInTheDocument()
    // The hint is the one that teaches how artifacts come to exist.
    expect(screen.getByText(/Ask the agent to save one/)).toBeInTheDocument()
  })

  it('says "no matching" — and drops the create advice — when a filter is active', () => {
    render(<ArtifactGrid artifacts={[]} onOpen={() => {}} narrowed />)
    expect(screen.getByText('No matching artifacts')).toBeInTheDocument()
    expect(screen.queryByText('No artifacts')).not.toBeInTheDocument()
    // Telling someone with a full library to create their first artifact was the whole bug.
    expect(screen.queryByText(/Ask the agent to save one/)).not.toBeInTheDocument()
  })
})

describe('the canonical no-match copy', () => {
  // Locks the shape the other surfaces (agents, projects) render inline, so a future edit that
  // reverts one of them to a single shared string has something to fail against.
  it('pairs a search icon with "No matching <entity>" and a retry hint', () => {
    render(<EmptyState icon={Search} title="No matching agents" hint="Try a different term." />)
    expect(screen.getByText('No matching agents')).toBeInTheDocument()
    expect(screen.getByText('Try a different term.')).toBeInTheDocument()
  })

  it('keeps the teaching hint and an action for the genuinely-empty case', () => {
    render(
      <EmptyState icon={Users} title="No native agents" hint="Create an agent to define its model."
        action={{ label: 'New agent', onClick: () => {}, icon: Box }} />,
    )
    expect(screen.getByRole('button', { name: /New agent/ })).toBeInTheDocument()
  })
})
