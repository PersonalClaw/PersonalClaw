import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { AssistantActions } from '../pages/chat/MessageActions'

// ── A control that vanishes from the tab order AT ITS LIMIT ───────────────────────────
//
// The sibling failure to the validity-gated submit (`disabledReason.test.tsx`): a pager arrow,
// an "up one level", a "back" — natively `disabled` once the user reaches the boundary.
//
// Two costs, and the second is the one that makes this worse than a silent submit:
//
//  1. It cannot say WHY. "Previous answer" is dim and inert with `title: "Previous answer"` —
//     no hint that the reason is "you are already on the first one".
//  2. **It destroys the user's own focus, mid-task.** The user is pressing the arrow. The press
//     that reaches the limit removes that very button from the tab order, so `activeElement`
//     falls back to <body> and the next Tab starts from the top of the document. A keyboard
//     user paging through three regenerated answers is dumped out of the message they were
//     reading. Nothing about that is discoverable — the control they were using disappeared
//     from under them.
//
//     Measured in Chromium on the running app (not inferred): focus a live button, set the
//     native attribute → `activeElement: "body", still: false, tabbable: false`. Set
//     `aria-disabled` instead → `still: true, tabbable: true`.
//
// The canonical form is the one `Button.disabledReason` and `unavailableWhen()` already
// implement (cycles 56-60): `aria-disabled` instead of the native attribute, so the control
// keeps its tab stop and announces itself unavailable, with the reason on `title` and the click
// refused in code. This rail extends that rule from validity gates to POSITION boundaries.
//
// ⚠️ SCOPE — a position boundary, NOT an emptiness gate. `knowledge/KnowledgeDetail`'s Insights
// disclosure is `disabled={!hasMore}` and is deliberately left native. It is not "you are at
// the end of a sequence", it is "there is nothing behind this control at all": the bar's own
// label already reads "No insights yet", and `aria-expanded` is dropped when `!hasMore`, so it
// is not acting as a disclosure. A tab stop there would be a dead stop announcing a fact the
// visible text already carries. Emptiness gates are out of this rail's population on purpose;
// widening it to cover them would be a different ruling, not a stricter version of this one.
//
// The two ICON-BUTTON primitives are a separate population, covered by its own rail at the
// bottom of this file. `IconButton` and `SquareIconButton` map `disabled` to `aria-disabled`
// internally and never set the native attribute, so their boundary-gated reorder controls never
// lose their tab stop — they fail cost 1 but not cost 2. Both gained `disabledReason` so the
// keyboard user who DOES land on them hears the limit; being icon-only, they have no visible
// text to carry it either.

