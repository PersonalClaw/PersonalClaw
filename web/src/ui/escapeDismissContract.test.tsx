import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A dismissible overlay needs a KEYBOARD way out ──────────────────────────────
//
// Cycle 38 covered the `aria-modal` dialogs. This is the rest of the family: every overlay a user
// can OPEN and must be able to CLOSE. A mouse user always has the click-away scrim; a keyboard user
// has only Escape, so an overlay whose sole dismissal is a scrim tap strands them.
//
// Census — Escape handling across every overlay/menu surface (39 sites bind it, so the app's
// convention is strong and the outliers are genuine drift, not a missing convention):
//
//   ui/Popover · ui/Combobox · ui/ProjectPicker · ui/NotificationBell · ui/HeaderActions ·
//   ui/FeedbackThumbs · ui/motion/ContextMenu · ui/SidePanel · ui/Modal · ui/dialog/DialogShell ·
//   ui/composer/{Slash,Mention}Menu · CommandPalette · FindBar · … all bind Escape.      ✓
//
// SIX outliers, all fixed here — every one had a click-away scrim and NO keyboard exit:
//
//   ui/DegradedChip.tsx              role=dialog popover (also returns focus to the chip)
//   app/App.tsx NavRail drawer       the overlay nav
//   pages/chat/SessionSkillsReview   a review sheet
//   ui/content/ContentSurface        the export menu
//   ui/widget/WidgetFrame            expanded-widget backdrop
//   ui/widget/ReactWidgetFrame       ditto (the React-host twin)
//
// After: 10 scrim-bearing surfaces tree-wide, 0 without Escape.
//
// `ui/Popover` documents the canonical contract and `NotificationBell` already honoured it (verified
// on the live DOM: Escape closes AND focus returns to the trigger). So these two were outliers
// against a working sibling.
//
// Measured on the live DOM.
//
//   DegradedChip (1440px)   BEFORE  panel still open after Escape; its full-viewport
//                                   `fixed inset-0` scrim STILL UP — which also swallowed pointer
//                                   events for the whole app until a mouse click landed on it.
//                           AFTER   panel closed, scrim count 0, focus back on the "2 degraded" chip.
//
//   NavRail drawer (700px)  BEFORE  aria-hidden="false" after Escape — no keyboard way out.
//                           AFTER   aria-hidden="true".
//
// 🪤 The drawer nearly got ruled a DISTINCTION on the reasoning "it is mobile-only, and a touch
// device has no Escape key". That was wrong: `useIsMobile` is a `max-width: 768px` MEDIA QUERY, so a
// narrow DESKTOP window gets the drawer *and* a real keyboard. Measuring at 700px is what caught it.
// A "mobile-only" surface is not automatically keyboard-exempt.
//
// Both fixes use `stopPropagation`, as Popover explains: without it one Escape press bubbles to
// other document-level handlers and closes two layers at once.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

describe('DegradedChip is dismissible from the keyboard', () => {
  const src = read('ui/DegradedChip.tsx')

  it('Escape closes it and returns focus to the chip', () => {
    expect(src).toMatch(/e\.key !== 'Escape'/)
    expect(src).toMatch(/setOpen\(false\)/)
    expect(src).toMatch(/triggerRef\.current\?\.focus\(\)/)
  })

  it('the trigger carries the ref that focus returns to', () => {
    // Without this the handler would call `.focus()` on a null ref — the fix would look complete
    // and drop focus to <body>, which is the shape `ui/Popover`'s comment warns about.
    expect(src).toMatch(/<button ref=\{triggerRef\}/)
  })

  it('Escape is consumed so one press does not close two layers', () => {
    expect(src).toMatch(/e\.stopPropagation\(\)/)
  })

  it('the listener is scoped to the open state', () => {
    // A document listener bound unconditionally would fire for every Escape in the app.
    expect(src).toMatch(/if \(!open\) return/)
  })
})

describe('the NavRail overlay drawer is dismissible from the keyboard', () => {
  const src = read('app/App.tsx')

  it('Escape closes the drawer', () => {
    expect(src).toMatch(/if \(!mobileNavOpen\) return/)
    expect(src).toMatch(/setMobileNavOpen\(false\)/)
    expect(src).toMatch(/e\.stopPropagation\(\)/)
  })

  it('the drawer is reachable at desktop widths, which is why it needs Escape', () => {
    // `useIsMobile` is a media query, not a touch test — pinned here because the "mobile-only, so
    // no keyboard" reasoning is exactly what almost got this ruled a distinction.
    expect(read('app/useIsMobile.ts')).toMatch(/max-width: 768px/)
    expect(/navigator\.maxTouchPoints|ontouchstart/.test(read('app/useIsMobile.ts'))).toBe(false)
  })
})

describe('the rail: an overlay with a click-away scrim also binds Escape', () => {
  const files = walk(SRC).map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))

  // The marker of a DISMISSIBLE overlay is a full-viewport `fixed inset-0` layer that CLOSES ON
  // CLICK. `fixed inset-0` alone over-matches badly — measured: it also flagged `Onboarding.tsx`
  // (a full-screen PAGE with no onClick, not dismissible at all) and `UpdateProgressOverlay`
  // (whose inset-0 is the CONTAINER; it is a blocking progress dialog and correctly has no
  // click-away). Requiring the click handler on the same element is what makes this decidable.
  // `fixed` OR `absolute` inset-0: `ui/Modal` puts its scrim `absolute inset-0` INSIDE a fixed
  // container, so a fixed-only marker misses the canonical example — which is exactly what the
  // vacuity assertion below caught when it demanded Modal be in scope.
  const SCRIM = /className="[^"]*\b(?:fixed|absolute)\b[^"]*\binset-0\b[^"]*"[^>]{0,140}onClick=/
  const withScrim = files.filter((f) => SCRIM.test(f.src))

  it('every file with a click-away scrim handles Escape', () => {
    const offenders = withScrim.filter((f) => !/'Escape'/.test(f.src)).map((f) => f.rel)
    expect(
      offenders,
      `A scrim is a MOUSE dismissal; without an Escape handler a keyboard user cannot close the ` +
        `overlay:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the scrim-bearing files', () => {
    // Two cycles ago a rail matched nothing and reported a clean sweep, because
    // `expect(offenders).toEqual([])` cannot tell "nothing is broken" from "my matcher is broken".
    expect(withScrim.length, 'the scanner must find the scrim-bearing overlays').toBeGreaterThan(4)
    const rels = withScrim.map((f) => f.rel)
    expect(rels).toContain('ui/DegradedChip.tsx')
    expect(rels).toContain('ui/Modal.tsx')
    // And it must still FLAG the shape: a scrim with no Escape handler anywhere in the file.
    // Both scrim shapes must be recognised — `fixed` (a portaled popover) and `absolute` (a scrim
    // inside an already-fixed container, which is what Modal does).
    for (const cls of ['fixed inset-0 z-40', 'absolute inset-0 bg-canvas/70']) {
      expect(SCRIM.test(`<div className="${cls}" onClick={close} />`), cls).toBe(true)
    }
    // A full-screen page with NO click handler is not a dismissible overlay and must not match.
    expect(SCRIM.test('<div className="fixed inset-0 z-[100] overflow-hidden" style={{}}>')).toBe(false)
    const sample = { rel: 'x.tsx', src: '<div className="fixed inset-0 z-40" onClick={close} />' }
    expect(SCRIM.test(sample.src) && !/'Escape'/.test(sample.src)).toBe(true)
  })
})
