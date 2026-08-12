import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A settings panel may not present fabricated values as saved state ────────────
//
// Four panels read their section of the config with
// `api.personalclawConfig().then(c => (c.<section> ?? {})).catch(() => ({}))`. The catch made a FAILED
// read resolve with an empty section, so every control rendered at its fallback — indistinguishable from
// "this is what you saved" — and the panel offered to edit values it had never loaded.
//
// Measured with `GET /api/config` at 500 and a cold sessionStorage, counting editable controls on the page:
//
//   route                    before                          after
//   #/settings/agent         9 editable controls, no error    0 controls · "Couldn't load your settings" + Retry
//   #/settings/ambient       6                                0 · same
//   #/settings/guardrails    6                                0 · same
//   #/settings/legibility    2                                0 · same
//
// 🔑 The load-bearing half is not the copy — it is that the FORM IS GONE. Cycle 91's lesson on the
// incident kill switch applies to every settings form: ask what a swallowed read lets the user DO. Here it
// let them "change" a setting whose current value was unknown.
//
// Out of scope on purpose (different shapes, each needing its own measurement): panels whose reads
// substitute `null` and already branch on it (`ChatPanel`, `DurabilityPanel`, `FeedbackPanel`,
// `InboxSettingsPanel`, `MemoryPanel`), and `AppsPanel`, whose `'apps'` key was fixed by the cache-key
// sweep instead.

const SETTINGS = join(process.cwd(), 'src', 'pages', 'settings')
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** The four panels that read a config SECTION and render a form from it. */
const PANELS: Array<[string, string]> = [
  ['AgentDefaultsPanel.tsx', 'agent'],
  ['AmbientPanel.tsx', 'ambient'],
  ['GuardrailsPanel.tsx', 'guardrails'],
  ['LegibilityPanel.tsx', 'legibility'],
]

describe('a settings panel reports a failed config read', () => {
  it.each(PANELS)('%s does not fabricate an empty %s section', (file, section) => {
    const src = strip(readFileSync(join(SETTINGS, file), 'utf8'))
    expect(src, `${file} must still read its section`).toMatch(
      new RegExp(`personalclawConfig\\(\\)[\\s\\S]{0,90}c\\.${section}`),
    )
    expect(/personalclawConfig\(\)[\s\S]{0,160}\.catch\(\(\) => \(\{/.test(src),
      `${file}: an empty section is indistinguishable from saved defaults`).toBe(false)
  })

  it.each(PANELS)('%s reads the hook error and replaces the form with it', (file) => {
    const src = strip(readFileSync(join(SETTINGS, file), 'utf8'))
    expect(src, `${file}: the rejection must be read`).toMatch(/error:\s*loadErr/)
    expect(src, `${file}: and reported`).toMatch(/<LoadError what="settings" error=\{loadErr\} onRetry=\{refresh\}/)
    // Before the skeleton gate, or a failed read spins the skeleton forever.
    const errAt = src.search(/<LoadError\b/)
    const skelAt = src.search(/<FormSkeleton\b/)
    expect(skelAt, `${file} must still have a loading state`).toBeGreaterThan(-1)
    expect(errAt, `${file}: the error branch must precede the skeleton`).toBeLessThan(skelAt)
  })

  it('reads the real files (not vacuously green)', () => {
    for (const [file] of PANELS) {
      expect(readFileSync(join(SETTINGS, file), 'utf8').length, file).toBeGreaterThan(1500)
    }
  })
})
