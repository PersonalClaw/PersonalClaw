import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A failed read is not an empty fleet ───────────────────────────────────────────────────────
//
// `SystemWidget`'s background-agent monitor is shell-level — one click from every surface — and it
// loaded the fleet with `.catch(() => setAgents([]))`. The guard directly below hides the section when
// the list is empty, so **a failed `/api/spawn` and a genuinely empty fleet were the same value**, and
// the section simply vanished.
//
// 🔑 MEASURED IN A BROWSER, same gateway and same seeded home, only the bundle swapped, with
// `/api/spawn` intercepted:
//
//                          on `main`                                    after
//   healthy poll           "2 running · 0 done" + both rows             same
//   the poll then fails    **ABSENT — the section vanishes**            "2 running · 0 done · not
//                                                                       updating" + both rows kept
//   the poll recovers      section returns                              section unchanged
//   fails on first read    **ABSENT**                                   "Couldn't read the background
//                                                                       agents — retrying…"
//   genuinely empty        ABSENT                                       ABSENT (deliberately unchanged)
//
// So it was worse than "hidden on failure": with two agents running the section **flickered** — shown,
// gone, shown — which reads as the fleet emptying itself.
//
// This is the rule the same file already states 115 lines above, for the system read: a failed
// `api.system()` sets `disconnected` and the card still renders, because it should *"still give the
// click a useful result"*. The fleet read was the one that gave none.
//
// 🪤 WHY THE EMPTY CASE IS STILL HIDDEN, and that is not the same bug. There is nothing to monitor in
// an empty fleet, so hiding is the right product call — it is only wrong when it is inferred from a
// failure. The control assertion below pins that, because "just always render the section" is the
// tempting over-correction and it would put a permanently empty slab in every surface's shell.
//
// 🪤 AND THERE IS A SECOND CONSUMER OF THIS ENDPOINT, which is how the browser probe was designed
// wrong the first time. `lib/useAgentActivity.ts` also polls `/api/spawn`, so two reads land before the
// widget is even opened, and a request-counting stub fed its good payload to the wrong caller. That
// consumer already does the right thing (`setError(e)` and it leaves `sources` alone, so it keeps the
// last known value) — which is the same shape as the fix here, arrived at independently.

const SRC = join(process.cwd(), 'src')
const widget = readFileSync(join(SRC, 'ui/SystemWidget.tsx'), 'utf8')

/** `RunningAgents` only — bounded to the construct so a match elsewhere in the file cannot satisfy it.
 *
 *  🪤 COMMENTS ARE STRIPPED, and that is load-bearing rather than tidiness. The component now carries a
 *  comment quoting the defect it fixed — `.catch(() => setAgents([]))` — so the negative assertions
 *  below matched their own documentation and this rail failed on a correct tree. A scanner that cannot
 *  tell a statement from a sentence about a statement is measuring the wrong thing. Line/template
 *  contents are preserved, so the JSX copy assertion still reads the real string. */
function runningAgents(): string {
  const at = widget.indexOf('function RunningAgents(')
  expect(at, 'RunningAgents must still exist').toBeGreaterThan(-1)
  const end = widget.indexOf('\nfunction ', at + 1)
  expect(end, 'the component must terminate before the next top-level function').toBeGreaterThan(at)
  return widget
    .slice(at, end)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
}

describe('the background-agent fleet read', () => {
  it('reads its subject — a rail over nothing asserts nothing', () => {
    expect(widget.length, 'SystemWidget.tsx did not read').toBeGreaterThan(5_000)
    const fn = runningAgents()
    expect(fn, 'it must still load the fleet').toMatch(/api\.spawnedAgents\(\)/)
    expect(fn, 'and still poll while open').toMatch(/setInterval\(load, 4000\)/)
  })

  it('🔴 a failed read does not clobber the list it already had', () => {
    const fn = runningAgents()
    // The exact regression: `.catch(() => setAgents([]))`. Any catch that WRITES the list turns a
    // transient failure into "the fleet is empty", which the guard below then renders as nothing.
    expect(fn, 'a failed read must not overwrite a known-good fleet with []')
      .not.toMatch(/catch\s*\(\s*\)?\s*=>\s*setAgents\(/)
    expect(fn, 'and must not clear it to an empty array anywhere in the failure path')
      .not.toMatch(/catch[\s\S]{0,60}?setAgents\(\s*\[\s*\]\s*\)/)
  })

  it('the failure is its own state, distinguishable from an empty fleet', () => {
    const fn = runningAgents()
    expect(fn, 'a failure flag separate from the list').toMatch(/setFailed\(true\)/)
    // …and it must CLEAR on a good read, or the marker sticks forever after one blip. The browser
    // probe's third state ("recovered") is what this asserts statically.
    expect(fn, 'a good read must clear the failure').toMatch(/setFailed\(false\)/)
  })

  it('a first read that fails renders a notice instead of nothing', () => {
    const fn = runningAgents()
    expect(fn, 'the never-loaded failure case must be handled before the empty-fleet guard')
      .toMatch(/if\s*\(\s*failed\s*&&\s*!agents\s*\)/)
    expect(fn, 'and it must say so in words a user can read')
      .toMatch(/Couldn’t read the background agents/)
    // The order matters: if the empty-fleet `return null` came first it would swallow this branch.
    expect(
      fn.indexOf('failed && !agents') < fn.indexOf('agents.length === 0'),
      'the failure branch must precede the empty-fleet guard, or it is unreachable',
    ).toBe(true)
  })

  it('a stale list does not claim to be live', () => {
    // Showing the last known fleet is right; showing it as though it were current is not.
    expect(runningAgents(), 'the header marks a non-updating list').toMatch(/not updating/)
  })

  it('✅ CONTROL — a genuinely empty fleet is still hidden', () => {
    // The over-correction guard. Rendering the section unconditionally would park an empty slab in
    // every surface's shell, which is a different defect, not a fix.
    expect(runningAgents(), 'an empty fleet still renders nothing')
      .toMatch(/if\s*\(!agents\s*\|\|\s*agents\.length === 0\)\s*return null/)
  })

  it('the sibling consumer still keeps its last known value too', () => {
    // Named because it is the corroboration for the shape chosen here, and because a later pass
    // "simplifying" it to a clobber would reintroduce the same defect one layer up.
    const hook = readFileSync(join(SRC, 'lib/useAgentActivity.ts'), 'utf8')
    expect(hook, 'useAgentActivity also reads /api/spawn').toMatch(/api\.spawnedAgents\(\)/)
    expect(hook, 'and records the error rather than emptying its sources').toMatch(/catch\([\s\S]{0,80}?setError\(/)
    expect(hook, 'it must not blank its sources on failure').not.toMatch(/catch[\s\S]{0,80}?setSources\(\s*(null|\{)/)
  })
})
