import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── One slider, three provider behaviours (#657) ─────────────────────────────────────────────────
//
// The "Speaking speed" slider was labelled Fast-at-minimum with "lower is faster"
// unconditionally. That is Piper's semantics (--length-scale, <1 is faster) and exactly
// BACKWARDS for OpenAI-compatible remotes, where the same raw number is the API's speed
// multiplier (higher is faster) — dragging toward "Fast" made speech slower. And Gemini's
// generateContent speech API has no rate parameter at all and uses its own preset voices,
// so for it both the slider and the persona select were dead controls.
//
// The fix derives what the user is TOLD (and which controls exist) from the bound
// provider family, leaving the stored value raw — both live backends consume it
// correctly. These are source assertions in the style of the neighboring panel rails:
// they pin the derivation so the unconditional label cannot quietly return.

const src = readFileSync(join(import.meta.dirname, 'VoicePanel.tsx'), 'utf8')

describe('speaking-speed orientation follows the consumer (#657)', () => {
  it('no unconditional "lower is faster" hint survives', () => {
    expect(src).not.toContain('— lower is faster.`}')
  })

  it('the hint direction is derived from the provider family', () => {
    expect(src).toContain("${higherIsFaster ? 'higher' : 'lower'} is faster")
  })

  it('both end labels flip with the family — no hardcoded Fast/Slow ends remain', () => {
    expect(src).toContain("{higherIsFaster ? 'Slow' : 'Fast'}")
    expect(src).toContain("{higherIsFaster ? 'Fast' : 'Slow'}")
    // The old fixed ends around the range input are gone.
    expect(src).not.toMatch(/text-\[0\.75rem\]">Fast</)
    expect(src).not.toMatch(/text-\[0\.75rem\]">Slow</)
  })

  it('remote (OpenAI-multiplier) orientation is higher-is-faster', () => {
    expect(src).toContain('const higherIsFaster = isRemoteVoice')
  })
})

describe("Gemini's dead controls are withheld with a stated reason (#657)", () => {
  it('a Gemini provider list exists beside PIPER_PROVIDERS', () => {
    expect(src).toMatch(/const GEMINI_TTS_PROVIDERS = \['google'/)
  })

  it('the speed slider is not rendered for Gemini — the reason renders instead', () => {
    expect(src).toContain('isGeminiVoice ? (')
    expect(src).toContain('no speaking-rate control')
  })

  it('the persona select excludes Gemini (its provider ignores speech_voice)', () => {
    expect(src).toContain('{isRemoteVoice && !isGeminiVoice && (')
  })
})
