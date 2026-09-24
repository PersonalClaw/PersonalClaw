import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { NaturalVoicePill } from './controls'

// ── The natural-voice pill must show the EFFECTIVE state, and must not resolve it ──────────
//
// PT-7's control exists at two scopes: per-conversation (this pill) and per-agent (the agent
// definition). The resolution order between them is stated exactly ONCE, in Python
// (`natural_voice.NATURAL_VOICE_PRECEDENCE`), and the backend hands this component the already
// resolved `effective` + `source`.
//
// 🪤 THE FAILURE THIS RAIL EXISTS FOR. The tempting frontend shortcut is to take `choice` and the
// agent's own flag and work the effect out locally — `choice === 'on' || (choice === '' &&
// agentDefault)`. That is a SECOND copy of the resolution order, in a second language, free to
// drift from the one the turn actually uses. The pill would then be able to read "Plain" for a
// turn that ran without the instruction: a control reporting itself enabled while doing nothing,
// which is the single most common defect shape in this codebase.
//
// So the rail is two-part: the rendered name must track `effective`/`source` (below), and the
// component source must not contain the local derivation (the last test).

describe('the natural-voice pill shows what actually takes effect', () => {
  const noop = vi.fn()

  it('announces its dimension and the effective state', () => {
    render(<NaturalVoicePill choice="on" effective source="conversation" agentDefault={false} onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Plain' })).toBeTruthy()
  })

  it('names the AGENT when the agent is what turned it on', () => {
    // Attribution: the owner has to be able to tell "I set this here" from "this agent
    // always does this", or a prose change has no cause they can see.
    render(<NaturalVoicePill choice="" effective source="agent" agentDefault onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Plain (agent)' })).toBeTruthy()
  })

  it('reads Default when nothing asked for it', () => {
    render(<NaturalVoicePill choice="" effective={false} source="platform" agentDefault={false} onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Default' })).toBeTruthy()
  })

  it('an OFF override reads Default, not "Plain" — the agent does not win', () => {
    // The backend resolved this: conversation says off, agent says on, off wins.
    render(<NaturalVoicePill choice="off" effective={false} source="conversation" agentDefault onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Default' })).toBeTruthy()
  })

  it('before a conversation exists it shows the CHOICE, never a guessed effect', () => {
    // `source: ''` = a brand-new chat with nothing to resolve against. Showing an effect here
    // would require resolving the order client-side, so it shows the pick instead.
    render(<NaturalVoicePill choice="" effective={false} source="" agentDefault onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Agent default' })).toBeTruthy()
  })

  it('and a pre-session pick reads back as that pick', () => {
    render(<NaturalVoicePill choice="on" effective={false} source="" agentDefault={false} onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Plain' })).toBeTruthy()
  })

  it('is rendered at all — the state is visible without opening anything', () => {
    // A setting buried in a menu is not attributable. The state is on the closed pill.
    render(<NaturalVoicePill choice="" effective source="agent" agentDefault onSelect={noop} />)
    expect(screen.getByRole('button').textContent).toContain('Plain')
  })
})

describe('the pill does not re-derive the resolution order', () => {
  const SRC = readFileSync(join(process.cwd(), 'src/ui/composer/controls.tsx'), 'utf8')

  it('no local resolution of choice against the agent default', () => {
    const at = SRC.indexOf('export function NaturalVoicePill')
    expect(at, 'the component moved — this rail measures nothing').toBeGreaterThan(-1)
    const body = SRC.slice(at, SRC.indexOf('\n}\n', at))
    // The label expression is where a second copy of the order would live.
    const from = body.indexOf('const label =')
    expect(from, 'the label expression moved — this rail measures nothing').toBeGreaterThan(-1)
    const labelExpr = body.slice(from, body.indexOf('return (', from))
    expect(labelExpr.length, 'empty slice — vacuous').toBeGreaterThan(20)
    // The shape to forbid: combining the two scopes here to produce an effect.
    expect(labelExpr, 'the label must come from the backend, not from agentDefault')
      .not.toMatch(/agentDefault/)
    // It must read the RESOLVED pair instead.
    expect(labelExpr).toMatch(/effective/)
    expect(labelExpr).toMatch(/source/)
    // `agentDefault` is allowed elsewhere in the component — but only as the inherit
    // row's HINT text, which describes the agent rather than deciding an effect.
    expect(body).toMatch(/hint=\{agentDefault \?/)
  })

  it('the composer renders it whenever the host supplies it', () => {
    const COMPOSER = readFileSync(join(process.cwd(), 'src/ui/Composer.tsx'), 'utf8')
    expect(COMPOSER).toMatch(/\{naturalVoice && <NaturalVoicePill \{\.\.\.naturalVoice\} \/>\}/)
  })
})
