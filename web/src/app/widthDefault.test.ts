import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { WIDTH_PRESETS, DEFAULT_WIDTH_PRESET } from './appearance'

// ── Two declared defaults for one setting, and the reachable one was not the documented one ──
//
// Found while driving cycle 132's rejected `resetAll` claim. The app declared its default content width
// twice and they disagreed:
//
//   `empty.widthPreset = 'full'`              → 100%, edge to edge
//   `ov.widthPreset ?? 'default'` (×2 reads)  → the 1100px cap
//
// 🪤 THE PRESET NAMED `'default'` IS NOT THE DEFAULT PRESET. That is the whole trap in one line, and it is
// why a reader would "fix" the wrong one.
//
// The fallbacks were **unreachable**, and this is measured rather than reasoned: `load()` returns
// `{ ...empty, ...stored }` — or a clone of `empty` — so `widthPreset` is always set. Driven on the parent
// tree, both cases resolve to `full` even though the file contains `?? 'default'` twice:
//
//   pristine (no stored appearance)       stored widthPreset=full   --content-width=100%
//   legacy payload with NO widthPreset    stored widthPreset=full   --content-width=100%
//                                         ← the exact shape the fallback was written for
//
// So the risk was never a wrong width today; it was that deleting `empty.widthPreset`, or trusting the
// `?? 'default'` comment, would silently re-cap **every page** from edge-to-edge to 1100px. One named
// constant now, read in all three places: a disagreement becomes a type error instead of a comment nobody
// reads. Behaviour after the change is identical (`full` / 100% in both cases).

const SRC = join(process.cwd(), 'src')
const appearance = readFileSync(join(SRC, 'app/appearance.tsx'), 'utf8')
const code = appearance.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('there is exactly one default content width', () => {
  it('is named, exported, and is `full`', () => {
    expect(DEFAULT_WIDTH_PRESET).toBe('full')
  })

  it('resolves to a true edge-to-edge width', () => {
    expect(WIDTH_PRESETS[DEFAULT_WIDTH_PRESET]).toBe('100%')
  })

  it('no read falls back to the 1100px preset any more', () => {
    // The disagreement itself, pinned. Comments are stripped first — this file DOCUMENTS the old spelling.
    expect(code, "the `'default'` preset is not the default").not.toMatch(/\?\?\s*'default'/)
  })

  it('the factory overrides use the constant, not a literal', () => {
    expect(code).toMatch(/widthPreset: DEFAULT_WIDTH_PRESET/)
    expect(code, 'a second literal would re-open the gap').not.toMatch(/widthPreset: 'full'/)
  })

  it('both reads use the constant', () => {
    const reads = [...code.matchAll(/ov\.widthPreset \?\? DEFAULT_WIDTH_PRESET/g)]
    expect(reads.length, 'the CSS variable and the context value').toBeGreaterThanOrEqual(2)
  })

  it('keeps the `default` preset in the map — the naming hazard is deliberate', () => {
    // Renaming the map key would be an API break for stored payloads; the fix is to stop treating the
    // WORD "default" as the default, not to rename a shipped option.
    expect(WIDTH_PRESETS.default).toBe('1100px')
    expect(Object.keys(WIDTH_PRESETS).sort()).toEqual(['default', 'full', 'narrow', 'wide'])
  })

  it('the loader invariant that made the fallback dead is still in place', () => {
    // `{ ...empty, ...stored }` is what guarantees `widthPreset` is always set. If a future refactor stops
    // spreading `empty`, the fallback stops being dead and this assertion is where that shows up.
    expect(code).toMatch(/\{ \.\.\.empty, \.\.\.JSON\.parse\(raw\) \}/)
    expect(code).toMatch(/return structuredClone\(empty\)/)
  })
})
