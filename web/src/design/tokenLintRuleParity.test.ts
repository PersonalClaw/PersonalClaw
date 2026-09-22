import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  HEX,
  RAW_PX,
  PX_OK_CONTEXT,
  CALC_WITH_TOKEN,
  lineViolations,
  stripComments,
} from './tokenLintRule'

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
const APPS_DIR = join(process.cwd(), '..', 'src', 'personalclaw', 'apps')
const RULES_PATH = join(APPS_DIR, 'token_lint_rules.json')
// A regex can be shared as data; a LEXER cannot, so the comment-state tracker exists
// twice (stripComments / strip_comments) and its BEHAVIOUR is shared as data instead.
// Both languages' tests run this file — see its own _comment (#3337).
const CASES_PATH = join(APPS_DIR, 'token_lint_comment_cases.json')

interface Rules {
  hex: string
  raw_px: string
  px_ok_context: string
  calc_with_token: string
}

interface CommentCase {
  name: string
  why: string
  lines: string[]
  expected: ('hex' | 'px')[][]
  end_state: 'code' | 'block'
}

function loadRules(): Rules {
  return JSON.parse(readFileSync(RULES_PATH, 'utf8')) as Rules
}

function loadCommentCases(): CommentCase[] {
  return (JSON.parse(readFileSync(CASES_PATH, 'utf8')) as { cases: CommentCase[] }).cases
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

describe('token-lint comment-state parity (TS ↔ packaged cases)', () => {
  const cases = loadCommentCases()
  const rules = loadRules()

  it('the shared case file was found and carries both directions', () => {
    // Vacuity floor: an empty `cases` array would make every `it.each` below vanish
    // and the suite would still be green. Require real cases, and require BOTH
    // directions — a file with only `clean_*` cases would green a tracker that
    // stopped catching raw hexes entirely, which is the whole risk of this change.
    expect(cases.length, CASES_PATH).toBeGreaterThanOrEqual(10)
    expect(cases.filter((c) => c.expected.some((e) => e.length)).length).toBeGreaterThanOrEqual(5)
    expect(cases.filter((c) => c.expected.every((e) => e.length === 0)).length).toBeGreaterThanOrEqual(5)
    for (const c of cases) {
      expect(c.expected.length, `${c.name}: one expectation per line`).toBe(c.lines.length)
      expect(['code', 'block'], `${c.name}: end_state`).toContain(c.end_state)
    }
  })

  it.each(cases.map((c) => [c.name, c] as const))(
    'the TS tracker reaches the declared verdict: %s',
    (_name, c) => {
      const { code, endState } = stripComments(c.lines.join('\n'))
      expect(code.length, `${c.name}: a line index must stay a line number`).toBe(c.lines.length)
      expect(endState, `${c.name}: ${c.why}`).toBe(c.end_state)
      const got = code.map((line) => lineViolations(line))
      expect(got, `${c.name}: ${c.why}`).toEqual(c.expected)
    },
  )

  it('the JSON patterns, recompiled, reach the same verdict over the comment cases', () => {
    // The tracker decides WHAT is code; the patterns decide what is a violation. This
    // runs the recompiled-from-JSON patterns over the tracker's output, so a drift in
    // either half shows up here rather than only in the app-bundle gate.
    const jsonHex = new RegExp(rules.hex)
    const jsonPx = new RegExp(rules.raw_px)
    const jsonOk = new RegExp(rules.px_ok_context)
    const jsonCalc = new RegExp(rules.calc_with_token)
    for (const c of cases) {
      const got = stripComments(c.lines.join('\n')).code.map((line) => {
        const out: ('hex' | 'px')[] = []
        if (jsonHex.test(line)) out.push('hex')
        if (jsonPx.test(line) && !jsonCalc.test(line) && !jsonOk.test(line)) out.push('px')
        return out
      })
      expect(got, `JSON rules on case: ${c.name}`).toEqual(c.expected)
    }
  })
})
