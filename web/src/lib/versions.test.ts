import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { VERSION, compareVersions } from './versions'

// ── The web orders versions as the gateway and the desktop app do ───────────────────────────────
//
// The web decides one thing by a version's order: whether the desktop app is older than the gateway
// it uses (`AppCheckOnGatewayAhead`). It reads the one table the gateway's and the desktop's
// comparators read in their own tests, so the three cannot drift apart unseen.

const repo = resolve(__dirname, '../../..')
const table = JSON.parse(readFileSync(resolve(repo, 'tests/fixtures/version_order.json'), 'utf8')) as {
  rows: [string, string, -1 | 0 | 1][]
  unreadable: string[]
}

describe('versions', () => {
  it.each(table.rows)('%s against %s orders %d', (a, b, order) => {
    expect(compareVersions(a, b)).toBe(order)
    expect(compareVersions(b, a)).toBe(order === 0 ? 0 : -order)
  })

  it.each(table.unreadable)('reads %j as no version', (text) => {
    expect(compareVersions(text, '0.3.8')).toBeNull()
  })

  it("reads a version by the desktop app's own pattern, character for character", () => {
    const desktop = readFileSync(resolve(repo, 'desktop/appUpdate.js'), 'utf8')
    const theirs = /^const VERSION = (\/.*\/i);$/m.exec(desktop)?.[1]
    expect(theirs, 'the desktop app has a VERSION pattern').toBeTruthy()
    expect(String(VERSION)).toBe(theirs)
  })
})
