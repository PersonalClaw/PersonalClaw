import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Button } from './Button'

// ── A disabled submit that cannot say why ─────────────────────────────────────────────
//
// A native `disabled` button is REMOVED FROM THE TAB ORDER, so a keyboard user cannot reach
// it to hear anything — they tab straight past the action they are looking for with no way to
// learn what is missing. Measured on the New-project modal before this change:
//
//   { name: "Create project", nativeDisabled: true, title: null,
//     ariaDescribedby: null, focusable: FALSE }
//
// Censused 37 validity-gated submits (`disabled={!x.trim() …}`) across the app: **37 of 37
// carried no title and no description.** Every one is an action a user is actively trying to
// take, sitting silent behind a condition it will not name.
//
// `disabledReason` swaps the native attribute for `aria-disabled` — semantically unavailable,
// still focusable — and suppresses the click in the handler. The native attribute is stronger
// protection, but a form submit that cannot explain itself is the worse failure.
//
// 🪤 THE REASON CANNOT BE AN sr-only SPAN INSIDE THE BUTTON. That was the first attempt, and
// the accessible NAME became "Create projectEnter a name first" — the action stopped being
// findable by its own name, which is worse than the silence it fixed. An `aria-describedby`
// target outside the button would need a wrapper element at 100+ call sites. So the reason
// rides `title`, already the kit's convention for a supplementary description (ruled cycle 37)
// and simultaneously the sighted tooltip.
//
// 🪤 `loading` KEEPS THE NATIVE ATTRIBUTE even with a reason. An in-flight button must not be
// re-clickable, and `aria-busy` already announces the state (cycle 52) — the reason a button is
// unavailable *while working* is self-evident.

describe('an unavailable button says why', () => {
  it('stays focusable and reachable', () => {
    render(<Button disabled disabledReason="Enter a name first">Create project</Button>)
    const b = screen.getByRole('button', { name: 'Create project' })
    expect(b.hasAttribute('disabled'), 'the native attribute would remove the tab stop').toBe(false)
    expect(b.getAttribute('aria-disabled')).toBe('true')
  })

  it('keeps its accessible NAME clean', () => {
    // The regression that killed the first implementation: the name must be the action, not
    // the action plus an explanation.
    render(<Button disabled disabledReason="Enter a name first">Create project</Button>)
    expect(screen.getByRole('button').textContent).toBe('Create project')
    expect(screen.getByRole('button', { name: 'Create project' })).toBeTruthy()
  })

  it('carries the reason where AT and sighted users both get it', () => {
    render(<Button disabled disabledReason="Enter a project name first">Create project</Button>)
    expect(screen.getByRole('button').getAttribute('title')).toBe('Enter a project name first')
  })

  it('appends to an existing title rather than replacing it', () => {
    // A button with a real tooltip (a shortcut hint) must not lose it.
    render(<Button disabled disabledReason="Enter a name" title="Save (⌘S)">Save</Button>)
    expect(screen.getByRole('button').getAttribute('title')).toBe('Save (⌘S) — Enter a name')
  })

  it('refuses the click', () => {
    // aria-disabled is advisory to the browser — the handler must enforce it.
    const onClick = vi.fn()
    render(<Button disabled disabledReason="nope" onClick={onClick}>Go</Button>)
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('does nothing when the button is ENABLED', () => {
    // The reason must not linger once the condition clears — verified live: filled form →
    // aria-disabled null, title null.
    const onClick = vi.fn()
    render(<Button disabledReason="stale reason" onClick={onClick}>Go</Button>)
    const b = screen.getByRole('button')
    expect(b.getAttribute('aria-disabled')).toBeNull()
    expect(b.getAttribute('title')).toBeNull()
    fireEvent.click(b)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('stays NATIVELY disabled with no reason given', () => {
    // Correct for a button whose unavailability is self-evident from context.
    render(<Button disabled>Go</Button>)
    expect(screen.getByRole('button').hasAttribute('disabled')).toBe(true)
  })

  it('stays NATIVELY disabled while loading, even with a reason', () => {
    render(<Button loading disabledReason="x">Go</Button>)
    expect(screen.getByRole('button').hasAttribute('disabled')).toBe(true)
  })
})

// ── The call-site half ────────────────────────────────────────────────────────────────
// The primitive existing is not the fix; a surface has to pass a reason. This pins the five
// migrated create-submits and deliberately does NOT assert the other 32 — the reason text is
// per-form copy, and inventing 32 strings without reading each form is how a sweep ships
// wrong messages.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

describe('the migrated submits pass a reason', () => {
  const ADOPTERS = [
    // cycle 56 — the five create-submits that proved the primitive
    'pages/projects/ProjectsSection.tsx',
    'pages/tasks/TaskForm.tsx',
    'pages/tasks/TaskCreatePage.tsx',
    'pages/agents/AgentCreatePage.tsx',
    'pages/prompts/PromptCreatePage.tsx',
    // cycle 59 — the rest of the Button-primitive tail, per-form copy read from each form
    'pages/settings/MemoryPanel.tsx',
    'pages/settings/MultiInstanceCard.tsx',
    'pages/settings/DesignPanel.tsx',
    'pages/prompts/PromptDetail.tsx',
    'pages/prompts/SnippetDetail.tsx',
    'pages/tasks/TaskDetail.tsx',
    'pages/inbox/InboxDetail.tsx',
    'pages/triggers/LifecycleDetail.tsx',
    'pages/schedule/ScheduleDetail.tsx',
    'pages/code/WorkspacePicker.tsx',
    'pages/workflows/WorkflowAsk.tsx',
    'pages/workflows/SteeringPanel.tsx',
    'pages/files/comments/CommentLayer.tsx',
    'pages/loops/LoopCockpitPage.tsx',
    'pages/apps/AppsSection.tsx',
    'pages/ChatPage.tsx',
  ]

  for (const rel of ADOPTERS) {
    it(`${rel} explains its disabled submit`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must pass disabledReason').toMatch(/disabledReason=\{/)
      // Conditional on purpose: an unconditional reason would announce "enter a name" on a
      // button disabled because a save is in flight. The condition need not START with `!` —
      // WorkflowAsk gates on `kind === 'text' && !text.trim()`, and the apps installer leads
      // with a security verdict — so assert it is a TERNARY yielding undefined when satisfied,
      // not a bare string.
      expect(src, 'the reason must be conditional, not a constant').toMatch(/disabledReason=\{[^}]*\?/)
      expect(src, 'the reason must fall back to undefined when nothing is missing').toMatch(/disabledReason=\{[^}]*undefined/)
    })
  }

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})

// ── The raw-<button> tail, ratcheted ──────────────────────────────────────────────────
// 29 of 37 validity-gated submits now explain themselves. The remaining 8 are RAW `<button>`s,
// not the `Button` primitive, so they cannot inherit the prop — each needs `aria-disabled` +
// a click guard + a title written by hand, which is a different change from passing a prop.
// This ratchets the count so the tail can only shrink.

/** Complete opening tags for a validity-gated submit, tracking {} depth so a `>` inside an
 *  attribute value cannot truncate the match. */
function gatedSubmits(): Array<{ file: string; line: number; tag: string }> {
  const out: Array<{ file: string; line: number; tag: string }> = []
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
          // Count BOTH shapes of a validity gate: the native `disabled={!x.trim()}` and the
          // converted `{...unavailableWhen(!x.trim(), …)}`. Moving the 10 raw buttons onto the
          // helper dropped this matcher's population 37 → 29 and tripped the vacuity floor
          // below — because the floor was measuring the OLD shape only, so it stopped covering
          // the very sites that had just been fixed.
          if (
            /disabled=\{[^}]*!\w+[\w.]*\.trim\(\)|disabled=\{[^}]*length === 0|unavailableWhen\(/.test(tag)
          ) {
            out.push({ file: abs.slice(SRC.length + 1), line: text.slice(0, m.index).split('\n').length, tag })
          }
          break
        }
      }
    }
  }
  return out
}

