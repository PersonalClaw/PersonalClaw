import { describe, it, expect } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Toaster } from './Toaster'
import { Button } from './Button'

// ── A state that exists for 5 seconds still has to reach a screen reader ─────────────
//
// The TIME axis. Every a11y probe in this session drives a surface, waits for it to settle,
// then measures — so anything that exists only in a window was never looked at. Cycle 51
// found a real defect living in a 600ms slice while data loaded; this covers the two
// transient mechanisms the whole app routes through.
//
// 1. TOASTS WERE NEVER ANNOUNCED. `notify()` is the app's ONE channel for "that worked" /
//    "that failed", dispatched from ~everywhere via a `ne:toast` CustomEvent. The host had
//    no role and no `aria-live`, so the text sat in the a11y tree as ordinary content that
//    nothing prompted anyone to read, and was gone after 5s. axe reports NOTHING here — a
//    missing announcement is not a rule violation, which is exactly why it survived every
//    scan.
//
// 2. AN IN-FLIGHT BUTTON SAID NOTHING. `Button`'s `loading` cross-fades the label to
//    opacity 0 and swaps in an `aria-hidden` spinner. Sighted users see the action is in
//    flight; everyone else got a button that went quiet and disabled while keeping its
//    original name. Measured: 0 buttons in the app carried `aria-busy` before this.
//
// The split into TWO live regions is deliberate: `role="status"` (polite) lets a
// confirmation wait for a pause, `role="alert"` (assertive) interrupts for a failure. One
// region cannot carry both urgencies, and an error queued behind three confirmations is the
// wrong tradeoff.

describe('toasts are announced', () => {
  function fire(message: string, level: 'info' | 'success' | 'error') {
    act(() => {
      window.dispatchEvent(new CustomEvent('ne:toast', { detail: { message, level } }))
    })
  }

  it('mounts both live regions BEFORE any toast exists', () => {
    // A live region created at the same moment its content appears is not reliably
    // observed — the regions must already be in the DOM, empty, waiting.
    const { container } = render(<Toaster />)
    expect(container.querySelector('[role="status"][aria-live="polite"]')).not.toBeNull()
    expect(container.querySelector('[role="alert"][aria-live="assertive"]')).not.toBeNull()
  })

  it('routes a success to the POLITE region and an error to the ASSERTIVE one', () => {
    const { container } = render(<Toaster />)
    fire('Project deleted', 'success')
    fire('Upload failed', 'error')
    const polite = container.querySelector('[aria-live="polite"]')!
    const assertive = container.querySelector('[aria-live="assertive"]')!
    expect(polite.textContent).toContain('Project deleted')
    expect(polite.textContent, 'an error must not wait politely').not.toContain('Upload failed')
    expect(assertive.textContent).toContain('Upload failed')
    expect(assertive.textContent, 'a confirmation must not interrupt').not.toContain('Project deleted')
  })

  it('announces only ADDITIONS, so the auto-dismiss does not re-announce', () => {
    const { container } = render(<Toaster />)
    for (const r of container.querySelectorAll('[aria-live]')) {
      expect(r.getAttribute('aria-relevant')).toBe('additions')
    }
  })

  it('does not put the message in the tree twice', () => {
    // The live region owns the announcement; the visible card's text is aria-hidden.
    const { container } = render(<Toaster />)
    fire('Saved the thing', 'success')
    const visibleText = [...container.querySelectorAll('[data-type="body-m"]')]
    expect(visibleText.length).toBeGreaterThan(0)
    for (const el of visibleText) expect(el.getAttribute('aria-hidden')).toBe('true')
  })

  it('keeps the CARD itself exposed — hiding it buries the Dismiss button', () => {
    // Putting aria-hidden on the card is the obvious dedupe and it is wrong: it makes a
    // focusable control a descendant of a hidden element (axe `aria-hidden-focus`,
    // serious). Measured — doing exactly that produced exactly that violation.
    const { container } = render(<Toaster />)
    fire('Saved', 'success')
    const btn = screen.getByRole('button', { name: /^Dismiss/ })
    for (let el = btn.parentElement; el && el !== container; el = el.parentElement) {
      expect(
        el.getAttribute('aria-hidden'),
        'no ancestor of the Dismiss button may be aria-hidden',
      ).not.toBe('true')
    }
  })

  it('names each Dismiss by its message — toasts stack up to 4', () => {
    render(<Toaster />)
    fire('Project deleted', 'success')
    fire('Trigger saved', 'success')
    const names = screen.getAllByRole('button', { name: /^Dismiss/ }).map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(['Dismiss: Project deleted', 'Dismiss: Trigger saved'])
    // A bare "Dismiss" on every card gave a screen-reader user nothing to choose between.
    expect(new Set(names).size, 'each Dismiss must be distinguishable').toBe(names.length)
  })
})

describe('an in-flight button says it is busy', () => {
  it('sets aria-busy while loading', () => {
    render(<Button loading>Create project</Button>)
    expect(screen.getByRole('button').getAttribute('aria-busy')).toBe('true')
  })

  it('does NOT set it when idle', () => {
    // `aria-busy="false"` on every button in the app is noise; the attribute should be
    // absent unless something is actually happening.
    render(<Button>Create project</Button>)
    expect(screen.getByRole('button').getAttribute('aria-busy')).toBeNull()
  })

  it('keeps its accessible name while busy', () => {
    // `loading` fades the label visually. If it removed the name, the button would become
    // an unnamed control mid-action — worse than the silence this fixes.
    render(<Button loading>Create project</Button>)
    expect(screen.getByRole('button', { name: /Create project/ })).toBeTruthy()
  })
})

// ── The rest of the family is NOT fixed here, and the rail records why ───────────────
// 50 call sites hand-roll their own in-flight state — `disabled={busy}` plus a
// `{busy ? <Loader2/> : <Icon/>}` swap — instead of passing `loading`. They get NO
// aria-busy, so this primitive fix reaches 11 of 61 sites.
//
// Converting them is NOT mechanical and is NOT this PR: `loading` cross-fades the WHOLE
// label out for a centred spinner, while the hand-rolled form swaps only the leading icon
// and keeps the text. That is a visible difference on 50 buttons — a visual-language
// decision, logged as an owner taste call rather than guessed at.
//
// This assertion pins the population so the number cannot drift silently while the
// decision is pending.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

describe('the hand-rolled in-flight population is pinned', () => {
  it('has not grown', () => {
    const hand = walk(SRC).flatMap((abs) => {
      const text = readFileSync(abs, 'utf8')
      return [...text.matchAll(/\{\s*(?:busy|saving|checking|applying|running|submitting|pending)\s*\?\s*<Loader2/g)]
        .map(() => abs.slice(SRC.length + 1))
    })
    // Measured 24 across 18 files at the time of writing. (A line-oriented grep said 22 —
    // this matcher tolerates the whitespace variants, so 24 is the real number. Trust the
    // rail over the grep; see the JSX-matcher note in the ledger.) A NEW hand-rolled
    // spinner should either pass `loading` (and inherit aria-busy) or consciously raise
    // this ceiling.
    expect(
      hand.length,
      `${hand.length} hand-rolled in-flight spinners (was 24). Prefer <Button loading={…}>, ` +
        'which carries aria-busy for free:\n  ' + [...new Set(hand)].join('\n  '),
    ).toBeLessThanOrEqual(24)
  })
})
