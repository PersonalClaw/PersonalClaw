import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  BLAST_RADIUS_FACET_ORDER, approvalRiskOf, blastRadiusLine, blastRadiusOf, establishedFacets,
  mayDestroy,
} from './approvalMeta'

// ── The radius is the backend's; this module decodes it and holds its words ─────────────────
//
// What a pending call can touch is composed where the approval is registered, from the same
// reading of the call that gives its risk (`approval_brief.call_blast_radius`). The surfaces
// here render it; they never guess one from a tool's name.

describe('blastRadiusOf — the one decoder for the wire', () => {
  it('reads the four facets the backend sends', () => {
    expect(blastRadiusOf({ writes: true, network: false, shell: false, saysReadOnly: false, readOnly: false }))
      .toEqual({ writes: true, network: false, shell: false, saysReadOnly: false, readOnly: false })
    expect(blastRadiusOf({ writes: false, network: false, shell: false, saysReadOnly: false, readOnly: true }))
      .toEqual({ writes: false, network: false, shell: false, saysReadOnly: false, readOnly: true })
  })

  it('is no radius at all for anything it cannot read — never a partial claim', () => {
    for (const raw of [undefined, null, '', 'yes', 1, [], {}, { writes: true },
      { writes: true, network: false, shell: false, readOnly: false },
      { writes: 'true', network: false, shell: false, saysReadOnly: false, readOnly: false }]) {
      expect(blastRadiusOf(raw), JSON.stringify(raw)).toBeUndefined()
    }
  })

  it('keeps only the four facets, whatever else the object carries', () => {
    expect(blastRadiusOf({ writes: false, network: true, shell: false, saysReadOnly: false, readOnly: false, extra: true }))
      .toEqual({ writes: false, network: true, shell: false, saysReadOnly: false, readOnly: false })
  })
})

describe('approvalRiskOf / mayDestroy — the effective risk vocabulary', () => {
  it('knows the declared levels and the shell command nobody could check', () => {
    for (const risk of ['safe', 'caution', 'destructive', 'unchecked']) {
      expect(approvalRiskOf(risk)).toBe(risk)
    }
  })

  it('a level this build has never heard of, or none, is no evidence', () => {
    for (const raw of ['', 'catastrophic', undefined, null, 3]) {
      expect(approvalRiskOf(raw)).toBeUndefined()
    }
  })

  it('a destructive call and one nobody could check are the two that may destroy', () => {
    expect(mayDestroy('destructive')).toBe(true)
    expect(mayDestroy('unchecked')).toBe(true)
    for (const risk of ['safe', 'caution', undefined] as const) expect(mayDestroy(risk)).toBe(false)
  })
})

describe('approvalMeta.ts must stay a pure leaf that nothing can gate on', () => {
  // The durable invariant: no runtime imports, so it can neither reach the API client nor be
  // reached by an approval decision path, and it never reads a command or a tool name itself.
  const source = readFileSync(join(process.cwd(), 'src/pages/chat/approvalMeta.ts'), 'utf8')

  it('the rail is not vacuous — it is reading the real module', () => {
    expect(source.length).toBeGreaterThan(1000)
    expect(source).toContain('export function blastRadiusOf')
  })

  it('has no runtime imports at all (type-only)', () => {
    const runtimeImports = source
      .split('\n')
      .filter((l) => /^\s*import\s/.test(l) && !/^\s*import\s+type\s/.test(l))
    expect(runtimeImports).toEqual([])
  })

  it('performs no I/O and reads no ambient state', () => {
    for (const forbidden of ['fetch(', 'localStorage', 'sessionStorage', 'Date.now', 'Math.random', 'window.']) {
      expect(source).not.toContain(forbidden)
    }
  })

  it('never derives a facet from a tool name or a command string', () => {
    for (const forbidden of ['_HINTS', 'deriveBlastRadius', 'normalizeToolName', 'rm -rf', "split('|')"]) {
      expect(source).not.toContain(forbidden)
    }
  })
})

// ── The facet vocabulary the surfaces share ─────────────────────────────────────────────────

describe('establishedFacets / blastRadiusLine', () => {
  it('the order list covers every facet of the radius — a fifth cannot go unrendered', () => {
    const all = blastRadiusOf({ writes: true, network: true, shell: true, saysReadOnly: false, readOnly: true })
    expect(all).toBeDefined()
    expect([...BLAST_RADIUS_FACET_ORDER].sort()).toEqual(Object.keys(all as object).sort())
  })

  it('returns ONLY established facets, in the declared order', () => {
    const facets = establishedFacets({ writes: true, shell: true, network: false, saysReadOnly: false, readOnly: false })
    expect(facets.map((f) => f.key)).toEqual(['writes', 'shell'])
    expect(facets.map((f) => f.label)).toEqual(['Writes files', 'Runs a command'])
  })

  it('yields nothing at all for an undefined radius — the unknown channel stays silent', () => {
    expect(establishedFacets(undefined)).toEqual([])
    expect(blastRadiusLine(undefined)).toBe('')
  })

  it('never renders a false facet as a negative claim', () => {
    const none = establishedFacets({ writes: false, shell: false, network: false, saysReadOnly: false, readOnly: false })
    expect(none).toEqual([])
    expect(blastRadiusLine({ writes: false, shell: false, network: false, saysReadOnly: false, readOnly: false })).toBe('')
  })

  it('every facet has a label and a spelled-out detail, and none of them is a verdict', () => {
    const facets = establishedFacets({ writes: true, shell: true, network: true, saysReadOnly: true, readOnly: true })
    expect(facets).toHaveLength(5)
    for (const f of facets) {
      expect(f.label.length, f.key).toBeGreaterThan(3)
      expect(f.detail.length, f.key).toBeGreaterThan(10)
      for (const advocacy of [/safe to/i, /recommend/i, /harmless/i, /no risk/i, /probably/i]) {
        expect(`${f.label} ${f.detail}`, f.key).not.toMatch(advocacy)
      }
    }
  })

  it("says a server's read-only label as the server's word, apart from an established read", () => {
    const facets = establishedFacets({ writes: false, shell: false, network: false, saysReadOnly: true, readOnly: false })
    expect(facets.map((f) => f.label)).toEqual(['Server says it only reads'])
    expect(facets[0].detail).toMatch(/server you trust/)
  })

  it('renders the compact line from the same words as the chips', () => {
    const radius = { writes: true, shell: true, network: false, saysReadOnly: false, readOnly: false }
    const line = blastRadiusLine(radius)
    expect(line).toBe('writes files, runs a command')
    for (const f of establishedFacets(radius)) expect(line).toContain(f.label.toLowerCase())
  })
})
