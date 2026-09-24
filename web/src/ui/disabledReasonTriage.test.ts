import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The 106 unexplained disabled buttons, triaged ────────────────────────────────────────
//
// The ledger carried "triage the 108 unexplained `disabled` Buttons (busy-only vs genuinely
// blocked)" for eleven cycles. Done, and the split is the whole point:
//
//   143  <Button disabled={…}>  in the tree
//    37  already carry a disabledReason
//   106  do not  →   87  busy-only        ← twice called CORRECT, twice on an unchecked ground;
//                                            the class is CLOSED now, see the retraction below
//                     13  a state the user can fix   ← that change
//                      6  neither         ← named exclusions below
//
// 🔴🔴 SECOND RETRACTION, AND THE PARAGRAPH BELOW IS WHAT IS BEING RETRACTED. It read: *"`Button`'s
// own contract says a reason turns the native `disabled` into `aria-disabled` to keep the tab stop —
// deliberately NOT what you want for an in-flight action, which must not be re-clickable. So a rail
// that demanded a REASON everywhere would have broken 87 correct sites."*
//
// **That was false too, and this rail never checked it either.** `Button` suppresses the click when
// soft-off — `onClick={softOff ? (e) => e.preventDefault() : onClick}`, under its own comment *"Both
// paths must refuse the click"* — so a busy gate given a reason keeps its tab stop AND still cannot be
// fired. Nothing became re-clickable, and the 87 sites were not correct; they were silent.
//
// 🪤 THE SHAPE OF THE MISTAKE IS THE INTERESTING PART: cycle ux-796 retracted the FIRST justification
// (that the state was already announced), and put this second one in its place for the same 87 sites,
// with the same absence of an assertion. An exemption that survives the loss of its reason by acquiring
// a new one is not an exemption, it is a habit. Both are now asserted against the primitive rather than
// asserted in prose.
//
// 🔑 WHAT WAS ACTUALLY TRUE is a property of the TIER, not of busy gates. A raw `<button>` has no click
// guard, so `unavailableWhen`'s busy branch really must stay native — and it does, and that is asserted
// too. One rule stated across both tiers was wrong about one of them whichever way it was written.
//
// The busy class now carries `BUSY_REASON` (74 sites, one shared sentence, deliberately neutral about
// whose action is running because a shared flag usually cannot say).
//
// 🔴 CORRECTION (cycle ux-796) — THIS PARAGRAPH USED TO JUSTIFY the busy class on the grounds that
// the state was already announced to assistive tech, and used that as the exemption criterion for
// all 87. **That justification was false, and this rail never checked it.** `Button` publishes
// `aria-busy={loading || undefined}` — from the `loading` prop, never from `disabled` — so a
// `<Button disabled={busy}>` announces nothing whatsoever. ⚠️ THE NEXT CLAUSE OF THIS PARAGRAPH — that
// the exemption "still holds on its OWN terms (a busy gate does not owe a `disabledReason`; giving it
// one would make an in-flight action re-clickable)" — IS THE ONE RETRACTED ABOVE, and it is left in
// place rather than deleted so the sequence stays legible: one exemption, two justifications, neither
// checked. The missing announcement is a
// real, separate defect: `ui/busyIsNotAnnounced.test.ts` measures it and ratchets it down. Two
// rails about two properties must not borrow each other's conclusions again — that borrowing is
// what let ~180 sites read as certified.
//
// Verified on `#/settings/account`, the handle Save:
//
//   before   nativeDisabled: true   aria-disabled: null    title: null    focusable: false
//   after    nativeDisabled: false  aria-disabled: "true"  title: "No changes to save"  focusable: true
//
// 🪤 THE REASON CAN EXIST AND STILL BE UNREACHABLE. `KnowledgeListPage` had the right words —
// "Gather some matches first" — parked on a WRAPPING `<span title=…>`. A wrapper title is a
// sighted hover tooltip; the button inside stayed natively disabled, so the keyboard user it was
// written for could never land on it. Moved onto the button, where `Button` merges it with the
// action's own title. Any future wrapper-title-around-a-disabled-button is a rail failure.
//
// 🪤 AND THE FAMILY IS BIGGER THAN THE PRIMITIVE. A `<Button>`-scoped census cannot see a
// hand-rolled control: there are **41 raw `<button disabled={…}>`**, of which **21** have a
// non-busy gate — two of them the OTHER Save buttons on the very panel used to verify this
// change. They are left alone here because converting them is primitive-adoption work that moves
// pixels, but the count is recorded so the next pass starts from a number rather than a guess.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** In-flight vocabulary, from the census of what these gates actually reference. */
const BUSY = /\b(busy|saving|sending|loading|installing|retrying|pending|working|submitting|launching|testing|promoting|consolidating|regen\w*|bulkBusy|levelBusy|deleting|creating|running|uploading|importing|exporting|refreshing|syncing|starting|stopping)\b/i