describe('a pager arrow at its limit keeps its tab stop', () => {
  const props = {
    text: 'an answer', isLast: true, variantCount: 3,
    onCopy: vi.fn(), onRegenerate: vi.fn(), onFork: vi.fn(), onSpeak: vi.fn(), onSwitchVariant: vi.fn(),
  }

  it('names the limit instead of going silent at the first answer', () => {
    render(<AssistantActions {...props} variantIdx={0} />)
    const prev = screen.getByRole('button', { name: 'Previous answer' })
    expect(prev.hasAttribute('disabled'), 'the native attribute would remove the tab stop').toBe(false)
    expect(prev.getAttribute('aria-disabled')).toBe('true')
    expect(prev.getAttribute('title')).toBe('Previous answer — Already at the first answer')
  })

  it('names the limit at the last answer', () => {
    render(<AssistantActions {...props} variantIdx={2} />)
    const next = screen.getByRole('button', { name: 'Next answer' })
    expect(next.hasAttribute('disabled')).toBe(false)
    expect(next.getAttribute('aria-disabled')).toBe('true')
    expect(next.getAttribute('title')).toBe('Next answer — Already at the last answer')
  })

  it('still refuses the click at the limit', () => {
    const onSwitchVariant = vi.fn()
    render(<AssistantActions {...props} variantIdx={0} onSwitchVariant={onSwitchVariant} />)
    fireEvent.click(screen.getByRole('button', { name: 'Previous answer' }))
    expect(onSwitchVariant, 'aria-disabled is advisory — the click must be refused in code').not.toHaveBeenCalled()
  })

  it('STAYS IN THE TAB ORDER when the press reaches the limit', () => {
    // The regression this rail exists for: the arrow the user is pressing must not drop out of
    // the tab order under them.
    //
    // 🪤 THIS CANNOT BE ASSERTED AS FOCUS IN JSDOM. jsdom does not implement blur-on-disable, so
    // `document.activeElement` survives a native `disabled` there and a focus-based assertion
    // passes with OR without this fix — a vacuous test that reads like proof. (Confirmed by
    // stashing the fix: the focus assertion still passed while the title assertions failed.)
    // The browser behaviour was measured separately, in Chromium on the running app: focusing a
    // live button and setting the native attribute moved activeElement to `<body>`
    // (`still: false, tabbable: false`), while `aria-disabled` kept it (`still: true,
    // tabbable: true`). So what is asserted HERE is the property jsdom does model faithfully —
    // absence of the native attribute, which is exactly what decides tab-order membership.
    const { rerender } = render(<AssistantActions {...props} variantIdx={1} />)
    const prev = screen.getByRole('button', { name: 'Previous answer' })
    prev.focus()
    expect(document.activeElement).toBe(prev)
    fireEvent.click(prev)
    rerender(<AssistantActions {...props} variantIdx={0} />)   // the host echoes the new index back
    const clamped = screen.getByRole('button', { name: 'Previous answer' })
    expect(clamped.hasAttribute('disabled'), 'the native attribute is what evicts it from the tab order').toBe(false)
    expect(clamped.getAttribute('aria-disabled')).toBe('true')
  })

  it('leaves a mid-sequence arrow completely alone', () => {
    render(<AssistantActions {...props} variantIdx={1} />)
    for (const name of ['Previous answer', 'Next answer']) {
      const b = screen.getByRole('button', { name })
      expect(b.getAttribute('aria-disabled'), `${name} is live at 2/3`).toBe(null)
      expect(b.getAttribute('title')).toBe(name)
    }
  })
})

// ── The tree-wide ratchet ─────────────────────────────────────────────────────────────

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** A gate that reads "you are at one end of a sequence": an index pinned to 0 or to the last
 *  slot, a named at-start/at-end flag, or a walk that cannot go further up. Deliberately does
 *  NOT match emptiness (`!hasMore`, `length === 0`) — see the scope note above. */
const BOUNDARY = /\b(?:atStart|atEnd)\b|\b(?:index|idx|i)\s*(?:===\s*0|<=\s*0)|===\s*(?:\w+\.)?(?:length|count|total)\s*-\s*1|\bparent\s*===\s*path\b/

/** Complete opening tags, tracking {} depth so a `>` inside an attribute value cannot truncate
 *  the match. (The lesson from cycle 61: a regex that cannot parse half its inputs still prints
 *  a confident distribution — so this asserts its own extraction rate below.) */
function boundaryGated(): Array<{ file: string; line: number; tag: string; src: string }> {
  const out: Array<{ file: string; line: number; tag: string; src: string }> = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(/<(?:Button|button|motion\.button)\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < text.length; i++) {
        const ch = text[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) {
          const tag = text.slice(m.index!, i + 1)
          // Both shapes: still-native `disabled={atEnd}`, and converted `unavailableWhen(atEnd, …)`.
          // Matching only the native shape would make this rail stop covering the very sites it
          // just fixed — the trap the validity rail hit when its population fell 37 → 29.
          const gate = /(?<!aria-)disabled=\{([\s\S]*?)\}|unavailableWhen\(([\s\S]*?),/.exec(tag)
          if (gate && BOUNDARY.test(gate[1] ?? gate[2] ?? '')) {
            out.push({
              file: abs.slice(SRC.length + 1),
              line: text.slice(0, m.index).split('\n').length,
              tag,
              src: text,
            })
          }
          break
        }
      }
    }
  }
  return out
}

