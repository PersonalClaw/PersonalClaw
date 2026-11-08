import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import katex from 'katex'
import { MATH_MAX_SIZE, Markdown } from './Markdown'
import { InboxMessageBody } from '../pages/inbox/ForeignContent'
import type { InboxItem } from '../lib/api'

// ── Math in a message stays in its box ─────────────────────────────────────────────────────────
//
// 🔴 Math is drawn at the sizes it asks for, and a size can be far larger than the message that holds
// it: a rule thousands of pixels wide, a box raised far above its line, or a negative space that
// moves what follows to the left of the message. A quoted message's frame did not clip what it held,
// so its math could draw outside the frame. Now the sizes math asks for are capped (10em), a quoted
// message clips to its frame, a message's rendered body clips to its box, and a math display scrolls
// a long formula inside its own box. jsdom lays nothing out, so this file checks the sizes and the
// containment each element carries; `e2e/mathStaysInItsBox.spec.ts` measures where the math lands in
// a browser.

/** Every em length the math under `root` was drawn with. */
function emsOf(root: Element): number[] {
  return Array.from(root.querySelectorAll('.katex [style]')).flatMap((el) =>
    Array.from((el.getAttribute('style') ?? '').matchAll(/(-?\d+(?:\.\d+)?)em/g), (m) => Number(m[1])))
}

/** The em lengths of `tex` as the renderer draws it on its own. */
function emsOfOwn(tex: string, displayMode: boolean, maxSize?: number): number[] {
  const own = document.createElement('div')
  own.innerHTML = katex.renderToString(tex, { displayMode, throwOnError: false, ...(maxSize ? { maxSize } : {}) })
  return emsOf(own)
}

const OWNER = 'keyur-golani'
const quoted = (message: string) => ({
  id: 'C1_1', channel: 'C1', message, sender_id: 'U1', sender_name: 'Sender', owner_username: 'someone-else',
  classification: 'needs_reply', status: 'pending', created_at: 1700000000, item_kind: 'message',
} as unknown as InboxItem)

/** A real formula: a table strut, a space and a raised box, each a size it asks for. */
const REAL = String.raw`\left(\begin{array}{c|c} A & \rule{0pt}{2em} B \\ \hline C & D \end{array}\right)\kern{2em}\raisebox{0.25em}{x}\int_0^1 x^2\,dx`
/** A formula whose sizes are computed, not asked for: a tall matrix's delimiters. */
const TALL = String.raw`\begin{pmatrix} ${Array.from({ length: 24 }, (_, i) => i + 1).join(' \\\\ ')} \end{pmatrix}`

describe('the sizes math asks for are capped', () => {
  // `$$…$$` within a line is inline math; on lines of its own it is a display.
  const inline = (tex: string) => `Notes $$${tex}$$ end`
  const displayed = (tex: string) => `Notes\n\n$$\n${tex}\n$$`
  it.each([
    ['a rule, inline', String.raw`\rule{4000px}{3000px}`, inline],
    ['a rule, displayed', String.raw`\rule{4000px}{3000px}`, displayed],
    ['a raised box, inline', String.raw`\raisebox{4000px}{x}`, inline],
    ['a raised box, displayed', String.raw`\raisebox{4000px}{x}`, displayed],
  ])('%s, in a quoted message, is drawn at the 10em cap', (_what, tex, written) => {
    const { container } = render(<InboxMessageBody item={quoted(written(tex))} owner={OWNER} />)
    // Exactly as the same formula written at 10em is drawn.
    expect(emsOf(container)).toEqual(emsOfOwn(tex.replace(/\d+px/g, '10em'), written === displayed, MATH_MAX_SIZE))
  })

  it('a real formula is drawn as the renderer draws it with no cap', () => {
    const { container } = render(<Markdown>{`$$\n${REAL}\n$$`}</Markdown>)
    expect(container.querySelector('.katex-display'), 'a display').not.toBeNull()
    expect(emsOf(container)).toEqual(emsOfOwn(REAL, true))
    expect(container.querySelector('.katex-error'), 'the formula parsed').toBeNull()
  })

  it("a size the renderer computes is not capped: a tall matrix's delimiters", () => {
    const { container } = render(<Markdown>{`$$\n${TALL}\n$$`}</Markdown>)
    expect(Math.max(...emsOf(container))).toBeGreaterThan(14)
  })
})

