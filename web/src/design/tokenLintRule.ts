// ── THE token-lint rule ────────────────────────────────────────────────────
// Extracted from tokenLint.test.ts so an APP bundle can be linted by the SAME
// rule the host frontend is (APE-4). The canonical patterns live in
// src/personalclaw/apps/token_lint_rules.json — packaged, so the apps-repo CI
// gets them from an installed wheel — and tokenLintRuleParity.test.ts fails if
// the literals below drift from that file. Editing one without the other is
// exactly the two-dialect defect the quality atom exists to prevent.
//
// This directory is EXEMPT from token-lint itself (EXEMPT_DIRS = ['design/']),
// which is why a hex-shaped pattern may appear here: this file DEFINES the rule.

/** A raw color hex in a style/className context. The HARD rule — hardcoded
 *  colors bypass the theme/scheme system and must reach 0. */
export const HEX = /#[0-9a-fA-F]{3,8}\b/

/** A raw px literal INSIDE an inline style object, where a design token genuinely
 *  applies (font-size / spacing / radius). Arbitrary Tailwind values
 *  (min-w-[200px], border-l-[3px]) are pragmatic one-off layout dims — not flagged. */
export const RAW_PX = /style=\{\{[^}]*?\b\d+px\b/

/** Legitimate inline-px contexts a design token doesn't cover — not violations:
 *  CSS grid track sizing (minmax/repeat), border/outline hairline WIDTHS (the color
 *  there is already a token), and computed pixel heights/widths (Math.min(...)). */
export const PX_OK_CONTEXT = /minmax\(|repeat\(|\bmin\(|\bmax\(|\bclamp\(|\b(border|outline)(-[a-z]+)?:\s*[^;}]*\d+px|border[A-Z][a-zA-Z]*:\s*[`'"]?\s*\$?\{?[^}]*\d+px|Math\.(min|max)\(/

/** A px inside a calc() that already references a token (e.g.
 *  calc(var(--content-width) + 160px)) is a legitimate token+offset. */
export const CALC_WITH_TOKEN = /calc\([^)]*var\(/

/** Line-level verdict, shared by the host lint and the app-bundle lint. Returns
 *  the violation kinds found on this line (empty = clean). Comment-only lines are
 *  skipped by the caller: design rationale often cites hex/px in prose. */
export function lineViolations(line: string): ('hex' | 'px')[] {
  const out: ('hex' | 'px')[] = []
  if (HEX.test(line)) out.push('hex')
  if (RAW_PX.test(line) && !CALC_WITH_TOKEN.test(line) && !PX_OK_CONTEXT.test(line)) out.push('px')
  return out
}
