import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Six delete buttons that announced nothing at all ───────────────────────────
//
// The plan-review surface is NOT reachable by URL from a fresh fixture: `LoopsSection` renders it
// from an in-memory draft, or from a RESUMED loop whose status is `review`. No seeded loop had that
// status, so this surface had never been driven. Setting one goal loop to `review` in the validation
// home's `loop/loops.db` (the authority — the per-loop `status.json` is secondary and changing it
// alone did nothing) put it on screen. Both defects were then measured on the live DOM:
//
//     BEFORE   6 buttons with NO accessible name — no text, no aria-label, an `X` glyph only.
//              One per sub-goal, and the action is DELETE.
//              1 unnamed <input> ("Add a sub-goal…").
//     AFTER    0 unnamed buttons. Each reads `Remove sub-goal: <that sub-goal>`, all six distinct.
//              The input reads "New sub-goal".
//
// An icon-only button is the worst case of this family: a raw <input> at least has a placeholder a
// sighted user can read, while an `X` with no name is announced as just "button". Six identical
// "button"s next to six sub-goals, one of which deletes the one you are on.
//
// The pattern was already in the file — the sibling add control is
// `<IconButton icon={Plus} label="Add sub-goal" …>`, three lines below. The remove row never got the
// same treatment, which is the usual shape: a per-ITEM control missed while the per-SURFACE one was
// handled.
//
// Named from the sub-goal text, truncated at 60 chars because a sub-goal is a full sentence — the
// name has to identify the row without reading a paragraph.
//
// Also checked and NOT changed (three false positives from a source scan that stripped `{…}`
// expressions and so could not see rendered text): the title button renders `{title}` plus a `title`
// attribute; the skill-install button renders "Install"/"Installed"; the option chips render
// `{o.name}`. All three are named. `CodePlanReview` has NO icon-only unnamed buttons at all.

const SRC = join(process.cwd(), 'src')
const code = (rel: string) =>
  readFileSync(join(SRC, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('every sub-goal row names its own delete button', () => {
  const src = code('pages/loops/LoopPlanReview.tsx')

  it('the remove button is named from the sub-goal it removes', () => {
    // A CONSTANT label would pass a naive "is it named?" check and still announce identically six
    // times — the shape this session has now hit three cycles running.
    expect(src).toMatch(/aria-label=\{`Remove sub-goal: \$\{s\.length > 60 \? `\$\{s\.slice\(0, 60\)\}…` : s\}`\}/)
    expect(/aria-label="Remove sub-goal"/.test(src), 'a constant here would announce 6 identical buttons').toBe(false)
  })

  it('the add-a-sub-goal input is named', () => {
    expect(src).toMatch(/aria-label="New sub-goal"/)
  })

  it('the sibling add control still carries its label (the pattern that was already right)', () => {
    // If this regresses, the file has lost the convention the fix was matched to.
    expect(src).toMatch(/<IconButton icon=\{Plus\} label="Add sub-goal"/)
  })
})

describe('no icon-only button in either plan-review surface is unnamed', () => {
  // Source-level rail, deliberately NARROW: it only judges buttons whose entire body is a lucide icon
  // element. That is decidable from source — unlike "is this control named?", which needs the DOM (a
  // wrapping <label> or an ancestor is invisible here; in the settings cycle a source rail reported 8
  // offenders against the DOM's 0).
  //
  // Finding a tag's end is NOT `[^>]*>`: that stops at the `>` inside `onClick={() => f()}`, so the
  // first version of this rail matched NOTHING and both assertions below were vacuously green. The
  // vacuity test at the bottom is what caught it. Track brace depth instead.
  const iconOnlyButtons = (src: string) => {
    const out: Array<{ line: number; tag: string; named: boolean }> = []
    const re = /<button\b/g
    let m: RegExpExecArray | null
    while ((m = re.exec(src)) !== null) {
      let depth = 0
      let end = -1
      for (let i = m.index + 1; i < src.length; i++) {
        const ch = src[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (depth === 0 && ch === '>') { end = i + 1; break }
      }
      if (end === -1) continue
      const close = src.indexOf('</button>', end)
      if (close === -1) continue
      const tag = src.slice(m.index, end)
      const body = src.slice(end, close).trim()
      // Body is EXACTLY one self-closing icon element and nothing else.
      // `\w*`, not `\w+`: the icon is often a SINGLE letter (`<X size={14} />`), and `\w+` demands a
      // second word character — which is why the first version of this scanner matched zero buttons
      // and reported a clean sweep. The vacuity test below is the only reason that surfaced.
      if (!/^<[A-Z]\w*[^>]*\/>$/.test(body)) continue
      out.push({ line: src.slice(0, m.index).split('\n').length, tag, named: /aria-label/.test(tag) })
    }
    return out
  }

  for (const rel of ['pages/loops/LoopPlanReview.tsx', 'pages/code/CodePlanReview.tsx']) {
    it(`${rel.split('/').pop()} has no unnamed icon-only button`, () => {
      const offenders = iconOnlyButtons(code(rel)).filter((b) => !b.named)
        .map((b) => `line ${b.line}: ${b.tag.replace(/\s+/g, ' ').slice(0, 90)}`)
      expect(
        offenders,
        `An icon-only button with no aria-label is announced as just "button":\n  ${offenders.join('\n  ')}`,
      ).toEqual([])
    })
  }

  it('the rail is not vacuously green — it finds the button it guards', () => {
    // The fixed sub-goal remove button IS an icon-only button, so the scanner must see it (and see it
    // as named). Without this, a broken matcher reports a clean sweep forever.
    const found = iconOnlyButtons(code('pages/loops/LoopPlanReview.tsx'))
    expect(found.length, 'the scanner must find at least the sub-goal remove button').toBeGreaterThan(0)
    expect(found.some((b) => b.named && /Remove sub-goal/.test(b.tag))).toBe(true)
    // And it must still flag the same shape without a label.
    const sample = `<button type="button" onClick={() => f()}><X size={14} /></button>`
    const s2 = iconOnlyButtons(sample)
    expect(s2.length).toBe(1)
    expect(s2[0].named).toBe(false)
  })
})
