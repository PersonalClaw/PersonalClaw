import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StreamingIndicator } from './StreamingIndicator'
import { WORK_PHASES, phaseLabel } from '../../lib/workPhase'

// ── The chat's thinking indicator says the phase the gateway measured ────────────────────────────
//
// It used to guess: `derivePhase` read "think", "respond", "writing" out of the free-text status and
// called anything else "Working", so a search, a plan and a wait on her answer all read the same.
// Now it is handed the turn's phase and the line the gateway composed for it: its label is the
// phase's own word (`workPhase.ts`), and its sentence is the line when the line says more than the
// label does.

describe('the label is the phase, never a reading of words', () => {
  it.each([...WORK_PHASES])('%s says its own word', (phase) => {
    render(<StreamingIndicator phase={phase} line="" />)
    expect(screen.getByText(phaseLabel(phase), { selector: '[data-phase-label]' })).toBeTruthy()
  })

  it('a step whose line talks about thinking is still the step it is', () => {
    render(<StreamingIndicator phase="tool" line="Thinking about the cache" />)
    expect(screen.getByText(phaseLabel('tool'), { selector: '[data-phase-label]' })).toBeTruthy()
    expect(screen.queryByText('Thinking', { selector: '[data-phase-label]' })).toBeNull()
    // The gateway's line is shown as it said it.
    expect(screen.getByText('Thinking about the cache')).toBeTruthy()
  })

  it('a search says where it looks', () => {
    render(<StreamingIndicator phase="searching" line="Searching your knowledge" />)
    expect(screen.getByText('Searching your knowledge')).toBeTruthy()
  })

  it('a turn the gateway has not described yet reads as waiting to start', () => {
    render(<StreamingIndicator phase={null} line="" />)
    expect(screen.getByText(phaseLabel('queued'), { selector: '[data-phase-label]' })).toBeTruthy()
  })
})
