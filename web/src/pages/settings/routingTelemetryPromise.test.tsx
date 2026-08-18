import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── An empty state must not promise data that cannot arrive ─────────────────────────────────────
//
// `RoutingPanel` offers three axes — Chat (the DEFAULT), Code & tools, Reasoning — and told all three
// "it fills in as models handle this kind of request". Traced through the backend, only one of them can
// ever fill:
//
//   routing stats are folded in `ModelCallGuard._audit` → the guard is applied by `provider_bridge`
//   only when `_guard_use_case` is set → which happens for exactly
//   ("reasoning", "background", "loops", "orchestration").
//
// The bridge says why in its own comment: "The interactive chat/code_tools stream stays OUT OF SCOPE …
// both human-watched". So a fresh install lands on Chat and waits forever. This is the inert-promise
// shape — the mechanism is fine and deliberate; the COPY was the defect.
//
// The tabs are deliberately left alone (mirroring the Models panel's axes is a stated choice, and
// removing two of them is a product decision) — filed as an owner taste call instead.

const SRC = join(process.cwd(), 'src')
const PY = join(__dirname, '../../../../src/personalclaw')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const ui = strip(readFileSync(join(SRC, 'pages/settings/RoutingPanel.tsx'), 'utf8'))
const bridge = readFileSync(join(PY, 'providers/provider_bridge.py'), 'utf8')

describe('the routing empty state tells the truth per axis', () => {
  it('the measured list matches the axes the backend actually guards', () => {
    // 🔑 THE PAIRING THAT MATTERS. If the guard ever widens to chat, this fails — and at that moment the
    // copy should go back to promising it will fill in, which is exactly the review this test forces.
    expect(ui).toMatch(
      /const MEASURED_USE_CASES = \['reasoning', 'background', 'loops', 'orchestration'\] as const/,
    )
    expect(bridge, "the bridge's own gate, verbatim").toMatch(
      /if use_case in \("reasoning", "background", "loops", "orchestration"\):\s*\n\s*kwargs\["_guard_use_case"\] = use_case/,
    )
  })

  it('an unmeasured axis says so instead of promising', () => {
    expect(ui, 'the honest branch exists').toContain('Nothing is measured for this axis.')
    expect(ui, 'and it is chosen by membership, not hardcoded per tab').toMatch(
      /\(MEASURED_USE_CASES as readonly string\[\]\)\.includes\(useCase\)/,
    )
    // The promise survives for the axis where it is TRUE.
    expect(ui).toContain('it fills in as models handle this kind of request')
  })

  it('the default tab is one of the unmeasured ones — which is why this mattered', () => {
    // Not decoration: it is the reason the defect reached every fresh install rather than a corner.
    expect(ui).toMatch(/const USE_CASES = \[\s*\{ key: 'chat'/)
    expect(ui).toMatch(/const DEFAULT_USE_CASE = USE_CASES\[0\]\.key/)
    expect(['reasoning', 'background', 'loops', 'orchestration']).not.toContain('chat')
  })

  it('the fold really is inside the guard — the other end of the chain', () => {
    const guard = readFileSync(join(PY, 'guardrails/model_call.py'), 'utf8')
    const audit = guard.slice(guard.indexOf('    def _audit('))
    expect(audit.slice(0, 2600), 'the stats fold hangs off the audit').toMatch(
      /record_routing_stats\(_asdict_row\(rec\), home=config_dir\(\)/,
    )
    // 🪤 NO CHARACTER WINDOW. My first version took 2000 chars from `if guard_use_case:` and the wrap
    // call sits ~44 lines further down, so it failed on correct code — the sixth time in two cycles that
    // a fixed budget stood in for a scope. Ordering is the actual claim: the wrap happens INSIDE the
    // gate, so its index must be greater and no earlier wrap may exist.
    const gate = bridge.indexOf('if guard_use_case:')
    const wrap = bridge.indexOf('wrap_model_call_guard(', gate)
    expect(gate, 'the gate must be found').toBeGreaterThan(0)
    expect(wrap, 'the wrap happens inside the gate').toBeGreaterThan(gate)
    expect(bridge.slice(0, gate), 'and nowhere before it').not.toMatch(/wrap_model_call_guard\([^)]/)
  })
})
