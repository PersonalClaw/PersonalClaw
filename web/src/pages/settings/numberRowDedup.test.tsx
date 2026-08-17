import { describe, expect, it, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { NumberRow } from './settingsUI'

// ── One cfg-driven numeric config row ─────────────────────────────────────────
//
// `NumberRow` appeared in SIX settings panels, but it was never one family — it is two contracts,
// and only one of them was actually duplicated:
//
//   cfg-driven   {cfg, field, patch}      patches BY KEY, owns its own flash
//     SourcesPanel        Field · min/max required · step 1
//     AmbientPanel        Field · min/max required · step 1     ← BYTE-IDENTICAL to Sources
//     AgentDefaultsPanel  Row   · optional min/max/step · suffix · useSavedFlash
//
//   value-driven  {value, onCommit}       is TOLD what to save; the PANEL owns the flash
//     DurabilityPanel     Row · suffix (w-14) · `saved` prop
//     ChatPanel           Row · suffix (w-6)  · `saved` prop · optional step
//     GuardrailsPanel     Row · `dollars` prefix · `onSave` returns a Promise it flashes off
//
// Sources and Ambient were the only verbatim pair — identical down to a duplicated `num()`
// coercion helper each kept privately. Those two are now one export here, and both `num()` copies
// went with them.
//
// WHAT THIS TEST DELIBERATELY DOES NOT DEMAND. It does not require the other four to converge:
//  · The value-driven three are a different contract, not a divergent copy of this one. Choosing
//    between "patch by key" and "be told a value" is a judgement about which shape the settings
//    panels should standardise on — logged as an open question rather than settled by a dedup.
//  · AgentDefaults is cfg-driven but its shape is a SUPERSET (suffix, optional bounds, `Row` not
//    `Field`). Folding it in would change its layout, which is a redesign wearing a dedup costume.
// Converging 2 of 6 is the complete pass over a coherent subset; the rail is scoped to match, so
// it cannot be satisfied by flattening a real distinction.
//
// The migration was verified HTML-identical before landing: the shared component and the removed
// copy were rendered side by side over 6 cfg shapes — a normal value, a missing key, a non-numeric
// string, null, zero, and a numeric string — and asserted to produce the same innerHTML. All 6
// matched. Those coercion cases are the point: the fallback logic had to be reproduced exactly, and
// they are where an inlined rewrite of `num()` would have diverged silently.

const SETTINGS = join(process.cwd(), 'src/pages/settings')

/** The panels that use the cfg-driven contract this export owns. */
const MIGRATED = ['SourcesPanel', 'AmbientPanel']
/** Declares its own NumberRow on a DIFFERENT contract — deliberately untouched. */
const OTHER_CONTRACT = ['GuardrailsPanel', 'DurabilityPanel', 'ChatPanel', 'AgentDefaultsPanel']

describe('the cfg-driven NumberRow lives in settingsUI', () => {
  it('neither migrated panel declares a private copy', () => {
    const definers = MIGRATED
      .filter((f) => /function NumberRow\b/.test(readFileSync(join(SETTINGS, `${f}.tsx`), 'utf8')))
    expect(definers, `private NumberRow in: ${definers.join(', ')}`).toEqual([])
  })

  it('neither migrated panel keeps a private num() coercion helper', () => {
    // Both kept their own identical `num()`, used only by the removed row. A leftover copy would
    // be dead code that still looks load-bearing.
    const leftovers = MIGRATED
      .filter((f) => /function num\(/.test(readFileSync(join(SETTINGS, `${f}.tsx`), 'utf8')))
    expect(leftovers, `orphaned num() in: ${leftovers.join(', ')}`).toEqual([])
  })

  it('both migrated panels import it', () => {
    for (const f of MIGRATED) {
      expect(readFileSync(join(SETTINGS, `${f}.tsx`), 'utf8'), `${f} should import NumberRow`)
        .toMatch(/import \{[^}]*\bNumberRow\b[^}]*\} from '\.\/settingsUI'/)
    }
  })

  it('the other-contract panels are left alone', () => {
    // The counterpart assertion. If this ever fails, someone "finished the dedup" by merging two
    // different contracts — which is the flattening this scope exists to avoid. Their convergence
    // is an open question, not an omission.
    for (const f of OTHER_CONTRACT) {
      expect(readFileSync(join(SETTINGS, `${f}.tsx`), 'utf8'),
        `${f} declares NumberRow on its own contract and should stay that way`)
        .toMatch(/function NumberRow\b/)
    }
  })
})

describe('NumberRow behaviour', () => {
  const rowFor = (cfg: Record<string, unknown>, patch = vi.fn()) => ({
    patch,
    ...render(<NumberRow label="Max tiles" cfg={cfg} field="max" min={1} max={48} patch={patch as never} />),
  })

  it('shows the configured value', () => {
    const { container } = rowFor({ max: 12 })
    expect(container.querySelector('input')?.value).toBe('12')
  })

  it('falls back to min when the key is missing', () => {
    // First-run state: the backend has not written this key yet.
    const { container } = rowFor({})
    expect(container.querySelector('input')?.value).toBe('1')
  })

  it('falls back to min for a non-numeric value rather than showing NaN', () => {
    const { container } = rowFor({ max: 'abc' })
    expect(container.querySelector('input')?.value).toBe('1')
  })

  it('keeps a legitimate zero instead of treating it as absent', () => {
    // `0` is falsy and finite — the case a `||` fallback would silently replace with min.
    const { container } = render(
      <NumberRow label="Layers" cfg={{ n: 0 }} field="n" min={0} max={2} patch={vi.fn() as never} />)
    expect(container.querySelector('input')?.value).toBe('0')
  })

  it('patches the field with the committed number and a flash callback', () => {
    const patch = vi.fn()
    const { container } = rowFor({ max: 12 }, patch)
    const input = container.querySelector('input')!
    fireEvent.change(input, { target: { value: '20' } })
    fireEvent.blur(input)
    // The third argument is what makes "Saved ✓" appear; without it the row would save silently.
    // The FOURTH is the control's visible label, so a rejected save can name "Max tiles" instead of
    // the config key `max` — the row holds both and used to hand over only the key.
    expect(patch).toHaveBeenCalledWith('max', 20, expect.any(Function), 'Max tiles')
  })

  it('clamps to max on commit (inherited from NumberField)', () => {
    const patch = vi.fn()
    const { container } = rowFor({ max: 12 }, patch)
    const input = container.querySelector('input')!
    fireEvent.change(input, { target: { value: '999' } })
    fireEvent.blur(input)
    expect(patch).toHaveBeenCalledWith('max', 48, expect.any(Function), 'Max tiles')
  })
})
