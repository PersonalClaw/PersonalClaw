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
// ⚠️ THE LAST ROW OF THAT CENSUS WAS WRONG, and `iconButtonBusyNotDisabled.test.tsx` is the
// correction: those gates did NOT "already read as busy". They read as UNAVAILABLE — 40% opacity,
// `cursor: not-allowed`, `aria-disabled="true"`, no `aria-busy` — because `disabled` was the only
// prop these two primitives had. Both now carry `loading`, and every in-flight gate moved onto it,
// which is why this file's populations shrank: 25 gated tags → 16, 12 mute → 3. The remaining
// three are genuine unavailability (a license gate, `disabled={false}`, an already-pinned widget).
// A reason is still not wanted on any of them, so this rail's question is unchanged; only its
// population is smaller and now consists solely of real gates.
//
// 🔑 THE CANONICAL FORM WAS ALREADY IN THE REPO. `ui/Composer`'s `send-disabled` case is the same
// state (`canSend` false because the draft is empty) and it names the fix. Converging onto what ships
// rather than inventing copy is the whole point; the two cockpit composers are hand-rolled textareas
// that never inherited it.
//
// 🪤 A COMPOUND GATE GETS A CONDITIONAL REASON. `disabled={!text.trim() || busy}` mixed a state the
// user can fix with one they cannot, so the reason was passed ONLY for the `!text.trim()` branch —
// announcing "Type a steer first" while a send is in flight would be a lie. The compound gate is
// now SPLIT at the source (`disabled={!text.trim()} loading={busy}`), which is the stronger form of
// the same ruling: the two branches are two props, so the reason cannot fire on the wrong one. The
// conditional is kept anyway — `disabledReason` is only read while `disabled`, and the ternary is
// what documents which branch it belongs to.

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
    // Both steer sends: the gate is now the fixable branch ALONE (`loading={busy}` carries the
    // other half), so the pin follows the split rather than the pre-split compound expression.
    ['pages/code/CodeCockpitPage.tsx', /disabled=\{!text\.trim\(\)\} disabledReason=[\s\S]*?loading=\{busy\}/],
    ['pages/code/CodeCockpitPage.tsx', /disabled=\{!text\.trim\(\)\} disabledReason=[\s\S]*?loading=\{sending\}/],
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

  it('a self-evident gate is still left mute — and every one left is a REAL gate', () => {
    // Not vacuous, and a guard against a future sweep "finishing the job": a move-up on the first
    // row explains itself by position, and a license-gated download by the badge beside it.
    const mute = walk(SRC).flatMap((abs) => gatedTags(readFileSync(abs, 'utf8')))
      .filter((t) => !/disabledReason=/.test(t))
    // Measured 3 (was 11, when 8 of them were in-flight states miscast as unavailability — see the
    // ⚠️ note at the top): a license gate, `disabled={false}`, and an already-pinned widget.
    expect(mute.length, 'the self-evident gates keep their silence deliberately')
      .toBeGreaterThanOrEqual(3)
    // The half of the old claim that was false: none of the surviving silences may be in-flight
    // vocabulary. That is `loading`'s job now, and a regression here means a busy button went back
    // to announcing itself unavailable.
    // No word boundaries on purpose — `savePending`/`isBusy` are the shapes these gates take, and a
    // `\b`-anchored matcher reads camelCase as clean. (Checked against the three survivors: none of
    // `gatedUndownloaded`, `false`, `pinned` matches.)
    const inFlight = mute.filter((t) => /(?<!aria-)disabled=\{[^}]*(?:busy|saving|sending|testing|rechecking|reconnecting|deleting|pending|loading)/i.test(t))
    expect(inFlight, 'an in-flight gate belongs on `loading`, not `disabled`').toEqual([])
  })

  it('both icon primitives keep the tab stop, which is what makes a reason audible at all', () => {
    for (const rel of ['ui/IconButton.tsx', 'ui/SquareIconButton.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel} must map disabled to aria-disabled`).toMatch(/aria-disabled=\{disabled \|\| undefined\}/)
      expect(src, `${rel} must not emit the native attribute`).not.toMatch(/<motion\.button[\s\S]{0,300}?\sdisabled=\{/)
    }
  })
})
