import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SCAN_RULE_GLOSS, ruleGloss } from './scanFindings'

// ── The gloss map is DATA, shared with the Python CLI ───────────────────────────────────────────
//
// The sentences used to be a literal in `scanFindings.ts`. Then `personalclaw skills install`
// needed the same map from Python (#2633) and could not have it: the wheel ships `web/dist`, not
// `web/src`. Duplicating the map in Python is the two-copies-of-one-fact defect #2535 removed, so
// the map moved to `src/personalclaw/scan_rule_gloss.json` — packaged for the wheel, imported here
// at build time. `tests/test_scan_rule_gloss.py` proves the Python half, including reading the
// file back out of a built wheel.
//
// 🪤 THIS SIDE FAILS DIFFERENTLY, AND WORSE. Python's loader RAISES on an empty map. A Vite build
// with an emptied JSON succeeds: `SCAN_RULE_GLOSS` becomes `{}`, `ruleGloss` answers `''` for
// everything, and every gloss silently vanishes from both consent surfaces — the exact silence the
// map exists to remove, arriving as a green build. So the floor is asserted here, not assumed.

const GLOSS_JSON = join(process.cwd(), '..', 'src', 'personalclaw', 'scan_rule_gloss.json')

describe('the gloss map this build actually carries', () => {
  it('is populated — an empty map would be a silent, green regression', () => {
    expect(Object.keys(SCAN_RULE_GLOSS).length, 'the map must not be reachable-but-empty')
      .toBeGreaterThanOrEqual(15)
    for (const [rule, text] of Object.entries(SCAN_RULE_GLOSS)) {
      expect(typeof text, `${rule} must gloss to a string`).toBe('string')
      expect(text.length, `${rule} has an empty gloss`).toBeGreaterThan(0)
    }
  })

  it('came from the shared JSON, not from a literal in this module', () => {
    // The keys and sentences must match the file byte for byte. If someone re-inlines the map,
    // this passes only while the two agree — and `test_scan_rule_gloss.py` reds on the inlining
    // itself, so the pair covers both drift and duplication.
    const raw = JSON.parse(readFileSync(GLOSS_JSON, 'utf8')) as Record<string, unknown>
    const expected = Object.fromEntries(
      Object.entries(raw).filter(
        (e): e is [string, string] => !e[0].startsWith('_') && typeof e[1] === 'string',
      ),
    )
    expect(SCAN_RULE_GLOSS).toEqual(expected)
  })

  it('never serves the file’s own rationale as a rule', () => {
    // JSON cannot hold comments, so the file carries its `_comment` prose as a key (the same
    // convention as `apps/token_lint_rules.json`). A metadata key leaking into the map would put
    // an array of maintainer notes on a user's consent screen.
    expect(ruleGloss('_comment'), 'metadata is not a rule').toBe('')
    expect(Object.keys(SCAN_RULE_GLOSS).some((k) => k.startsWith('_'))).toBe(false)
  })

  it('answers empty for an unknown rule rather than echoing the name', () => {
    // Preserved from the pre-move accessor: a name repeated as though it explained something is
    // the defect, not the fix.
    expect(ruleGloss('no_such_rule_exists')).toBe('')
    expect(ruleGloss('')).toBe('')
    expect(ruleGloss('python_exec')).toBe('This code runs an external program on your machine.')
  })
})
