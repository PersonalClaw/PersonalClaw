import { afterEach, describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { activePersonaTheme } from './personalities'

// ── Issue 650: the persona reaches the model ────────────────────────────────────────
//
// The backend's persona injection read `color_theme` from the chat POST body; no
// client ever sent it, so picking "Retro Terminal" recolored the UI and left the
// assistant's voice unchanged — the picker's own hint ("a terse operator voice") was
// false. activePersonaTheme() derives the wire value from the active personality's
// personaSnippet (the single declaration of a voice identity), and sendChat attaches
// it centrally so every send path carries it.

afterEach(() => localStorage.removeItem('personality'))

describe('activePersonaTheme (issue 650)', () => {
  it('is empty for the default personality (no voice declared)', () => {
    localStorage.setItem('personality', 'personalclaw')
    expect(activePersonaTheme()).toBe('')
  })

  it("derives the backend theme key from the active personality's snippet", () => {
    localStorage.setItem('personality', 'retro-terminal')
    expect(activePersonaTheme()).toBe('retro-terminal')
    localStorage.setItem('personality', 'claw-arcade')
    expect(activePersonaTheme()).toBe('claw-arcade')
  })

  it('falls back to the default (voiceless) for an unknown stored id', () => {
    localStorage.setItem('personality', 'removed-personality')
    expect(activePersonaTheme()).toBe('')
  })
})

describe('the send path carries it (wiring pin)', () => {
  it('sendChat attaches color_theme from activePersonaTheme', () => {
    const src = readFileSync(join(process.cwd(), 'src/lib/api.ts'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    const fn = src.slice(src.indexOf('sendChat:'), src.indexOf('cancelQueued:'))
    expect(fn).toMatch(/const color_theme = activePersonaTheme\(\)/)
    expect(fn).toMatch(/\.\.\.\(color_theme \? \{ color_theme \} : \{\}\)/)
  })

  it('every personality promising a voice in its hint declares a personaSnippet', () => {
    const src = readFileSync(join(process.cwd(), 'src/design/personalities.ts'), 'utf8')
    // The two voice-selling hints exist and their entries carry snippets.
    expect(src).toMatch(/terse operator voice/)
    expect(src).toMatch(/persona-retro-terminal/)
    expect(src).toMatch(/playful, high-energy voice/)
    expect(src).toMatch(/persona-claw-arcade/)
  })
})
