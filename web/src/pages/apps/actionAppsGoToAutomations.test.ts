import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// ── An automation action has no app-level form: its Configure goes to the Automations page ───────
//
// Measured before the fix: Apps › A2A Agent Call › Configure offered the action's own fields (Agent
// URL, Skill, Message), saved them as the app's settings, and nothing read them: each fire hands
// the action its automation's values, so every automation set up that way failed "A2A action is
// missing 'url' field". An action's `settingsSchema` is its form in each automation that runs it,
// so the gateway refuses the app-level door (409) and says `configuredPerAutomation: true` on the
// app row. The Library follows it: the action is "Set up in an automation", and it opens the
// Automations page, where an automation's action takes those fields.
//
// Pinned at the SOURCE level like `perInstanceAppsGoToProviders`: labels and one routing choice in
// one file. Comments are stripped so prose ABOUT the change can neither satisfy nor trip the rail.

const strip = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
const code = strip(readFileSync(resolve(__dirname, 'AppsSection.tsx'), 'utf8'))
const apps = readFileSync(resolve(__dirname, '../../../../src/personalclaw/dashboard/handlers/apps.py'), 'utf8')

describe('Configure on an automation action', () => {
  it('routes to the Automations page instead of opening a form the gateway refuses', () => {
    const configure = code.slice(code.indexOf("case 'configure':"), code.indexOf("case 'update':"))
    expect(configure).toMatch(/if \(app\.configuredPerAutomation\) \{ nav\('triggers'\); return \}[\s\S]*setConfigFor/)
  })

  it('carries the flag from the app row into every action it dispatches', () => {
    expect(code).toMatch(/configuredPerAutomation: !!a\.configuredPerAutomation/)
    const actionObjects = code.match(/const app = \{ name: item\.name[^}]*\}/g) ?? []
    expect(actionObjects.length, 'the card and its menu both build one').toBe(2)
    for (const obj of actionObjects) expect(obj).toContain('configuredPerAutomation: item.configuredPerAutomation')
  })

  it('names the action for what it does, on every surface that offers it', () => {
    expect(code).toMatch(/app\.configuredPerAutomation \? 'Set up in an automation'/)
    expect((code.match(/> Set up in an automation<\/Button>/g) ?? []).length, 'both detail-panel buttons').toBe(2)
    expect(code).toMatch(/onSetUpInAnAutomation=\{\(\) => nav\('triggers'\)\}/)
    expect(code, 'the native card menu offers it, not "always on"').toMatch(/item\.configuredPerInstance \|\| item\.configuredPerAutomation\s*\n?\s*\? <MenuRow/)
  })

  it('the flag it follows is the one the gateway sends', () => {
    expect((apps.match(/"configuredPerAutomation": per_automation/g) ?? []).length, 'installed and bundled rows').toBe(2)
    expect(apps, 'and the app-level config doors refuse such an app').toMatch(/refusal = _configured_elsewhere\(manifest\)/)
  })
})
