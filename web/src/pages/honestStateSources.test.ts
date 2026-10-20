// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { stripComments } from '../design/tokenLintRule'
import { readSource } from '../test/sourceTree'

// ── What only the source can say about an honest surface ───────────────────────────────────────
//
// The surfaces of this family are driven in `pages/honestStateSurfaces.test.tsx`, and a fetcher
// that resolves its own rejection is the load-error census's to count. What is left here is what
// neither can reach: gates and sentences on a surface no test mounts whole, and one claim a file
// makes about itself in prose. Each pin was kept because its mutant survived everything else.

const SRC = join(process.cwd(), 'src')
/** A file's code, comments blanked line for line, so prose quoting an old defect is not code. */
const codeOf = (rel: string) => stripComments(readSource(join(SRC, rel))).code.join('\n')

// ── The design cockpit's token read: one dropped rejection, five false statements ───────────────
//
// It loaded with `.catch(() => {})`, and five consumers inferred their state from `tokens ===
// null`: both views said "Loading tokens…" forever (their rendering is driven in the surfaces
// file), the CSS export was dead with no reason, and the JSON and DESIGN.md exports were NOT gated,
// so a failed read handed over a well-formed file whose content was wrong. The JSON row's own copy
// is "feed into any build pipeline", where `"resolved": {}` reads as a deliberate empty set.
//
// 🪤 THE DESIGN.md GATE IS `!authoredDoc && !tokens`. The download prefers the loop's authored
// DESIGN.md and only falls back to building one from the tokens, so gating on `!tokens` alone
// would disable a download that was going to be right.
// 🪤 AND THE REASON TEXT BRANCHES: `!tokens` is true while the read is in flight too, so a flat
// "could not be read" would trade one false statement for another.

describe('the design cockpit keeps its token read failure, and its exports say so', () => {
  const code = codeOf('pages/loops/DesignCockpitPage.tsx')
  /** An export row's JSX, from its title to its closing `/>`. */
  const row = (title: string) => {
    const m = code.match(new RegExp(`title="${title.replace(/[.()]/g, '\\$&')}"[\\s\\S]{0,900}?/>`))?.[0] ?? ''
    expect(m, `the ${title} row must be matched before it can be asserted on`).not.toBe('')
    return m
  }

  it('the rejection is recorded, and a retry clears the last one', () => {
    expect(code, 'uLoopDesignTokens must not swallow').not.toMatch(/uLoopDesignTokens\([^)]*\)[\s\S]{0,120}?\.catch\(\(\) => \{\}\)/)
    expect(code).toMatch(/\.catch\(\(e\) => setTokensErr\(e/)
    expect(code, 'otherwise the error line outlives the error').toMatch(/setTokensErr\(null\)\s*\n\s*api\.uLoopDesignTokens\(/)
  })

  it('the JSON export of an unread set is gated, and says why', () => {
    expect(row('Token set (JSON)')).toMatch(/disabled=\{!tokens\} disabledReason=\{unread\}/)
  })

  it('DESIGN.md is gated on both conditions, not the token read alone', () => {
    expect(row('DESIGN.md')).toMatch(/disabled=\{!authoredDoc && !tokens\}/)
  })

  it('an export row hands its reason to the Button', () => {
    expect(code).toMatch(/function ExportRow\([^)]*disabledReason/)
    expect(code).toMatch(/disabled=\{disabled\} disabledReason=\{disabledReason\}/)
  })

  it('the reason tells a read in flight from one that failed', () => {
    expect(code, 'a mid-load gate must not claim the read failed').toMatch(/const unread = tokensErr\s*\n?\s*\?/)
    expect(code).toMatch(/'Still reading the token set — this becomes available once it loads\.'/)
  })
})

// ── The routing panel's doc comment ─────────────────────────────────────────────────────────────
//
// The page told a user its own controls did nothing ("Observation only"); its header is driven in
// the surfaces file. The file's doc comment carried the same claim ("This surface ONLY visualizes
// — it never changes routing"), so both were corrected together: left alone, the file contradicts
// itself for the next reader.

describe('the routing panel describes itself as it is', () => {
  it('its doc comment names both halves, and claims neither is all of it', () => {
    const raw = readSource(join(SRC, 'pages/settings/RoutingPanel.tsx'))
    const doc = raw.slice(0, raw.indexOf('export function RoutingPanel'))
    expect(/ONLY visualizes/.test(doc), 'the doc comment carried the same stale claim').toBe(false)
    expect(/never changes routing/.test(doc)).toBe(false)
    expect(doc).toMatch(/DECIDES/)
  })
})
