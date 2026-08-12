import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A failed health probe must be announced, and must not leave an action armed ────
//
// `#/settings/doctor` had two `null`-substituting reads, and each failed differently. Measured with
// `/api/doctor` and `/api/doctor/remediation` at 500:
//
//                        before                                  after
//   the report           "Couldn't load the doctor report."      same text, now role="alert"
//                        rendered, but [role="alert"] count 0
//   the health score     **"Loading…" forever**                  "Couldn't load the health score: …"
//   the Run now button   **ENABLED**                             disabled, "The health score could not be read"
//
// Two shapes in one panel. The report was the cycle-92 half — a message that renders but is not announced,
// on a HEALTH surface where "we could not probe" changes what the whole screen means. The remediation read
// was the cycle-96 half (fabricated PENDENCY: a dead end indistinguishable from a slow network) *plus*
// cycle 91's: an action offered against state nobody could read. Pressing Run now performs a real
// maintenance pass whose result would have been unverifiable.
//
// `Run now` is disabled ONLY on a read failure, not during the initial load — the same distinction the
// incident kill switch draws.
//
// Also checked and left alone: the report's own failure copy already existed. `DoctorPanel` was the one
// sibling of the `null`-substituting panels that had a real message, which is why this cycle is about
// announcement and armed actions rather than adding a message.

const SRC = join(process.cwd(), 'src', 'pages', 'settings', 'DoctorPanel.tsx')
const raw = readFileSync(SRC, 'utf8')
const src = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the doctor panel reports its read failures', () => {
  it('reads the real file (not vacuously green)', () => {
    expect(raw).toMatch(/function RemediationSection\(/)
    expect(raw.length).toBeGreaterThan(6000)
  })

  it('the report failure is announced', () => {
    expect(src).toMatch(/<div role="alert"[^>]*>Couldn't load the doctor report\./)
  })

  it('the remediation read captures its rejection instead of substituting null', () => {
    expect(/doctorRemediation\(\)[\s\S]{0,80}\.catch\(\(\) => setSnap\(null\)\)/.test(src),
      'setSnap(null) rendered "Loading…" forever').toBe(false)
    expect(src, 'the rejection must land in state').toMatch(/\.catch\(setLoadErr\)/)
  })

  it('the health score says it failed instead of pretending to load', () => {
    expect(src).toMatch(/Couldn't load the health score/)
    expect(src, 'and announces, because it replaces content the user was reading').toMatch(
      /<span role="alert">Couldn't load the health score/,
    )
  })

  it('Run now is disarmed on a read failure, and says why', () => {
    expect(src).toMatch(/disabled=\{Boolean\(loadErr\)\}/)
    expect(src).toMatch(/disabledReason=\{loadErr \? 'The health score could not be read'/)
  })

  it('and is NOT disarmed merely by the initial load', () => {
    // The guard is on the error, not on `!snap` — otherwise the button would be dead on every mount.
    expect(/disabled=\{[^}]*!snap[^}]*\}/.test(src), 'a loading snapshot must not disable the action').toBe(false)
  })
})