describe('the unexplained-submit tail only shrinks', () => {
  const gated = gatedSubmits()
  // A site is explained by EITHER path: the `Button` prop, or the raw-button helper.
  const unexplained = gated.filter((t) => !/disabledReason|unavailableWhen\(/.test(t.tag))

  it('finds the gated submits (not vacuously green)', () => {
    expect(gated.length, 'the matcher must find validity-gated submits').toBeGreaterThan(30)
  })

  it('has NO unexplained validity-gated submit left', () => {
    // The raw-<button> tail closed with `unavailableWhen`, so the floor is now ZERO: every
    // validity-gated submit in the tree explains itself by one path or the other.
    //
    // The count was 10, not 8, because an earlier census walked only `pages/` and missed
    // `app/Onboarding.tsx` and `ui/PlanningWalkthrough.tsx`. The rail walks the whole tree,
    // which is why its number is the one to trust.
    expect(
      unexplained.length,
      `${unexplained.length} validity-gated submits still cannot say why they are unavailable. ` +
        'Pass `disabledReason` on a <Button>, or spread `unavailableWhen()` on a raw <button>:\n  ' +
        unexplained.map((t) => `${t.file}:${t.line}`).join('\n  '),
    ).toBe(0)
  })

  it('every holdout is a RAW button, not the primitive', () => {
    // If a `<Button>` shows up here, someone added a gated submit without the prop — the
    // one-line fix is available and the ratchet should not absorb it.
    const primitives = unexplained.filter((t) => /^<Button\b/.test(t.tag)).map((t) => `${t.file}:${t.line}`)
    expect(
      primitives,
      'a Button-primitive submit can pass disabledReason directly — do not leave it silent:\n  ' +
        primitives.join('\n  '),
    ).toEqual([])
  })
})
