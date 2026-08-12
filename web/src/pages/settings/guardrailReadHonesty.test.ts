import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A failed read may not fabricate a guardrail state ────────────────────────────
//
// `GuardrailsPanel` had two reads whose `.catch` substituted a value instead of recording a failure:
//
//   api.incident().then(setState).catch(() => setState({ active: false, reason: '', started_at: '' }))
//   api.modelsHealth().then(r => setRows(r.providers)).catch(() => setRows([]))
//
// The first is the one that matters: it FABRICATES "no incident is active" — a claim about the control
// that suspends all unattended work. Measured with both endpoints at 500:
//
//   before   hint "Off — automation runs normally."      toggle ENABLED   alerts []
//   after    hint "Couldn't check whether incident mode is active: …"   toggle DISABLED
//            alert "Couldn't check provider health: …"
//
// A toggle that is enabled while its true state is unknown offers an action in a direction nobody has
// verified. Leaving `state` at `null` keeps the existing `disabled={busy || !state}` honest, and the hint
// says what happened. The second read is the same shape with softer copy: "No background model calls
// recorded yet" is a reassuring sentence, and a failed request produced it.
//
// Verified NOT broken by the fix: with incident mode faked ACTIVE, the row still carries
// `bg-error/10 ring-1 ring-error/40` and its computed background is `oklab(… / 0.1)` — the alarming
// treatment is intact (and `--color-error` is a real token aliasing `--color-danger`, so the utility is
// not inert; that was checked before assuming).

const SRC = join(process.cwd(), 'src', 'pages', 'settings', 'GuardrailsPanel.tsx')
const raw = readFileSync(SRC, 'utf8')
// Comments are stripped before matching. The comments in this file QUOTE the old, wrong code so the
// next reader sees what was fixed — and a naive matcher then flagged the comment as the defect. (This
// repo has been bitten by the same shape before: the primitive-adoption ratchet once counted a
// `<button>` written in prose.) Assertions that must see the comments read `raw` explicitly.
const src = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the guardrails reads state their failure instead of inventing one', () => {
  it('reads the real file (not vacuously green)', () => {
    expect(src).toMatch(/function IncidentSection\(/)
    expect(src).toMatch(/function ProviderHealthSection\(/)
    expect(raw.length).toBeGreaterThan(4000)
  })

  it('the incident read never substitutes { active: false }', () => {
    expect(/catch\(\(\)\s*=>\s*setState\(\{\s*active:\s*false/.test(src),
      'a failed read must not claim the kill switch is off').toBe(false)
    expect(src, 'the rejection must be captured').toMatch(/api\.incident\(\)[\s\S]{0,160}\.catch\(setLoadErr\)/)
  })

  it('the incident hint reports the failure, and the toggle stays disabled', () => {
    expect(src).toMatch(/Couldn't check whether incident mode is active/)
    // `!state` is what disables it — the state must therefore stay null on failure.
    expect(src).toMatch(/disabled=\{busy \|\| !state\}/)
  })

  it('provider health does not substitute an empty audit', () => {
    expect(/catch\(\(\)\s*=>\s*setRows\(\[\]\)\)/.test(src),
      '"no calls recorded yet" is a claim, not an error').toBe(false)
    expect(src).toMatch(/Couldn't check provider health/)
    expect(src, 'and it announces, because it replaces content the user was reading').toMatch(/role="alert"/)
  })
})
