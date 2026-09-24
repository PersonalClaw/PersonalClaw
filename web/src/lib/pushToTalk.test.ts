import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { chordFromEvent, formatChord, DEFAULT_PUSH_TO_TALK_CHORD } from './pushToTalk'

/**
 * DESKTOP-CAPABILITIES S3 — the chord recorder's rules.
 *
 * These are the functions the Settings control is built on, and they carry two refusals
 * that matter more than they look:
 *
 *  - a **modifier-only** press is not a chord yet, so the recorder keeps listening
 *    instead of storing half a shortcut;
 *  - a **bare key** is never recorded at all, because the shell refuses to bind one (it
 *    would be taken from every app on the machine). Offering it here would offer a
 *    shortcut that cannot be saved.
 */

/** A keyboard event as the recorder sees it. */
function ev(over: Partial<Parameters<typeof chordFromEvent>[0]>) {
  return {
    key: '', code: '', metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...over,
  }
}

describe('chordFromEvent', () => {
  it('records a modifier + letter chord', () => {
    expect(chordFromEvent(ev({ key: 'k', code: 'KeyK', metaKey: true, shiftKey: true })))
      .toBe('Command+Shift+K')
  })

  it('records Space and function keys', () => {
    expect(chordFromEvent(ev({ key: ' ', code: 'Space', metaKey: true }))).toBe('Command+Space')
    expect(chordFromEvent(ev({ key: 'F13', code: 'F13', altKey: true }))).toBe('Alt+F13')
  })

  it('records digits by physical key, so a shifted digit is still the digit', () => {
    // Shift+2 reports key '@' on a US layout; the accelerator must name the KEY.
    expect(chordFromEvent(ev({ key: '@', code: 'Digit2', metaKey: true, shiftKey: true })))
      .toBe('Command+Shift+2')
  })

  it('returns nothing for a modifier-only press — the recorder keeps listening', () => {
    for (const k of ['Meta', 'Control', 'Alt', 'Shift']) {
      expect(chordFromEvent(ev({ key: k, code: k, metaKey: true })), k).toBe('')
    }
  })

  it('returns nothing for a bare key — the shell would refuse to bind it', () => {
    expect(chordFromEvent(ev({ key: 'k', code: 'KeyK' }))).toBe('')
    expect(chordFromEvent(ev({ key: ' ', code: 'Space' }))).toBe('')
    expect(chordFromEvent(ev({ key: 'F13', code: 'F13' }))).toBe('')
  })

  it('orders modifiers canonically, so the same chord is one string', () => {
    // Two presses of the same combination must not produce two different stored values.
    const all = ev({ key: 'k', code: 'KeyK', metaKey: true, ctrlKey: true, altKey: true, shiftKey: true })
    expect(chordFromEvent(all)).toBe('Command+Control+Alt+Shift+K')
  })
})

describe('formatChord', () => {
  it('renders the default the way a Mac user reads it', () => {
    expect(formatChord(DEFAULT_PUSH_TO_TALK_CHORD)).toBe('⌘⇧Space')
  })

  it('maps every modifier spelling to a symbol', () => {
    expect(formatChord('Control+Option+M')).toBe('⌃⌥M')
    expect(formatChord('CmdOrCtrl+Shift+K')).toBe('⌘⇧K')
  })

  it('leaves an unknown token visible rather than dropping it', () => {
    // Better a chord that reads oddly than one that silently displays fewer keys than it
    // binds — the user would press the wrong combination.
    expect(formatChord('Hyper+K')).toBe('HyperK')
  })

  it('is empty for an empty chord', () => {
    expect(formatChord('')).toBe('')
  })
})

describe('the Settings control binds before it saves', () => {
  it('a refused chord is not stored', () => {
    // The ORDER is the property: storing first would leave the setting claiming a
    // shortcut that does nothing, and a conflict would only surface at the next launch
    // with the old chord already thrown away. jsdom cannot press a global chord, so this
    // is pinned at the source.
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/VoicePanel.tsx'), 'utf8')
    const row = src.slice(src.indexOf('function ChordRow'))
    const bindAt = row.indexOf('await bindChord(chord)')
    const saveAt = row.indexOf('onChange(chord, flash)')
    expect(bindAt).toBeGreaterThan(-1)
    expect(saveAt).toBeGreaterThan(-1)
    expect(bindAt).toBeLessThan(saveAt)
    // And the refusal returns early rather than falling through to the save.
    expect(row).toMatch(/if \(!r\.ok\) \{ setError\(r\.reason\); return \}/)
  })

  it('the config path it patches is the one the loader reads', () => {
    // The call-site check: a field wired through every config point but patched under a
    // different name is an inert control.
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/VoicePanel.tsx'), 'utf8')
    expect(src).toMatch(/patch\('push_to_talk_chord'/)
  })
})
