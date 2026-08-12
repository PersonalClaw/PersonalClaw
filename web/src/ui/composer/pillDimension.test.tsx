import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AgentPill, ModelPill, ApprovalPill, ReasoningPill } from './controls'

// ── A value pill that never says what it controls ──────────────────────────────────────
//
// The composer's pill cluster is four value selectors in a row. Each renders an icon, its
// CURRENT VALUE, and a chevron — and nothing else. Measured accessible names on #/chat before
// this change:
//
//   "Agent"      (which agent is bound)
//   "Auto"       (the model)
//   "Default"    (the reasoning effort)
//   "Normal"     (the permission mode — rendered on surfaces that enable it)
//
// Four bare values. A screen-reader user tabbing the composer hears "Agent", "Auto", "Default"
// and has no way to know they are the agent, the model and the reasoning effort — the dimension
// lives only in the icon and the horizontal position, neither of which is announced.
//
// The app already solves this one row up. `HeaderModePill` composes
// `aria-label={`${ariaLabel}: ${label}`}`, so the header's pills announce "Task mode: Agent" and
// "Permission mode: Normal". The composer's pills are the same kind of control and now take the
// same shape; the visible label is untouched, because on screen the dimension is already carried
// by position and icon, which is exactly why the label spends its width on the value.
//
// 🪤 THE AMBIGUITY THAT FOUND THIS. On #/chat the header renders a task-mode pill labelled
// "Agent" with a Bot icon, and the composer renders the agent pill ALSO labelled "Agent" with a
// Bot icon, 380px apart, controlling different things (which tools may run vs. which agent
// answers). The accessible names were the only thing distinguishing them — and only one of the
// two had a useful one. The remaining VISIBLE collision is a copy decision, recorded for the
// owner rather than guessed at here: see the cycle-66 note in POLISH-SESSION.md.

describe('every composer pill announces its dimension', () => {
  it('the agent pill says Agent (not "Agent: Agent") when nothing is bound', () => {
    // Its fallback value IS the dimension word, so the composed name would stutter.
    render(<AgentPill value="" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Agent' })).toBeTruthy()
  })

  it('the agent pill names the dimension AND the bound agent', () => {
    render(<AgentPill value="researcher" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Agent: researcher' })).toBeTruthy()
  })

  it('the model pill says Model', () => {
    render(<ModelPill value="" onSelect={vi.fn()} />)
    // Unset resolves to the "Auto" use-case chain.
    expect(screen.getByRole('button', { name: 'Model: Auto' })).toBeTruthy()
  })

  it('the permission pill matches the HEADER pill it shares an axis with', () => {
    // The header announces "Permission mode: Normal"; the composer must not invent a second
    // name for the same dimension.
    render(<ApprovalPill value="normal" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Permission mode: Normal' })).toBeTruthy()
  })

  it('the reasoning pill says Reasoning effort', () => {
    render(<ReasoningPill value="" efforts={[{ value: 'low', label: 'Low' }]} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Reasoning effort: Default' })).toBeTruthy()
  })

  it('leaves the VISIBLE label alone (this is a naming fix, not a redesign)', () => {
    render(<ReasoningPill value="" efforts={[{ value: 'low', label: 'Low' }]} onSelect={vi.fn()} />)
    // The pill still shows just the value on screen; only the accessible name gained the axis.
    expect(screen.getByRole('button').textContent).toBe('Default')
  })
})

describe('the rail', () => {
  const src = readFileSync(join(process.cwd(), 'src/ui/composer/controls.tsx'), 'utf8')

  /** Complete `<PillButton …>` tags, tracking {} depth. A naive `[^>]*>` stops at the `/>` of the
   *  nested `<Bot … />` icon prop and reports every tag as dimension-less — which is how this rail
   *  first "found" three failures against a tree that was already fixed. */
  const sites = (() => {
    const out: string[] = []
    for (const m of src.matchAll(/<PillButton\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < src.length; i++) {
        const ch = src[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) { out.push(src.slice(m.index!, i + 1)); break }
      }
    }
    return out
  })()

  it('finds every pill trigger (not vacuously green)', () => {
    // Four at the time of writing. If a fifth pill appears, `dimension` being a REQUIRED prop
    // means typecheck stops it before this test does — this floor is here so the assertion below
    // cannot pass by matching nothing.
    expect(sites.length, 'the matcher must find the pill triggers').toBeGreaterThanOrEqual(4)
  })

  it('has no pill trigger without a dimension', () => {
    const mute = sites.filter((t) => !/\bdimension=/.test(t))
    expect(mute, `pill trigger(s) announce a bare value:\n  ${mute.join('\n  ')}`).toEqual([])
  })

  it('composes the name the same way HeaderModePill does', () => {
    // If the header's format changes, these two families drift apart again. Assert the shape in
    // both places rather than trusting them to stay in step.
    const header = readFileSync(join(process.cwd(), 'src/ui/HeaderActions.tsx'), 'utf8')
    expect(header, 'the header pill composes "<dimension>: <value>"').toMatch(/aria-label=\{`\$\{ariaLabel[^`]*\}: \$\{label\}`\}/)
    expect(src, 'the composer pill composes the same shape').toMatch(/`\$\{dimension\}: \$\{label\}`/)
  })
})
