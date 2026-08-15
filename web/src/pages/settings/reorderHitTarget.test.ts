import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The reorder buttons need a real target, and axe could not see that they lacked one ───────────
//
// `#/settings/routing?uc=reasoning` renders one reorder button pair per candidate model. They were
// `rounded-md p-1` around a 13px icon, which measures **21×21 CSS px** — under SC 2.5.8's 24px floor
// and under the size every other icon-button in the app uses.
//
// The mechanical pass never flagged it, and the reason matters: with a single bound candidate BOTH
// buttons are at an end of the list, so `unavailableWhen` marks them `aria-disabled` — and axe skips
// disabled controls. The defect was only reachable by measuring geometry directly:
//
//     desktop 1440×900   21×21px  (aria-disabled=true)
//     phone   390×844    21×21px  (aria-disabled=true)
//
// `size-7` is 28px: the exact geometry `SquareIconButton` uses (`grid size-7 place-items-center
// rounded-md`), which is the app's 70-use icon-button primitive.
//
// ⚠️ WHY THIS DOES NOT ADOPT THAT PRIMITIVE. `SquareIconButton` maps `disabled` to `aria-disabled` and
// NEVER to the native attribute, so it keeps its tab stop. `unavailableWhen` deliberately goes
// *natively* disabled while `busy` ("a running action goes natively disabled so it cannot be fired
// twice") and only uses `aria-disabled` for the end-of-list case. Those are different, documented
// behaviours, and the primitive cannot express the busy half — so adopting it would flatten a real
// distinction to satisfy a consistency scan. Geometry is borrowed; semantics are kept.

const SRC = join(process.cwd(), 'src')
const routing = () => readFileSync(join(SRC, 'pages/settings/RoutingPanel.tsx'), 'utf8')

/** The whole `<button …>…</button>` element for each Move action.
 *
 *  Deliberately sliced to `</button>` rather than matched with `/<button[\s\S]*?>/`: an arrow
 *  function in a prop (`onClick={() => move(i, -1)}`) contains a `>`, so a non-greedy scan to the
 *  first `>` truncates the tag before its className and silently checks nothing. */
function moveButtons(src: string): string[] {
  return src
    .split('<button type="button"')
    .slice(1)
    .map((chunk) => chunk.slice(0, chunk.indexOf('</button>')))
    .filter((el) => /aria-label=\{`Move /.test(el))
}

describe('reorder buttons carry a 24px+ target', () => {
  it('finds both reorder buttons (not vacuously green)', () => {
    expect(moveButtons(routing()).length, 'both the earlier and later button must be matched').toBe(2)
  })

  it('each uses the 28px square geometry, not p-1', () => {
    for (const tag of moveButtons(routing())) {
      expect(tag, 'must be a 28px grid-centred square').toMatch(/grid size-7 place-items-center/)
      expect(/\bp-1\b/.test(tag), '21px padding-only geometry must not come back').toBe(false)
    }
  })

  it('the size matches the icon-button primitive it borrows from', () => {
    // If SquareIconButton ever changes size, this rail should be re-derived rather than left
    // asserting a number that no longer matches the family.
    const sib = readFileSync(join(SRC, 'ui/SquareIconButton.tsx'), 'utf8')
    expect(sib).toMatch(/grid size-7 place-items-center rounded-md/)
  })

  it('the busy semantics are preserved — still unavailableWhen, not the primitive', () => {
    // The reason this file keeps its own button. `unavailableWhen` goes natively disabled while busy;
    // SquareIconButton never does. Losing that would let an in-flight save be fired twice.
    const src = routing()
    expect(src).toMatch(/unavailableWhen\(i === 0, 'Already tried first', \{ busy \}\)/)
    expect(src).toMatch(/unavailableWhen\(i === shown\.length - 1, 'Already tried last', \{ busy \}\)/)
    const helper = readFileSync(join(SRC, 'ui/unavailable.ts'), 'utf8')
    expect(helper, 'busy must still mean NATIVE disabled').toMatch(/if \(opts\?\.busy\) return \{ disabled: true/)
  })

  it('each button still names itself and hides its icon', () => {
    for (const tag of moveButtons(routing())) expect(tag).toMatch(/aria-label=\{`Move \$\{ref\} (earlier|later)`\}/)
    expect(routing()).toMatch(/<ArrowUp size=\{13\} aria-hidden \/>/)
    expect(routing()).toMatch(/<ArrowDown size=\{13\} aria-hidden \/>/)
  })
})