/** Complete `<Button …>` tags by brace depth — a `[^>]*>` matcher stops at the `>` inside
 *  `onClick={() => f()}` and reports every tag as prop-less. */
function buttonTags(src: string): Array<{ tag: string; line: number }> {
  const out: Array<{ tag: string; line: number }> = []
  for (const m of src.matchAll(/<Button\b/g)) {
    let depth = 0
    for (let i = m.index! + m[0].length; i < src.length; i++) {
      const ch = src[i]
      if (ch === '{') depth++
      else if (ch === '}') depth--
      else if (ch === '>' && depth === 0) { out.push({ tag: src.slice(m.index!, i + 1), line: src.slice(0, m.index!).split('\n').length }); break }
    }
  }
  return out
}

/** Gates that are NOT a state a user can fix, so the native attribute is right. Each is here
 *  with its reason — a silent filter would let a real one hide behind the same shape. */
const EXEMPT: Record<string, string> = {
  'pages/loops/DesignCockpitPage.tsx': 'the gate is a pass-through `disabled` prop; the reason belongs to the caller',
  'pages/settings/DurabilityPanel.tsx': 'the gate is a pass-through `disabled` prop; the reason belongs to the caller',
  'pages/settings/ProjectionRulesPanel.tsx': 'the gate is a pass-through `disabled` prop; the reason belongs to the caller',
  'pages/schedule/ScheduleDetail.tsx': '`ranFlash` is a transient post-run flash — in-flight, not blocked',
  'pages/skills/SkillInspector.tsx': '`content === null` means still loading',
}

