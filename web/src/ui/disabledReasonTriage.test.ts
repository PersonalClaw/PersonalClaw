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
//   106  do not  →   87  busy-only        ← CORRECT, and must stay that way
//                     13  a state the user can fix   ← this change
//                      6  neither         ← named exclusions below
//
// **Busy-only is not a defect.** `Button`'s own contract says a reason turns the native
// `disabled` into `aria-disabled` to keep the tab stop — deliberately NOT what you want for an
// in-flight action, which must not be re-clickable, and whose state `aria-busy` already
// announces. So a rail that demanded a reason everywhere would have broken 87 correct sites; it
// asks only where the gate is a state the user can act on.
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

  it('leaves the in-flight majority on the native attribute', () => {
    // The counterpart assertion: `busy`-gated buttons must NOT sprout reasons, or an in-flight
    // action becomes re-clickable. Sampled at the sites the triage classified as busy-only.
    const inbox = readFileSync(join(SRC, 'pages/inbox/InboxDetail.tsx'), 'utf8')
    const busyTags = buttonTags(inbox).filter(({ tag }) => /disabled=\{!!busy\}/.test(tag))
    expect(busyTags.length, 'the inbox action rows are the canonical busy-only case').toBeGreaterThanOrEqual(4)
    expect(busyTags.filter(({ tag }) => /disabledReason=/.test(tag)), 'a busy gate must stay native').toEqual([])
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
