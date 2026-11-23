import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Toggle } from './Toggle'

// ── A 20px switch is not a 24px target ─────────────────────────────────────────────────
//
// The button used to BE the track, so an `sm` switch was a **36×20** target. axe reported
// `[serious] target-size: Target has insufficient size (36px by 20px, should be at least 24px by
// 24px)` on five at once on the settings hub, in both themes.
//
// 🪤 SC 2.5.8's UNDERSIZED-TARGET EXCEPTION DOES NOT RESCUE IT, and it is easy to measure wrongly.
// Switch-to-switch centre distances are **34–107px**, which looks like ample spacing — I checked
// that first and it said "exception applies". It does not: the exception requires the 24px circle to
// clear *another target*, and on the settings hub each switch sits inside a **full-card nav overlay**
// (`bento.tsx` renders `<button aria-label="Open … settings" class="absolute inset-0">`). The circle
// is inside that button by construction. **A control embedded in a larger clickable surface can never
// use the spacing exception — measure against the ENCLOSING target, not the sibling.**
//
// The fix keeps the design and moves only the hit box: the button becomes a transparent 24px-tall
// wrapper, the 20px track moves inside it, and `-my-0.5` returns the extra 4px to the layout.
// Measured on the built app, before → after: hit box **36×20 → 36×24**, painted track **36×20 →
// 36×20**, switch centre-Y **497.8 → 497.8**, row height **130 → 130**.

describe('the sm switch is a reachable target', () => {
  it('gives the button a 24px-tall hit box', () => {
    render(<Toggle size="sm" on={false} onChange={vi.fn()} label="Restore sessions" />)
    expect(screen.getByRole('switch').className).toMatch(/\bh-6\b/)
  })

  it('returns the extra height to the layout, so nothing moves', () => {
    // Without this the switch would grow 4px taller and every row containing one would reflow.
    render(<Toggle size="sm" on={false} onChange={vi.fn()} label="Restore sessions" />)
    expect(screen.getByRole('switch').className).toMatch(/-my-0\.5/)
  })

  it('keeps the 20px track as the painted visual, inside the button', () => {
    render(<Toggle size="sm" on={false} onChange={vi.fn()} label="Restore sessions" />)
    const track = screen.getByRole('switch').querySelector('span')!
    expect(track.className, 'the sm track stays h-5 w-9 — the fix is the hit box, not the design').toMatch(/h-5 w-9/)
  })

  it('does not pad md — it is already 24px and needs no correction', () => {
    render(<Toggle on={false} onChange={vi.fn()} label="Deliver notifications" />)
    const btn = screen.getByRole('switch')
    expect(btn.className).toMatch(/\bh-6\b/)
    expect(btn.className, 'md must not gain a negative margin it does not need').not.toMatch(/-my-0\.5/)
    expect(btn.querySelector('span')!.className).toMatch(/h-6 w-10/)
  })

  it('still toggles, and still announces its state and name', () => {
    const onChange = vi.fn()
    render(<Toggle size="sm" on onChange={onChange} label="Send on Enter" />)
    const btn = screen.getByRole('switch', { name: 'Send on Enter' })
    expect(btn.getAttribute('aria-checked')).toBe('true')
    btn.click()
    expect(onChange).toHaveBeenCalledWith(false)
  })

  it('leaves the read-only indicator alone — it is not a target', () => {
    // SC 2.5.8 governs pointer TARGETS. A display-only switch is not clickable, so padding it
    // would add nothing and would change layout for no reason.
    render(<Toggle size="sm" on readOnly label="Enabled" />)
    const el = screen.getByRole('switch')
    expect(el.tagName).toBe('SPAN')
    expect(el.className).toMatch(/h-5 w-9/)
    expect(el.className).not.toMatch(/-my-0\.5/)
  })
})

// ── Every switch is a 44 px target on a phone ───────────────────────────────────────────────────
//
// Below the large breakpoint a switch's hit box was 24 px tall, and the UI quality matrix held 102
// switches on 31 surfaces under 44 px at 320 to 768 px in both themes. One switch asked for the
// phone's box (Settings › Chat's Keep my question in view); the rest waited to move at once. Now
// every switch that takes a click has it at the compact widths: the track is drawn the same, and
// only the transparent box around it grows. jsdom has no layout, so the classes that set the box
// are pinned; the matrix measures it.

const PHONE_BOX = ['max-lg:min-h-11', 'max-lg:min-w-11']
const classesOf = (el: Element) => el.className.toString().split(/\s+/)

describe('every switch is a 44 px target on a phone', () => {
  it('🔴 sm and md alike, with nothing to ask for it', () => {
    render(<><Toggle size="sm" on={false} onChange={vi.fn()} label="Restore sessions" /><Toggle on onChange={vi.fn()} label="Send on Enter" /></>)
    for (const name of ['Restore sessions', 'Send on Enter']) {
      expect(classesOf(screen.getByRole('switch', { name })), name).toEqual(expect.arrayContaining(PHONE_BOX))
    }
  })

  it('🔴 a switch that says why it is unavailable stays a target, as it stays reachable', () => {
    render(<Toggle on={false} onChange={vi.fn()} label="Require a 2FA code" disabled disabledReason="Set a password first" />)
    expect(classesOf(screen.getByRole('switch', { name: 'Require a 2FA code' }))).toEqual(expect.arrayContaining(PHONE_BOX))
  })

  it('a read-only or decorative switch is no target, so its box does not grow', () => {
    render(<><Toggle on readOnly label="Enabled" /><button type="button" aria-label="Turn sync on"><Toggle on={false} decorative /></button></>)
    expect(classesOf(screen.getByRole('switch', { name: 'Enabled' })).some((c) => c.startsWith('max-lg:min-'))).toBe(false)
    const inner = screen.getByRole('button', { name: 'Turn sync on' }).querySelector('span')!
    expect(classesOf(inner).some((c) => c.startsWith('max-lg:min-'))).toBe(false)
  })
})
