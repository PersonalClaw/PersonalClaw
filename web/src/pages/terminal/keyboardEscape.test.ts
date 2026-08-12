import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { escapeGate, ESC_ESC_MS } from './escapeGate'

// ── The terminal was a keyboard trap, on every route ────────────────────────────────────
//
// Measured on a LIVE PTY (Chromium, 1440×900) with focus on `.xterm-helper-textarea`, on
// `#/terminal` AND in the ⌘` drawer — the same `TerminalView`, so the cockpit's bottom
// terminal was the third mount:
//
//                     BEFORE                          AFTER
//   Tab               focus unchanged (trapped)       passes the terminal by
//   Shift+Tab         focus unchanged (trapped)       returns to the group
//   Escape            focus unchanged (trapped)       forwarded to the shell (unchanged)
//   Esc Esc           — (no such thing)               focus → the "Terminal session" group
//   Enter (on group)  —                               steps into the PTY
//   on-screen advice  none                            "Enter to type here · Esc Esc to leave"
//
// WCAG 2.1.2 No Keyboard Trap is level **A**, and its only exemption is a way out that is
// ADVISED to the user — there was neither a way out nor advice.
//
// 🪤 WHY NO TOOL HERE FOUND IT. `ux-audit` walks focusable controls and checks each one has a
// name and a visible ring; xterm's helper textarea passes both (`aria-label="Terminal input"`,
// ring on the host). axe has no rule for "focus can enter but not leave" — a trap is only
// observable by PRESSING Tab and reading `document.activeElement` afterwards, which no
// static or single-snapshot pass does.
//
// 🪤 THE HALF-FIX THAT MEASURED AS A FIX. Releasing focus to the container was not enough: the
// container precedes the PTY in DOM order, so the next Tab walked straight back into the
// terminal and the two looped forever. Fixing a trap means driving the WHOLE round trip —
// in, out, and PAST — not just the key that releases.

describe('escapeGate — which keydown releases focus', () => {
  it('forwards a lone Escape to the shell (vim, readline)', () => {
    const d = escapeGate('Escape', 1000, 0)
    expect(d.forward).toBe(true)
    expect(d.release).toBe(false)
    expect(d.lastEscAt).toBe(1000)
  })

  it('releases on a second Escape inside the window, and swallows it', () => {
    const first = escapeGate('Escape', 1000, 0)
    const second = escapeGate('Escape', 1000 + ESC_ESC_MS - 1, first.lastEscAt)
    expect(second.release).toBe(true)
    // The releasing key must NOT also reach the PTY, or leaving the terminal would type into it.
    expect(second.forward).toBe(false)
    expect(second.lastEscAt).toBe(0)
  })

  it('forwards BOTH when the two Escapes are far apart', () => {
    const first = escapeGate('Escape', 1000, 0)
    const later = escapeGate('Escape', 1000 + ESC_ESC_MS, first.lastEscAt)
    expect(later.release).toBe(false)
    expect(later.forward).toBe(true)
    // …and it re-arms, so Esc (pause) Esc Esc still gets you out.
    expect(escapeGate('Escape', 1000 + ESC_ESC_MS + 10, later.lastEscAt).release).toBe(true)
  })

  it('a key between two Escapes disarms the release', () => {
    // `Esc k` is a real vim sequence. Its `k` must not leave a release armed for an Escape
    // pressed a minute later, which would eject the user out of nowhere.
    const first = escapeGate('Escape', 1000, 0)
    const k = escapeGate('k', 1010, first.lastEscAt)
    expect(k.forward).toBe(true)
    expect(k.lastEscAt).toBe(0)
    expect(escapeGate('Escape', 1020, k.lastEscAt).release).toBe(false)
  })

  it('never releases on Tab — completion still belongs to the shell', () => {
    for (const key of ['Tab', 'Enter', 'ArrowUp', 'c']) {
      const d = escapeGate(key, 500, 400)
      expect(d.forward, `${key} must reach the PTY`).toBe(true)
      expect(d.release, `${key} must not release focus`).toBe(false)
    }
  })
})

describe('TerminalView wires the escape to the DOM', () => {
  // The rest of the contract lives in DOM plumbing that a jsdom mount cannot exercise
  // (xterm needs a canvas, a WebSocket and a real PTY), so it is asserted at the source.
  // Every line here was verified in a browser first; these keep it from silently regressing.
  const src = readFileSync(join(process.cwd(), 'src/pages/terminal/TerminalView.tsx'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('hands the key decision to escapeGate rather than re-deriving it', () => {
    expect(src).toMatch(/attachCustomKeyEventHandler/)
    expect(src).toMatch(/escapeGate\(e\.key, e\.timeStamp, lastEsc\)/)
  })

  it('takes xterm out of the tab order so Tab can pass the terminal by', () => {
    expect(src).toMatch(/\.xterm-helper-textarea/)
    expect(src).toMatch(/setAttribute\('tabindex', '-1'\)/)
  })

  it('gives the terminal ONE labelled tab stop that Enter steps into', () => {
    expect(src).toMatch(/tabIndex=\{0\}/)
    expect(src).toMatch(/role="group"/)
    expect(src).toMatch(/aria-label="Terminal session"/)
    expect(src).toMatch(/e\.key === 'Enter' && e\.target === e\.currentTarget/)
  })

  it('rings on plain :focus, because the release focuses it programmatically', () => {
    // `:focus-visible` does not match a scripted `.focus()`, so a focus-visible ring would
    // never paint on the way out — measured in a browser, where the ring is the 4th of five
    // box-shadow layers (`… 0px 0px 0px 2px inset`).
    expect(src).toMatch(/focus:ring-2 focus:ring-inset focus:ring-primary\/50/)
    expect(src).not.toMatch(/focus-visible:ring/)
  })

  it('advises the way out, which is what 2.1.2 actually requires', () => {
    expect(src).toMatch(/Enter to type here · Esc Esc to leave/)
    // Announced on arrival too — focus lands on xterm's textarea, so it carries the id.
    expect(src.match(/aria-describedby/g) ?? [], 'both the group and the PTY point at the hint').toHaveLength(2)
    // The hint fades once read, and a faded aria-describedby target must stay readable.
    expect(src).not.toMatch(/aria-hidden=\{hintRead\}/)
  })
})
