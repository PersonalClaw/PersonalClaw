import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { lessonPreview } from './MemoryPanel'
import type { Lesson } from '../../lib/api'

// ── "Why is it still doing that?" and "why did it stop?" have to be answerable HERE ──────────────
//
// A learned lesson is now injected only when its DERIVED confidence clears a gate (WF2LEA-15), which
// means the Memory studio's lesson list stopped being a list of things the agent follows: some rows
// are followed, some are retained below the gate and followed by nothing. A list that renders both
// identically is worse than the ungated version it replaced — the user can no longer tell which of
// their rules is actually in force, and neither question above has an answer anywhere in the product.
//
// Two rails, because the failure has two halves:
//
//   1. the STANDING must be in the row, in words. Not a dot, not a tint: a colour is not a state, and
//      the preview line is already the row's accessible name, so words put the standing into the
//      accessibility tree with nothing extra to maintain.
//   2. the REASON must be the server's sentence, rendered verbatim. The server composes it from the
//      same verdict the injection gate compared; a frontend that re-derived "because it was observed
//      twice" would be a second opinion, and the studio would eventually contradict the gate.

const base: Lesson = { rule: 'prefer ruff over flake8', category: 'tool' }

describe('a lesson row says whether it is in prompts', () => {
  it('names the injected state in words, with its confidence', () => {
    const line = lessonPreview({ ...base, standing: 'injected', confidence: 0.65 })
    expect(line).toContain('in prompts')
    expect(line).toContain('65%')
    expect(line).toContain('tool')
  })

  it('names the held state in words rather than by absence', () => {
    const line = lessonPreview({ ...base, standing: 'retained', confidence: 0.29 })
    expect(line).toContain('held below the gate')
    expect(line).toContain('29%')
    // The two states must not read the same — the whole point is telling them apart.
    expect(line).not.toEqual(lessonPreview({ ...base, standing: 'injected', confidence: 0.29 }))
  })

  it('claims no standing when the server reported none', () => {
    // A backend that predates the gate sends no `standing`. Inventing "in prompts"
    // here would be the frontend asserting something nobody computed.
    expect(lessonPreview(base)).toEqual('tool')
    expect(lessonPreview({ ...base, category: '' })).toEqual('lesson')
  })

  it('rounds a missing confidence to 0% rather than rendering NaN', () => {
    expect(lessonPreview({ ...base, standing: 'retained' })).toContain('0% confidence')
  })
})

describe('the inspector shows the evidence, not a second opinion', () => {
  const src = readFileSync(join(__dirname, 'MemoryPanel.tsx'), 'utf8')

  it('renders the standing, the confidence and the server-composed reason', () => {
    expect(src).toContain("['In prompts'")
    expect(src).toContain("['Confidence'")
    expect(src).toContain('confidence_reason')
    expect(src).toContain("['Observed'")
  })

  it('never recomposes the reason sentence in the frontend', () => {
    // The reason arrives whole. If the panel ever starts assembling one from the
    // counters, this rail fails and the drift is caught at the point it is introduced.
    expect(src).not.toMatch(/observed \$\{[^}]*observations/)
    expect(src).not.toMatch(/below the \$\{[^}]*(threshold|gate)/)
  })
})
