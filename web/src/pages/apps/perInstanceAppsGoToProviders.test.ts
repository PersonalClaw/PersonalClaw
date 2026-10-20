import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// ── An app configured per instance has no app-level form: its Configure goes to Settings → Providers ─
//
// Measured before the fix: Apps → Ollama → Configure saved `apps/ollama-models/data/config.json`
// (200 OK) while chat read the instance in `config.json` `providers[]`, which stayed at localhost
// and kept failing after a restart. A multi-instance provider's `settingsSchema` describes ONE
// INSTANCE; no factory reads an app-level copy, so the gateway now refuses that door (409) and says
// `configuredPerInstance: true` on the app row. The Library must follow it: the action is
// "Manage instances", and it opens the one place instances are added, edited, tested and removed.
//
// Pinned at the SOURCE level like its neighbours (`uninstallMiddleRung`, `appToggleOneVocabulary`):
// labels and one routing choice in one file. Comments are stripped so prose ABOUT the change can
// neither satisfy nor trip the rail.

const strip = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
const code = strip(readFileSync(resolve(__dirname, 'AppsSection.tsx'), 'utf8'))
const apps = readFileSync(resolve(__dirname, '../../../../src/personalclaw/dashboard/handlers/apps.py'), 'utf8')

describe('Configure on an app configured per instance', () => {
  it('routes to Settings → Providers instead of opening a form the gateway refuses', () => {
    const configure = code.slice(code.indexOf("case 'configure':"), code.indexOf("case 'update':"))
    expect(configure, 'the per-instance branch comes first').toMatch(
      /if \(app\.configuredPerInstance\) \{ nav\('settings\/providers'\); return \}[\s\S]*setConfigFor/,
    )
  })

  it('carries the flag from the app row into every action it dispatches', () => {
    expect(code).toMatch(/configuredPerInstance: !!a\.configuredPerInstance/)
    const actionObjects = code.match(/const app = \{ name: item\.name[^}]*\}/g) ?? []
    expect(actionObjects.length, 'the card and its menu both build one').toBe(2)
    for (const obj of actionObjects) expect(obj).toContain('configuredPerInstance: item.configuredPerInstance')
  })

  it('names the action for what it does, on every surface that offers it', () => {
    // The card menu (native + non-native), the context menu, and the detail panel.
    expect((code.match(/Manage instances/g) ?? []).length).toBeGreaterThanOrEqual(5)
    expect(code).toMatch(/onManageInstances=\{\(\) => nav\('settings\/providers'\)\}/)
  })

  it('the flag it follows is the one the gateway sends', () => {
    // Cross-checked against the backend, so the two cannot drift into a label nothing sets.
    expect(apps).toMatch(/"configuredPerInstance": per_instance/)
    expect(apps, 'and the app-level config door refuses such an app').toMatch(/status=409/)
  })
})
