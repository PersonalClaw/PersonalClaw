import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { HEX, RAW_PX, PX_OK_CONTEXT, CALC_WITH_TOKEN, lineViolations } from './tokenLintRule'

// ── Two-sided pin: one token-lint rule, two consumers ──────────────────────
// APE-4 verifies an app's `quality.designSystem: "v2"` claim by running token-lint
// over the app BUNDLE's frontend — from Python, in the apps-repo CI, with only a
// core wheel installed. So the patterns had to become data
// (src/personalclaw/apps/token_lint_rules.json) with a thin consumer on each side.
//
// Two thin consumers of one rule is fine. Two rules that drift apart is the exact
// declared-vs-actual defect this atom exists to catch: the host would lint one way,
// an app's badge would be earned another way, and nothing would say so. This test
// is the thing that says so.
//
// vitest runs from web/, so the packaged JSON is two levels up.
const RULES_PATH = join(process.cwd(), '..', 'src', 'personalclaw', 'apps', 'token_lint_rules.json')

interface Rules {
  hex: string
  raw_px: string
  px_ok_context: string
  calc_with_token: string
}

function loadRules(): Rules {
  return JSON.parse(readFileSync(RULES_PATH, 'utf8')) as Rules
}

describe('token-lint rule parity (TS ↔ packaged JSON)', () => {
  const rules = loadRules()

  it('the canonical rule file was actually found and carries all four patterns', () => {
    // Vacuity floor: a missing/empty file would make every equality below compare
    // undefined to undefined and pass. Read it and require real content.
    expect(readFileSync(RULES_PATH, 'utf8').length, RULES_PATH).toBeGreaterThan(200)
    for (const k of ['hex', 'raw_px', 'px_ok_context', 'calc_with_token'] as const) {
      expect(typeof rules[k], `missing pattern: ${k}`).toBe('string')
      expect(rules[k].length, `empty pattern: ${k}`).toBeGreaterThan(5)
    }
  })

  it('every TS pattern is byte-identical to its canonical JSON source', () => {
    expect(HEX.source).toBe(rules.hex)
    expect(RAW_PX.source).toBe(rules.raw_px)
    expect(PX_OK_CONTEXT.source).toBe(rules.px_ok_context)
    expect(CALC_WITH_TOKEN.source).toBe(rules.calc_with_token)
  })

  it('the JSON patterns, recompiled, reach the same verdict as the TS rule', () => {
    // Equal `.source` proves the strings match; this proves the strings BEHAVE.
    // A corpus with a known verdict per line, so neither side can be vacuously clean.
    const corpus: [string, ('hex' | 'px')[]][] = [
      ["  const c = '#1a2b3c'", ['hex']],
      ['  <div style={{ fontSize: 13px }}>', ['px']],
      ["  <div style={{ maxWidth: 'calc(var(--w) + 160px)' }}>", []],
      ['  <div style={{ gridTemplateColumns: minmax(0, 120px) }}>', []],
      ['  <div className="bg-surface-high text-on-surface">', []],
      ["  <div style={{ color: '#fff', padding: 4px }}>", ['hex', 'px']],
    ]
    const jsonHex = new RegExp(rules.hex)
    const jsonPx = new RegExp(rules.raw_px)
    const jsonOk = new RegExp(rules.px_ok_context)
    const jsonCalc = new RegExp(rules.calc_with_token)
    const viaJson = (line: string): ('hex' | 'px')[] => {
      const out: ('hex' | 'px')[] = []
      if (jsonHex.test(line)) out.push('hex')
      if (jsonPx.test(line) && !jsonCalc.test(line) && !jsonOk.test(line)) out.push('px')
      return out
    }
    for (const [line, expected] of corpus) {
      expect(lineViolations(line), `TS rule on: ${line}`).toEqual(expected)
      expect(viaJson(line), `JSON rule on: ${line}`).toEqual(expected)
    }
    // …and the corpus is not all-clean, so "agrees" is not "both found nothing".
    expect(corpus.filter(([, e]) => e.length).length).toBeGreaterThanOrEqual(3)
  })
})
