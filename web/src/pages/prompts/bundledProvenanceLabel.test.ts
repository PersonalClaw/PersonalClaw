import { describe, it, expect } from 'vitest'
import { isReadOnly, sourceLabel, sourceTone } from './promptMeta'

// ── A shipped prompt must not be credited to the user ────────────────────────────────────────────
//
// `seed_bundled_prompts()` writes every `catalog.BUNDLED_PROMPTS` row to disk so it shows up in the
// Prompts UI *as an editable native prompt*, and `NativePromptProvider._load_prompt` then stamps
// EVERY on-disk prompt `tpl.source = 'user'` (native_provider.py). That stamp is what keeps a
// shipped prompt editable, so `isReadOnly`/`sourceTone` are right to treat it as the user's.
//
// The LABEL was not. The source pill read `user` on PersonalClaw's own shipped prompts — while the
// tag chips rendered two lines below on the same panel said `bundled`. One screen, two answers.
//
// 🪤 THE TEMPTING FIX IS A REGRESSION. Making the backend report `source: 'bundled'` would light up
// `sourceTone`'s `on-surface-low` branch and label the pill correctly — and make every shipped
// prompt READ-ONLY, because `isReadOnly` is `source !== 'user'`. That trades a cosmetic mislabel for
// losing the editability the seeder exists to provide. Provenance lives in the tags; the label reads
// it from there and touches nothing else. The last two cases below are what hold that line.

const BUNDLED = ['system', 'bundled']

describe('the source pill names a shipped prompt as bundled', () => {
  it('a seeded prompt says bundled, not user', () => {
    expect(sourceLabel('user', BUNDLED)).toBe('bundled')
  })

  it('a prompt the user actually wrote still says user', () => {
    expect(sourceLabel('user', ['mine'])).toBe('user')
    expect(sourceLabel('user', [])).toBe('user')
    expect(sourceLabel('user')).toBe('user')
    expect(sourceLabel(undefined)).toBe('user')
  })

  it('a genuinely non-user source keeps its own name, tags or no tags', () => {
    expect(sourceLabel('marketplace', BUNDLED)).toBe('marketplace')
    expect(sourceLabel('marketplace')).toBe('marketplace')
  })

  it('the relabel does not make a shipped prompt read-only', () => {
    // The capability, not the wording: a seeded prompt is editable and stays editable.
    expect(isReadOnly('user')).toBe(false)
  })

  it('the relabel does not dim a shipped prompt to the read-only tone', () => {
    // Coral is "you can edit this". A bundled TAG must not repaint it as an untouchable source.
    expect(sourceTone('user')).toBe('var(--color-primary)')
    expect(sourceTone('marketplace')).toBe('var(--color-info)')
  })
})
