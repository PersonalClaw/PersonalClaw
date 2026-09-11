import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { sessionTemplatePatch } from './sessionTemplate'
import type { SessionTemplate } from '../../lib/api'

// A saved starter with everything empty except what a case sets — mirrors the real shape
// (empty agent/model/reasoning mean "use the default at start time").
function starter(over: Partial<SessionTemplate> = {}): SessionTemplate {
  return { id: 't1', name: 'Starter', agent: '', model: '', reasoning_effort: '', first_prompt: '', created_at: 0, ...over }
}

// ── "prefills the composer selection" is a PARTIAL patch, never a full overwrite (SM-7) ──
//
// The bug this pins: a starter saved with no model must not silently reset the user's current
// pick to "Auto". `applyTemplate` guards `applySelection` on `Object.keys(patch).length`, so an
// empty patch means the current selection is left exactly as it was.
describe('sessionTemplatePatch — only the fields the starter carries', () => {
  it('an all-empty starter yields an empty patch (nothing is reset)', () => {
    expect(sessionTemplatePatch(starter())).toEqual({})
  })

  it('carries agent alone without inventing a model or reasoning', () => {
    expect(sessionTemplatePatch(starter({ agent: 'researcher' }))).toEqual({ agent: 'researcher' })
  })

  it('carries model alone', () => {
    expect(sessionTemplatePatch(starter({ model: 'claude-opus-5' }))).toEqual({ model: 'claude-opus-5' })
  })

  it('carries reasoning effort alone, mapped to the composer field', () => {
    expect(sessionTemplatePatch(starter({ reasoning_effort: 'high' }))).toEqual({ reasoning: 'high' })
  })

  it('carries all three when the starter set all three', () => {
    expect(sessionTemplatePatch(starter({ agent: 'a', model: 'm', reasoning_effort: 'low' })))
      .toEqual({ agent: 'a', model: 'm', reasoning: 'low' })
  })

  it('never puts the prompt in the selection patch — first_prompt feeds the input, not the picks', () => {
    const patch = sessionTemplatePatch(starter({ first_prompt: 'summarize my week', agent: 'a' }))
    expect(patch).toEqual({ agent: 'a' })
    expect(patch).not.toHaveProperty('first_prompt')
  })
})

// The other half of the clause — "prefills the … prompt and enables Send" — is a composer-INPUT
// write inside ChatPage's `applyTemplate`. ChatPage is not rendered in this suite (its own tests
// read source structurally, e.g. sessionLoadHonesty.test.ts), so this pins the wiring the same
// way: the prompt is prefilled through `setInput(t.first_prompt)`, guarded so an empty starter
// leaves the composer untouched. A non-empty input is what enables Send.
describe('applyTemplate wires the prompt prefill (ChatPage source)', () => {
  const src = readFileSync(join(__dirname, '..', 'ChatPage.tsx'), 'utf-8')
  it('applies the patch via the shared helper and prefills the prompt through setInput', () => {
    expect(src).toContain('const patch = sessionTemplatePatch(t)')
    expect(src).toContain('if (t.first_prompt) setInput(t.first_prompt)')
    expect(src).toContain('if (Object.keys(patch).length) applySelection(patch)')
  })
})
