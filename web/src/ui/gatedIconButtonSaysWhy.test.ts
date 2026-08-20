import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The canonical send button says why; the two hand-rolled ones said nothing ───────────────
//
// `IconButton` and `SquareIconButton` already carry `disabledReason` AND already keep their tab stop
// (both map `disabled` → `aria-disabled`, never the native attribute), so this is purely an ADOPTION
// gap: a keyboard user lands on the gated button and hears only its label.
//
// Census of gated icon buttons, by gate:
//
//   `ui/Composer.tsx`                  send + optimize   **has a reason** ("Type a bit more first")
//   `code/CodeCockpitPage.tsx` ×2      steer send        MUTE  ← same job, same primitive, no reason
//   `ui/FindBar.tsx` ×2                prev / next match MUTE  ← the 0/0 counter beside them speaks,
//   (censused at `chat/FindBar.tsx`;                              the buttons did not
//    promoted to `ui/` in KL-16)
//   ModelsPanel ×3 · PlanReview ×4     first/last, saving  self-evident or transient → left native
//   6 × `testing`/`busy`/`rechecking`  in flight           already reads as busy → left alone
//
// 🔑 THE CANONICAL FORM WAS ALREADY IN THE REPO. `ui/Composer`'s `send-disabled` case is the same
// state (`canSend` false because the draft is empty) and it names the fix. Converging onto what ships
// rather than inventing copy is the whole point; the two cockpit composers are hand-rolled textareas
// that never inherited it.
//
// 🪤 A COMPOUND GATE GETS A CONDITIONAL REASON. `disabled={!text.trim() || busy}` mixes a state the
// user can fix with one they cannot. The reason is passed ONLY for the `!text.trim()` branch —
// announcing "Type a steer first" while a send is in flight would be a lie, and the busy branch
// already reads as busy (spinner + label). The primitive's own doc asks for exactly this.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** Every `<IconButton …/>` / `<SquareIconButton …/>` tag that is gated. Matched non-greedily to `/>`:
 *  🪤 a `[^>]` matcher stops at the `>` inside `onChange={(v) => …}` and silently misses tags — that
 *  is how cycle 118's first census reported 2 sites where there were 4. */
function gatedTags(src: string): string[] {
  return [...src.matchAll(/<(?:Square)?IconButton\b[\s\S]{0,500}?\/>/g)]
    .map((m) => m[0])
    .filter((t) => /(?<!aria-)disabled=/.test(t))
}

describe('a gated icon button whose gate the user can fix says so', () => {
  const ADOPTERS: [string, RegExp][] = [
    ['pages/code/CodeCockpitPage.tsx', /disabled=\{!text\.trim\(\) \|\| busy\}/],
    ['pages/code/CodeCockpitPage.tsx', /disabled=\{!text\.trim\(\) \|\| sending\}/],
    ['ui/FindBar.tsx', /label="Previous match"/],
    ['ui/FindBar.tsx', /label="Next match"/],
  ]

  for (const [rel, gate] of ADOPTERS) {
    it(`${rel} ${gate.source.slice(0, 34)}… names what to do`, () => {
      const tag = gatedTags(readFileSync(join(SRC, rel), 'utf8')).find((t) => gate.test(t))
      expect(tag, `the gated button matching ${gate} must still exist`).toBeTruthy()
      expect(tag!, 'a fixable gate must say what fixes it').toMatch(/disabledReason=/)
    })
  }

  it('the cockpit reason is conditional, so it never fires mid-send', () => {
    const src = readFileSync(join(SRC, 'pages/code/CodeCockpitPage.tsx'), 'utf8')
    const conditional = [...src.matchAll(/disabledReason=\{!text\.trim\(\) \? 'Type a steer first' : undefined\}/g)]
    expect(conditional.length, 'both steer composers gate the reason on the fixable branch only').toBe(2)
  })

  it('it converges on the canonical composer, which had it first', () => {
    // If this ever stops being true the copy has diverged and this rail is measuring a fossil.
    const composer = readFileSync(join(SRC, 'ui/Composer.tsx'), 'utf8')
    expect(composer, "the canonical send button's reason is the model for the others").toMatch(
      /label="Send message" disabledReason="Type a bit more first"/,
    )
  })

  it('an in-flight or self-evident gate is still left native — and there are plenty', () => {
    // Not vacuous, and a guard against a future sweep "finishing the job": a spinner already reads as
    // busy, and a move-up on the first row explains itself by position.
    const mute = walk(SRC).flatMap((abs) => gatedTags(readFileSync(abs, 'utf8')))
      .filter((t) => !/disabledReason=/.test(t))
    // Measured: 11 of the 20 gated tags stay mute on purpose (6 in-flight, 4 first/last, 1 saving).
    expect(mute.length, 'the transient/self-evident gates keep their silence deliberately')
      .toBeGreaterThanOrEqual(11)
  })

  it('both icon primitives keep the tab stop, which is what makes a reason audible at all', () => {
    for (const rel of ['ui/IconButton.tsx', 'ui/SquareIconButton.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel} must map disabled to aria-disabled`).toMatch(/aria-disabled=\{disabled \|\| undefined\}/)
      expect(src, `${rel} must not emit the native attribute`).not.toMatch(/<motion\.button[\s\S]{0,300}?\sdisabled=\{/)
    }
  })
})