describe('no control goes silently dead at its limit', () => {
  const gated = boundaryGated()

  it('finds the boundary-gated controls (not vacuously green)', () => {
    // 4 at the time of writing: the two chat pager arrows, WorkspacePicker's "up one level",
    // and QuestionSlider's Back. A rail that matches nothing looks exactly like a clean tree.
    expect(gated.length, 'the matcher must find position-boundary gates').toBeGreaterThanOrEqual(4)
  })

  it('has NO unexplained boundary-gated control', () => {
    const unexplained = gated.filter((t) => !/disabledReason|unavailableWhen\(/.test(t.tag))
    expect(
      unexplained.length,
      `${unexplained.length} control(s) go dead at a boundary without naming the limit, and drop ` +
        'out of the tab order under the user who pressed them. Pass `disabledReason` on a ' +
        '<Button>, or spread `unavailableWhen()` on a raw <button>:\n  ' +
        unexplained.map((t) => `${t.file}:${t.line}`).join('\n  '),
    ).toBe(0)
  })

  it('restates the dim on aria-disabled wherever it left the native attribute', () => {
    // `disabled:opacity-40` stops firing the moment the native attribute is gone, so a converted
    // control renders at FULL opacity and reads as live unless the dim is restated. This is the
    // silent-visual half of the conversion and nothing else catches it.
    // Checked per FILE, not per tag: several of these share one extracted className constant,
    // so the utility legitimately lives a few lines away from the tag that uses it.
    const missing = gated
      .filter((t) => /unavailableWhen\(/.test(t.tag))
      .filter((t) => !/aria-disabled:opacity-\d+/.test(t.src))
    expect(
      missing.length,
      'converted control(s) never restate their dim on `aria-disabled:`, so they render as live:\n  ' +
        missing.map((t) => `${t.file}:${t.line}`).join('\n  '),
    ).toBe(0)
  })
})

// ── The icon-button population ────────────────────────────────────────────────────────
//
// `IconButton` and `SquareIconButton` both map `disabled` → `aria-disabled` and never set the
// native attribute, so a boundary-gated one keeps its tab stop: a keyboard user lands on it. What
// they had was nothing to say on arrival. Measured live at `#/settings/models` → Chat role (a
// 3-model chain), before this change:
//
//   { name: "Move global.anthropic.claude-fable-5 up", native: false, aria: true,
//     focusable: true, title: "Move global.anthropic.claude-fable-5 up" }
//
// Focusable, announced unavailable, and mute about why — and an icon-only button has no visible
// text to carry the reason either, so the user has no way at all to learn it. Both primitives
// gained `disabledReason`, appended to `title` (the accessible DESCRIPTION, since the name comes
// from `aria-label`).

describe('a boundary-gated icon button names its limit', () => {
  const REORDER = [
    'pages/settings/ModelsPanel.tsx',
    'pages/code/CodePlanReview.tsx',
    'pages/loops/LoopPlanReview.tsx',
  ]

  /** Complete opening tags for the two icon-button primitives, brace-depth tracked. */
  const iconButtonTags = (text: string) => {
    const out: string[] = []
    for (const m of text.matchAll(/<(?:IconButton|SquareIconButton)\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < text.length; i++) {
        const ch = text[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) { out.push(text.slice(m.index!, i + 1)); break }
      }
    }
    return out
  }

  it.each(REORDER)('%s explains every boundary-gated reorder control', (rel) => {
    const src = readFileSync(join(SRC, rel), 'utf8')
    const gated = iconButtonTags(src).filter((t) => {
      const g = /(?<!aria-)disabled=\{([\s\S]*?)\}/.exec(t)
      return g && BOUNDARY.test(g[1])
    })
    // Two per file: the up and the down of one reorder pair.
    expect(gated.length, `${rel} must still have its reorder pair`).toBe(2)
    for (const tag of gated) {
      expect(tag, `a boundary-gated icon button in ${rel} says nothing on arrival`).toMatch(/disabledReason/)
    }
  })

  it('keeps the reason on the BOUNDARY branch of a compound gate', () => {
    // `ModelsPanel` gates on `saving || i === 0`. A flat `disabledReason` there would explain the
    // wrong cause while a save is in flight — the row's own save state carries that one. So the
    // reason must be conditional on the boundary term, not passed unconditionally.
    const src = readFileSync(join(SRC, 'pages/settings/ModelsPanel.tsx'), 'utf8')
    for (const m of src.matchAll(/disabledReason=\{([^}]*)\}/g)) {
      expect(
        m[1],
        'a compound gate must condition the reason on its boundary term, not state it flatly',
      ).toMatch(/\?/)
    }
  })
})
