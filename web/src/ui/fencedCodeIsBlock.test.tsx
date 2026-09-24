import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { Markdown } from './Markdown'

// ── A fenced code block with NO language is still a fence (#2515) ─────────────────────────────────
//
// `renderCode`'s predicate was `!!className || str.includes('\n')`. react-markdown only sets a
// `language-*` className when the fence declares a language, and the line above strips the trailing
// newline mdast-util-to-hast appends — so a fence with no language AND a single content line failed
// both tests and came out as an inline `<code>` chip.
//
// Measured in a real browser (full numbers in the issue thread), one `artifact_get` result, region
// `clientWidth` 338:
//
//   arm  fence                                        rendered as       width            verdict
//   A    no language, 1 line, WITH spaces             inline chip       rectWidth 338    wrapped, contained
//   B    no language, 1 line, NO spaces (217-char)    inline chip       rectWidth 1424   overflowed by 1086px
//   C    arm B's bytes + a second line                <pre> CodeBlock   clientW 338      contained, scrollable
//
// Arm B's chip had `white-space: normal` and `overflow-x: visible`, so the token could neither wrap
// nor scroll, and because the chip was a sibling of the prose rather than a self-scrolling `<pre>` it
// forced the WHOLE result region onto a 1424px canvas. Re-measured with the panel at `clientWidth`
// 1218 the chip was still 1424px, so it is not a narrow-panel artifact: the token cannot wrap at any
// width. Arm C — the same bytes, one newline apart — already got a `<pre>`, a copy affordance and the
// canonical `tabIndex=0` + `role="group"` + `aria-label` trio.
//
// 🪤 jsdom HAS NO LAYOUT, so `rectWidth` cannot be re-measured here and a width assertion would read
// 0 for every arm and pass vacuously. The faithful rail is the CONTAINER SHAPE, which is what the
// widths were evidence about: a `<pre class="overflow-x-auto">` is contained by construction, an
// inline chip in the prose flow is not. So arms B and C must produce the same four values.
//
// 🪤 THE SIGNAL IS THE `<pre>` PARENT, NOT `node.position`. Both were measured; they disagree on one
// shape. `mdast-util-to-hast` wraps every block code node in a `<pre>` and never wraps an inline
// span, whereas the position line-span is only a proxy for that: a single-line 4-space INDENTED block
// reads `start.line === end.line` (3->3 measured) and would have stayed an inline chip — the very
// same defect, reachable with the very same payload. The last test below pins that shape.

const FENCE = '```'

// The measured payload's shape: JWT-ish, 217 chars, not one break opportunity in it.
const TOKEN = 'eyJhbGciOiJIUzI1NiJ9.' + 'QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5'.repeat(4) + 'QUJD'

const ARM_A = `# h\n\n${FENCE}\nsome short words here\n${FENCE}\n`
const ARM_B = `# h\n\n${FENCE}\n${TOKEN}\n${FENCE}\n`
const ARM_C = `# h\n\n${FENCE}\n${TOKEN}\nsecond line\n${FENCE}\n`

/** The four values the defect showed up in. `onFileClick` selects the chat component map, which is
 *  the SECOND `code` call site — `componentsWith()` calls `renderCode` separately from `COMPONENTS`,
 *  so a fix threaded into only one of them would leave chat on the old predicate. */
function shapeOf(src: string, onFileClick?: (p: string) => void) {
  const { container } = render(<Markdown onFileClick={onFileClick}>{src}</Markdown>)
  const code = container.querySelector('code')
  const pre = code?.closest('pre') ?? null
  return {
    pre: !!pre,
    tabIndex: pre?.getAttribute('tabindex') ?? undefined,
    role: pre?.getAttribute('role') ?? undefined,
    label: pre?.getAttribute('aria-label') ?? undefined,
  }
}

const BLOCK = { pre: true, tabIndex: '0', role: 'group', label: 'Code' }

describe('a no-language fence renders as a block, one line or many (#2515)', () => {
  it('arm C is a block on its own — the B-vs-C comparison is not vacuous', () => {
    expect(TOKEN.length, 'the measured token length').toBe(217)
    expect(TOKEN.includes(' '), 'an unbreakable token has no break opportunity').toBe(false)
    expect(shapeOf(ARM_C)).toEqual(BLOCK)
  })

  it('arm B renders as a block — the same shape as arm C, one newline apart', () => {
    expect(shapeOf(ARM_B), 'a single-line no-language fence is still a fence').toEqual(shapeOf(ARM_C))
    expect(shapeOf(ARM_B)).toEqual(BLOCK)
  })

  it("arm B's block owns a horizontal scrollport, so the token scrolls instead of widening the page", () => {
    const { container } = render(<Markdown>{ARM_B}</Markdown>)
    const pre = container.querySelector('code')!.closest('pre')!
    expect(pre.className, 'the containment the 1424px chip had no way to provide').toMatch(/overflow-x-auto/)
  })

  it('arm A stays contained too — spaces in the token are not the classifier', () => {
    // Arm A was ALSO an inline chip before this change; it only looked fine because its content had
    // break opportunities. A and B differ solely in whether the token can wrap, which cannot decide
    // block-vs-inline, so both land in the same contained `<pre>`.
    expect(shapeOf(ARM_A)).toEqual(BLOCK)
  })

  it('genuinely inline backticked code is still an inline chip, never a block', () => {
    // The guard against "fix" by flipping the predicate to always-true: a code SPAN is not a fence.
    const { container } = render(<Markdown>{'hello `inlineToken` world'}</Markdown>)
    const code = container.querySelector('code')!
    expect(code.closest('pre'), 'an inline span has no <pre> parent in the hast tree').toBeNull()
    expect(code.className, 'it keeps the chip styling').toMatch(/rounded-sm/)
    expect(code.textContent).toBe('inlineToken')
  })

  it('the chat component map gets the same classification — both call sites, not one', () => {
    expect(shapeOf(ARM_B, () => {}), 'componentsWith() calls renderCode separately').toEqual(BLOCK)
    const { container } = render(<Markdown onFileClick={() => {}}>{'hello `inlineToken` world'}</Markdown>)
    expect(container.querySelector('code')!.closest('pre')).toBeNull()
  })

  it('a single-line 4-space indented block is a block as well — why the `<pre>` parent, not position', () => {
    // Reads `start.line === end.line`, so the position proxy would have left this an inline chip.
    expect(shapeOf('# h\n\n    indentedOneLine\n')).toEqual(BLOCK)
  })
})