describe('what holds the math clips to its box', () => {
  const css = readFileSync(join(process.cwd(), 'src', 'design', 'tokens.css'), 'utf8')
  /** The declarations of the first rule for `selector` at or after `from`, whitespace folded. */
  const ruleFor = (selector: string, from = 0) => {
    const at = css.indexOf(`${selector} {`, from)
    return at < 0 ? '' : css.slice(at, css.indexOf('}', at)).replace(/\s+/g, ' ')
  }
  const SAFARI = '@supports not (overflow-clip-margin: 4px) {'

  it("a quoted message's frame clips what it holds", () => {
    const { getByRole } = render(<InboxMessageBody item={quoted('Notes')} owner={OWNER} />)
    expect(getByRole('group', { name: /quoted, not your own$/ }).className).toContain('overflow-clip')
  })

  it("a message's rendered body clips to its box, with room for a focus ring past the clip", () => {
    const { container } = render(<Markdown>{'Read [the notes](https://example.com/notes) first.'}</Markdown>)
    expect((container.firstElementChild as HTMLElement).classList.contains('md-clip')).toBe(true)
    const rule = ruleFor('.md-clip')
    expect(rule).toContain('overflow: clip;')
    expect(rule).toContain('overflow-clip-margin: 4px;')
    // Where the clip margin is read, the body keeps main's box: no padding, no margin of its own.
    expect(rule).not.toMatch(/padding|margin: -/)
    // Where it is not read (Safari), a padding inside a margin of the same size is the ring's room.
    expect(css).toContain(SAFARI)
    const safari = ruleFor('.md-clip', css.indexOf(SAFARI))
    expect(safari).toContain('padding: 4px;')
    expect(safari).toContain('margin: -4px;')
  })

  it("a body's text with no break of its own wraps at its edge, and a diagram's labels keep theirs", () => {
    expect(ruleFor('.md-clip')).toContain('overflow-wrap: break-word;')
    expect(ruleFor('.md-clip svg')).toContain('overflow-wrap: normal;')
  })

  it("a source's number stays on its link's last word, and the link's words wrap", () => {
    render(<Markdown sourceOf={() => 1}>{'See [the release notes for this week](https://example.com/notes).'}</Markdown>)
    const link = screen.getByRole('link', { name: /the release notes/ })
    const wrapper = link.closest('a')!.parentElement!
    expect(wrapper.className).not.toContain('whitespace-nowrap')
    expect(wrapper.textContent).toContain('the release notes for this week\u2060')
    expect(wrapper.querySelector('sup')?.textContent).toContain('1')
    // The number is one piece: its words for a screen reader open it, and no break falls in it.
    expect(wrapper.querySelector('sup')?.className).toContain('whitespace-nowrap')
  })

  it('a body that runs its widget blocks clips to its box too', () => {
    const { container } = render(<Markdown widgets>{'Notes\n\n<widget title="Chart"><div>chart</div></widget>'}</Markdown>)
    expect((container.firstElementChild as HTMLElement).classList.contains('md-clip')).toBe(true)
  })

  it('a math display scrolls a long formula inside its own box', () => {
    const rule = ruleFor('.md-clip .katex-display')
    expect(rule).toContain('overflow-x: auto;')
    expect(rule).toContain('overflow-y: hidden;')
    expect(rule).toContain('margin-block: 0.625em;')
  })

  it("a math display's focus ring is the app's own, outside it, never under its formula", () => {
    expect(css).not.toMatch(/katex-display[^{]*\{[^}]*outline-offset:\s*(-|calc\(\s*-)/)
  })

  it('every math display is a tab stop named Formula, as a code block is', () => {
    const { container } = render(<Markdown>{`$$\n${REAL}\n$$`}</Markdown>)
    const display = container.querySelector('.katex-display')!
    expect(display.getAttribute('tabindex')).toBe('0')
    expect(display.getAttribute('role')).toBe('group')
    expect(display.getAttribute('aria-label')).toBe('Formula')
    // Inline math scrolls nothing, so it is no tab stop.
    const { container: inline } = render(<Markdown>{'Notes $$x^2$$ end'}</Markdown>)
    expect(inline.querySelector('.katex')!.closest('[tabindex]')).toBeNull()
  })
})
