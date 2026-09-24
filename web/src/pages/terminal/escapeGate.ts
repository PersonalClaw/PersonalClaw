// ── The terminal's keyboard way out, as a pure decision ─────────────────────────────────
//
// xterm forwards every key to the PTY, Tab included, so a keyboard user who stepped into a
// live terminal could not step back out by any key — WCAG 2.1.2 No Keyboard Trap, level A,
// measured on both the terminal page and the ⌘` drawer.
//
// Which key to hand back is the whole design question, because each candidate costs the
// shell something real: Tab is completion, Escape is vim and readline, Shift+Tab is zsh's
// reverse-menu-complete. A DOUBLE Escape spends the least: the first Escape is forwarded
// untouched, and only a second one inside the window releases focus. Esc-Esc has no standard
// meaning in bash, zsh or vim (there the second is a no-op in normal mode).
//
// It lives here as a reducer rather than inline in the view because the interesting behaviour
// is entirely "which keydown releases", and that deserves a test that does not need a canvas,
// a WebSocket and a real PTY to run.

/** How long after an Escape a second one still means "let me out" rather than two independent
 *  Escapes both headed for the shell. */
export const ESC_ESC_MS = 600

export interface EscapeDecision {
  /** Whether xterm should forward this key to the PTY (its handler returns this verbatim). */
  forward: boolean
  /** The new "last Escape at" timestamp to carry into the next call; 0 means "no Escape pending". */
  lastEscAt: number
  /** Whether focus should be released to the terminal's container. */
  release: boolean
}

/**
 * Decide what one keydown means.
 *
 * @param key        `KeyboardEvent.key`
 * @param at         `KeyboardEvent.timeStamp` (monotonic — do not pass a wall clock)
 * @param lastEscAt  what the previous call returned
 */
export function escapeGate(key: string, at: number, lastEscAt: number, windowMs = ESC_ESC_MS): EscapeDecision {
  // Any other key ends a pending Escape: `Esc k` in vim is a real sequence, and its `k` must
  // not leave an armed release behind for an Escape typed a minute later.
  if (key !== 'Escape') return { forward: true, lastEscAt: 0, release: false }
  if (lastEscAt && at - lastEscAt < windowMs) return { forward: false, lastEscAt: 0, release: true }
  return { forward: true, lastEscAt: at, release: false }
}
