import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The panel must not deny the capability it ships ──────────────────────────────────────────────
//
// `#/settings/routing` opened with: "Observation only: this does not change routing (that's a later
// capability)". MRT-4 then added `RoutingPolicySection` directly below that sentence — mode, pin and
// per-class order, each written through `api.setRoutingPolicy`. So the page told a user its own
// controls did nothing, which is worse than saying nothing: a reader who believes the header will not
// touch the controls, and a reader who tries them has been misinformed by the product.
//
// Seen on a live gateway at `#/settings/routing?uc=reasoning`: the header hint claiming "Observation
// only" rendered ~350px above a Mode select, a Pin select and a reorder list that all persist.
//
// The file's own doc comment carried the same stale claim ("This surface ONLY visualizes — it never
// changes routing"), so both were corrected together; leaving one would have the file contradict
// itself for the next reader.
//
// The last assertion is the vacuity guard, and it is the important one: this rail is only *right*
// while the policy section actually writes. If `RoutingPolicySection` is ever removed, "observation
// only" becomes true again and this rail must be re-derived rather than left forbidding honest copy.

const SRC = join(process.cwd(), 'src')
const routing = () => readFileSync(join(SRC, 'pages/settings/RoutingPanel.tsx'), 'utf8')

/** Just the user-facing `hint=` string on the PanelHeader. */
function headerHint(src: string): string {
  return src.match(/<PanelHeader[\s\S]*?hint="([^"]*)"/)?.[1] ?? ''
}

describe('the routing panel describes what it actually does', () => {
  it('reads the real header hint (not vacuously green)', () => {
    const hint = headerHint(routing())
    expect(hint.length, 'the PanelHeader hint must be found').toBeGreaterThan(80)
    expect(hint).toMatch(/per-model efficiency/)
  })

  it('the hint no longer claims the surface cannot change routing', () => {
    const hint = headerHint(routing())
    expect(/Observation only/i.test(hint), 'the page ships routing controls — this claim is false').toBe(false)
    expect(/does not change routing/i.test(hint)).toBe(false)
    expect(/a later capability/i.test(hint), 'the capability already shipped (MRT-4)').toBe(false)
  })

  it('the hint points at the policy section that does the deciding', () => {
    expect(headerHint(routing()), 'a reader should be told where the decision is made').toMatch(/Routing policy/)
  })

  it('the file-level doc comment does not contradict it either', () => {
    const doc = routing().slice(0, routing().indexOf('export function RoutingPanel'))
    expect(/ONLY visualizes/.test(doc), 'the doc comment carried the same stale claim').toBe(false)
    expect(/never changes routing/.test(doc)).toBe(false)
    expect(doc, 'and it should name both halves').toMatch(/DECIDES/)
  })

  it('the policy controls really do write — the reason the old copy was false', () => {
    // VACUITY GUARD. Remove the policy section and "observation only" becomes accurate again; this
    // rail would then be forbidding correct copy. Assert the write path exists, so the rail fails
    // loudly instead of quietly outliving its premise.
    const src = routing()
    // `\(` matters: without it, renaming the function to `RoutingPolicySectionDISABLED` still
    // substring-matches and the guard passes on a page that no longer has policy controls at all.
    expect(src, 'the section must be declared').toMatch(/function RoutingPolicySection\(/)
    // Declared is not enough — it must be RENDERED, or the copy would describe a dead component.
    expect(src, 'and actually rendered by the panel').toMatch(/<RoutingPolicySection\s/)
    expect(src, 'and it must persist through the API').toMatch(/api\.setRoutingPolicy/)
    for (const lever of ['mode', 'pin', 'order']) {
      expect(src, `the ${lever} lever must still be written`).toMatch(new RegExp(`save\\(\\{[^}]*${lever}`))
    }
  })
})
