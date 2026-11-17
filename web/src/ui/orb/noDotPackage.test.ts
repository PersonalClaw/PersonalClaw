// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { filesUnder, readSource } from '../../test/sourceTree'

// ── No package draws either orb (#6117) ─────────────────────────────────────────────────────────
//
// The chat's orb and the buddy's once drew the `thinking-orbs` package's dots. Both now draw the
// halftone orb (`ui/orb/halftone.ts`), the project's own drawing, so the package is gone from the
// web app's dependencies and nothing in its source imports it.

describe('no package draws the orb', () => {
  it('the web app does not depend on the dot package', () => {
    const pkg = JSON.parse(readFileSync(join(process.cwd(), 'package.json'), 'utf8')) as Record<string, Record<string, string> | undefined>
    for (const field of ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']) {
      expect(Object.keys(pkg[field] ?? {}), field).not.toContain('thinking-orbs')
    }
  })

  it('no source file imports it', () => {
    const files = filesUnder(join(process.cwd(), 'src'), (name) => /\.(tsx?|jsx?)$/.test(name))
    expect(files.length, 'the scan read no source').toBeGreaterThan(100)
    for (const file of files) expect(readSource(file), file).not.toMatch(/from\s+['"]thinking-orbs(?:\/[^'"]*)?['"]|import\(\s*['"]thinking-orbs/)
  })
})
