import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Form hints speak the user's language, not the wire's (AUD-NZ12) ──────────────────
//
// The auto-approve hint used to read "Run tools without approval prompts
// (approval_mode=auto)." — a config key, in a sentence whose whole job is explaining the
// switch to someone who has never seen the config. The parenthetical taught nothing a
// user could act on (the switch IS how you set it) and marked the form as
// developer-facing. Hints describe the EFFECT; keys stay in code.

describe('schedule form hints', () => {
  it('no hint prints a config key at the user', () => {
    const src = readFileSync(join(process.cwd(), 'src', 'pages', 'schedule', 'ScheduleForm.tsx'), 'utf8')
    const hints = [...src.matchAll(/hint="([^"]*)"/g)].map((m) => m[1])
    expect(hints.length, 'the scan must find the hints').toBeGreaterThanOrEqual(2)
    for (const h of hints) {
      expect(h, `a hint must not carry a config key: "${h}"`).not.toMatch(/\w+_\w+=|approval_mode/)
    }
    // And the replacement sentence is the one that shipped, so it cannot silently regress
    // to the key without this file noticing the wording moved.
    expect(src).toContain('hint="Run tools without asking for approval each time."')
  })
})
