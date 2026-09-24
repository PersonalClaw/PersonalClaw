import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(join(__dirname, 'FilesSection.tsx'), 'utf8')

describe('the Python content-search fallback', () => {
  it('states each reduced-fidelity behavior beside the engine name', () => {
    expect(source).toContain('python fallback: substring matching, not regex')
    expect(source).toContain('ignores .gitignore')
    expect(source).toContain('approximate globs')
  })
})
