/**
 * #615 — the Apps tile's stat must count what its caption says.
 *
 * It filtered to non-provider apps and captioned the result "installed apps",
 * so an instance with 33 installed (all providers) read "0 installed apps".
 * The tile now counts the apps that expose settings in the panel it opens
 * ("apps with settings") and carries the installed total as context
 * ("of 33 installed"), so 0 cannot read as "you have no apps".
 *
 * Source-level by necessity, like settingsHubCoverage: SETTINGS_WIDGETS holds
 * hooks and the module imports real panels, so importing it drags the panel
 * tree into a unit test. Comments are STRIPPED before asserting — this repo
 * has repeatedly had rails count their own prose (the fix's explanatory
 * comment necessarily names the old caption).
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const src = readFileSync(join(process.cwd(), 'src/pages/settings/settingsWidgets.tsx'), 'utf8')

const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

/** The apps tile block: from its id line to the next widget id line. */
function appsTileBlock(): string {
  const start = src.indexOf("id: 'apps',")
  expect(start).toBeGreaterThan(-1)
  const rest = src.slice(start)
  const next = rest.slice(1).search(/^\s*id: '/m)
  return stripComments(next > 0 ? rest.slice(0, next + 1) : rest)
}

describe('the Apps settings tile counts what it says (#615)', () => {
  it('captions the stat with the subset it actually counts', () => {
    const block = appsTileBlock()
    // The stat's subject is the panel's subject: apps exposing settings here.
    expect(block).toContain("'app with settings'")
    expect(block).toContain("'apps with settings'")
    // The stat value is the configurable count, not the non-provider count.
    expect(block).toMatch(/BigStat\s+value=\{configurable\}/)
  })

  it('no longer captions a filtered count with the unqualified noun', () => {
    const block = appsTileBlock()
    // The measured lie: value={nonProvider.length} under "installed apps".
    expect(block).not.toMatch(/BigStat\s+value=\{nonProvider\.length\}/)
    expect(block).not.toContain("'installed apps'")
    expect(block).not.toContain("'installed app'")
  })

  it('keeps the installed total as context so 0 cannot read as "no apps"', () => {
    const block = appsTileBlock()
    expect(block).toContain('of ${data.length} installed')
    // And a truly empty instance is told where apps come from.
    expect(block).toContain('Nothing installed yet')
  })
})
