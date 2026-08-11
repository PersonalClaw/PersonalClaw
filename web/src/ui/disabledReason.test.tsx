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
    'pages/projects/ProjectsSection.tsx',
    'pages/tasks/TaskForm.tsx',
    'pages/tasks/TaskCreatePage.tsx',
    'pages/agents/AgentCreatePage.tsx',
    'pages/prompts/PromptCreatePage.tsx',
  ]

  for (const rel of ADOPTERS) {
    it(`${rel} explains its disabled submit`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must pass disabledReason').toMatch(/disabledReason=\{/)
      // Conditional on purpose: an unconditional reason would announce "enter a name" on a
      // button disabled because a save is in flight.
      expect(src, 'the reason must be conditional on the missing input').toMatch(/disabledReason=\{!/)
    })
  }

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})