const offenders = walk(SRC).flatMap((f) => {
  const rel = f.slice(SRC.length + 1)
  const src = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  return buttonTags(src)
    .filter(({ tag }) => /\bdisabled=\{/.test(tag) && !/\bdisabledReason=/.test(tag))
    .filter(({ tag }) => {
      const gate = /\bdisabled=\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/.exec(tag)?.[1] ?? ''
      // Any clause that is not in-flight vocabulary is a state the user can act on.
      return gate.split(/\|\||&&/).map((s) => s.trim()).filter(Boolean).some((c) => !BUSY.test(c))
    })
    .filter(() => !(rel in EXEMPT))
    .map(({ line }) => `${rel}:${line}`)
})

describe('a disabled Button that a user could unblock says how', () => {
  it('finds the population (not vacuously green)', () => {
    const all = walk(SRC).flatMap((f) => buttonTags(readFileSync(f, 'utf8')).filter(({ tag }) => /\bdisabled=\{/.test(tag)))
    // 143 at the time of writing; the assertion is a floor, not a pin.
    expect(all.length, 'the matcher must find the disabled Buttons').toBeGreaterThanOrEqual(100)
    const withReason = walk(SRC).flatMap((f) => buttonTags(readFileSync(f, 'utf8')).filter(({ tag }) => /\bdisabledReason=/.test(tag)))
    expect(withReason.length, 'and the ones that explain themselves').toBeGreaterThanOrEqual(48)
  })

  it('has none left unexplained', () => {
    expect(offenders, 'a keyboard user tabs past this action and cannot learn what is missing').toEqual([])
  })

  it('🔴 THE PREMISE: a soft-off Button REFUSES the click, which is what makes a busy reason safe', () => {
    // 🔴🔴 THIS ASSERTION IS THE INVERSE OF THE ONE IT REPLACES, AND THAT IS THE POINT.
    //
    // The test here used to read `expect(busyTags.filter(has disabledReason), 'a busy gate must stay
    // native').toEqual([])` — it actively FORBADE a reason on a busy gate, on the grounds stated at the
    // top of this file: *"giving it one would make an in-flight action re-clickable."*
    //
    // **That ground is false, and this rail never checked it either.** It is the SECOND unchecked
    // justification for the same exemption: cycle ux-796 retracted the first one (that the state was
    // already announced) and put this one in its place, for the same 87 sites, with the same absence of
    // an assertion. `Button` suppresses the click in code:
    //
    //     onClick={softOff ? (e) => e.preventDefault() : onClick}     // ui/Button.tsx
    //
    // under its own comment, *"Both paths must refuse the click."* So a busy `<Button>` given a reason
    // goes `aria-disabled` + focusable AND STILL CANNOT BE FIRED. Nothing became re-clickable.
    //
    // 🔑 WHAT WAS TRUE was a property of the TIER, not of busy gates: a RAW `<button>` has no such
    // guard, which is why `unavailableWhen`'s busy branch returns native `disabled` and says so. Two
    // rails stated one rule across both tiers, so it was wrong about one of them either way.
    //
    // Pinned against the primitive so the claim cannot float again.
    const btn = readFileSync(join(SRC, 'ui/Button.tsx'), 'utf8')
    expect(btn, 'soft-off must swap the handler for a refusal, not merely drop the native attribute')
      .toMatch(/onClick=\{softOff \? \(e\) => e\.preventDefault\(\) : onClick\}/)
    expect(btn, 'and soft-off is what a reason turns on').toMatch(/const softOff = off && !!disabledReason && !loading/)
    // The other half of the tier split: the RAW helper must stay native, where the claim does hold.
    const un = readFileSync(join(SRC, 'ui/unavailable.ts'), 'utf8')
    expect(un, "a raw <button> has no click guard, so its busy branch keeps the native attribute")
      .toMatch(/if \(opts\?\.busy\) return \{ disabled: true, 'aria-busy': true, title: opts\.title \}/)
  })

  it('the in-flight class now EXPLAINS itself, and shares one sentence to do it', () => {
    // The 74 busy-gated sites this file used to exempt now carry `BUSY_REASON`. One constant, so they
    // cannot drift into 74 wordings — and deliberately neutral about WHOSE action is running, because a
    // shared flag usually cannot say which of its buttons is the working one.
    const inbox = readFileSync(join(SRC, 'pages/inbox/InboxDetail.tsx'), 'utf8')
    const busyTags = buttonTags(inbox).filter(({ tag }) => /disabled=\{!!busy\}/.test(tag))
    expect(busyTags.length, 'the inbox action rows are the canonical busy-only case').toBeGreaterThanOrEqual(4)
    // 🪤 `loading=` SITES ARE EXCLUDED, and the first draft of this assertion forgot to — it reported
    // two InboxDetail rows that are entirely correct. A button passing `loading=` publishes `aria-busy`,
    // which announces "working"; that is a BETTER answer than a reason saying "unavailable", so
    // demanding one there would have pushed a correct site backwards.
    expect(
      busyTags.filter(({ tag }) => !/disabledReason=/.test(tag) && !/\bloading=/.test(tag)),
      'a busy gate owes a reason now: soft-off keeps the tab stop AND refuses the click',
    ).toEqual([])
    const un = readFileSync(join(SRC, 'ui/unavailable.ts'), 'utf8')
    expect(un, 'the shared sentence lives in one place').toMatch(/export const BUSY_REASON = /)
    // 🪤 And it must NOT name whose action it is. "Another action…" is a lie on the working button;
    // "Saving…" is a lie on the four beside it.
    expect(un.match(/export const BUSY_REASON = '([^']+)'/)?.[1], 'neutral about the owner')
      .not.toMatch(/\b(another|other|sibling)\b/i)
  })

  it('🔴 THE RATCHET: no busy-gated Button may go back to explaining nothing', () => {
    // The class is closed, so this is an EXACT assertion rather than a ceiling: every `<Button>` whose
    // gate is in-flight vocabulary carries either `loading=` or a reason. A new one that does neither
    // names itself here.
    //
    // 🪤 ONE NAMED EXCLUSION, and finding it is why this was not run as a sweep. `ReadingView`'s gate is
    // `!pending`, where `pending` is a pending text SELECTION rather than an action in flight — the BUSY
    // vocabulary matches the WORD, not the meaning. Its existing reason ("Select a passage in the
    // article first") is already correct and complete, and giving it `BUSY_REASON` would have been a
    // fresh lie. The vocabulary finds candidates; it never delivers the verdict on one.
    const OVERLOADED_VOCABULARY: Record<string, string> = {
      'pages/knowledge/ReadingView.tsx':
        '`!pending` is a pending text SELECTION, not an in-flight action — matched by the word, not the meaning',
    }
    const silent = walk(SRC).flatMap((f) => {
      const rel = f.slice(SRC.length + 1)
      if (rel in OVERLOADED_VOCABULARY) return []
      // Comments blanked IN PLACE (length-preserving) — this file's own prose quotes the shapes it scans.
      const src = readFileSync(f, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
        .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
        .replace(/(^|[^:"'`])\/\/[^\n]*/g, (m, p1) => p1 + ' '.repeat(m.length - p1.length))
      return buttonTags(src)
        .filter(({ tag }) => /\bdisabled=\{/.test(tag) && !/\bdisabledReason=/.test(tag) && !/\bloading=/.test(tag))
        .filter(({ tag }) => {
          const gate = /(?<!aria-)\bdisabled=\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/.exec(tag)?.[1] ?? ''
          return !!gate && BUSY.test(gate)
        })
        .map(({ line }) => `${rel}:${line}`)
    })
    expect(silent, 'a busy-gated Button with neither `loading=` nor a reason explains nothing to anyone')
      .toEqual([])
    // Anti-vacuity, set well below the 74 that now carry the shared reason: this family SHRINKS as sites
    // adopt `loading=` instead, so a floor near the measurement would red on the next correct fix.
    const withReason = walk(SRC).flatMap((f) =>
      buttonTags(readFileSync(f, 'utf8')).filter(({ tag }) => /\bdisabledReason=\{BUSY_REASON\}/.test(tag)))
    expect(withReason.length, 'the shared reason must still be in use').toBeGreaterThanOrEqual(40)
  })

  it('never parks the reason on a wrapper the keyboard user cannot reach', () => {
    const parked = walk(SRC).flatMap((f) => {
      const src = readFileSync(f, 'utf8')
      return [...src.matchAll(/<(span|div)[^>]{0,200}?\btitle=[^>]{0,240}>\s*\n?\s*<Button\b[^>]{0,400}?disabled=/gs)]
        // A static title explaining the ACTION is fine; what must not live there is the blocked
        // reason. `ScheduleDetail`'s dry-run tooltip is that legitimate case.
        .filter((m) => !/Dry-run replay/.test(m[0]))
        .map(() => f.slice(SRC.length + 1))
    })
    expect(parked, 'a wrapper title is a hover tooltip; a natively disabled button inside it is unreachable').toEqual([])
  })
})
