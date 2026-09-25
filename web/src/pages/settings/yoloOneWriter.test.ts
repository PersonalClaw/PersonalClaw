import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// ── `agent.yolo` has exactly ONE writer in the SPA, and it asks first ───────────────────────────
//
// The hub tile turned YOLO on in one click because it wrote the field through the GENERIC config
// patch — `setCfg('yolo', v)` → `api.patchConfig(\`agent.${key}\`, …)` — while the panel asked. A
// per-surface test catches the surfaces someone thought of; this is the census that catches the next
// one. The server refuses `true` without `confirm: true` too, so a stray writer now fails loudly at
// runtime — this rail is what makes it fail in CI first.
//
// Comments are stripped before matching: the explanation of this defect quotes the defect.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
  const p = join(d, n)
  if (statSync(p).isDirectory()) return walk(p)
  return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
})
const codeOf = (abs: string) => readFileSync(abs, 'utf8')
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const FILES = walk(SRC).map((abs) => ({ rel: relative(SRC, abs).split('\\').join('/'), code: codeOf(abs) }))
const filesMatching = (re: RegExp) => FILES.filter((f) => re.test(f.code)).map((f) => f.rel).sort()

describe('one writer for agent.yolo', () => {
  it('scans the whole SPA, not a handful of files (vacuity floor)', () => {
    expect(FILES.length).toBeGreaterThan(300)
    expect(FILES.some((f) => f.rel === 'lib/api.ts')).toBe(true)
  })

  it('the field is named in exactly one place — the API method that carries the consent', () => {
    expect(filesMatching(/['"`]agent\.yolo['"`]/)).toEqual(['lib/api.ts'])
  })

  it('that method has exactly one caller — the function that asks first', () => {
    expect(filesMatching(/\bapi\.setAgentYolo\(/)).toEqual(['pages/settings/agentYolo.ts'])
  })

  it('no generic config patcher is handed the `yolo` key', () => {
    // The measured shape: `setCfg('yolo', v)`. A generic patcher cannot know this key needs consent.
    expect(filesMatching(/\(\s*['"]yolo['"]\s*,/)).toEqual([])
  })

  it('both surfaces that show the switch go through that function', () => {
    const callers = filesMatching(/\bsetAgentYolo\(/).filter((f) => f !== 'pages/settings/agentYolo.ts')
    expect(callers).toEqual(['pages/settings/AgentDefaultsPanel.tsx', 'pages/settings/settingsWidgets.tsx'])
  })
})
