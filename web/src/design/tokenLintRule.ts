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
 *  calc(var(--spacing-l) + 1px)) is a legitimate token+offset. */
export const CALC_WITH_TOKEN = /calc\([^)]*var\(/

/** Line-level verdict, shared by the host lint and the app-bundle lint. Returns
 *  the violation kinds found on this line (empty = clean). Feed it the CODE-only
 *  text from `stripComments` — design rationale legitimately cites hex/px in prose. */
export function lineViolations(line: string): ('hex' | 'px')[] {
  const out: ('hex' | 'px')[] = []
  if (HEX.test(line)) out.push('hex')
  if (RAW_PX.test(line) && !CALC_WITH_TOKEN.test(line) && !PX_OK_CONTEXT.test(line)) out.push('px')
  return out
}

// ── Comment state, tracked across lines (#3337) ─────────────────────────────
// Both consumers used to decide "is this a comment?" from the line's OWN first
// characters. An INTERIOR line of a multi-line `{/* … */}` block carries no marker,
// so it was linted as code — and because every decimal digit is a hex digit, HEX
// matches any 3-to-8-digit issue reference (`#532`, `#1783`). Citing the issue
// number in a comment, the convention this program runs on, therefore reddened the
// rail. Tightening HEX to 3/4/6/8 digits does not help: `#1783` is four.
//
// So the BLOCK state is tracked across lines instead. Three scoping decisions, each
// measured against the real corpus rather than assumed:
//
// 1. STRING-AWARE, and `//` beats `/*`. A `/*` inside a string or a line comment must
//    not open a block, because a tracker that opens one there leaves state stuck open
//    and silently stops catching real raw hexes for the rest of the file — the ONLY
//    way this change could weaken the rail instead of fixing it. All three shapes ship
//    today:
//      - `ui/widget/editMode.ts`  — `'/*EDITMODE-BEGIN*/'`, a `/*` inside a string
//      - `ui/Toggle.doc.ts`       — `'… all 34 #/settings/* subpages …'`, likewise
//      - `app/swPolicy.ts`        — ``// THE RULE: `/api/*` responses …``, `/*` in a `//`
//
// 2. Template literals are a LINE-LOCAL quote, and only when the line CLOSES them (#3347).
//    A backtick opens quote state only if another backtick follows on the same line, so
//    `` `${proto}//${host}` `` is content while an UNPAIRED backtick stays an ordinary
//    character — which is what keeps the three regex literals that ship today working:
//    `pages/chat/parseAssistant.ts` (`[\s(`'"]`), `pages/tools/ToolOutput.tsx` and
//    `ui/content/renderers.tsx` (both ```` /^```/m ````). Their trailing `//` comments
//    stay stripped, and un-stripping one would re-create #3337 (every decimal digit is a
//    hex digit, so `// see #1783` would read as a raw hex). Pairing on the line needs no
//    parser context and cannot survive a newline, so state never leaks to EOF — the way
//    tracking template literals as a third state broke before. Multi-line template CONTENT
//    therefore stays linted: a raw hex in a css-in-template is still caught. Measured over
//    all 665 corpus files: 7 lines of stripped code change, violation set and every EOF
//    state unchanged.
//
// 3. `://` is a scheme separator, not a comment (#3347). Only the IMMEDIATELY preceding
//    character counts, so `case 'x': // note` is still prose, while a URL in JSX text —
//    unquoted, so rule 1 cannot help it — no longer truncates the line and hides the code
//    after it. Three corpus lines carry this shape today (`appSdk.tsx`, `useChatSocket.ts`,
//    `TerminalView.tsx`).
//
// A line whose first non-space characters are `//` is prose in any non-block state.
// That covers the `//` comments inside the embedded-JS templates of `widgetSrcdoc.ts`
// and `ToolOutput.tsx`, which is what the old shape-based skip was doing for them.
//
// Not handled, deliberately — a `/*` in JSX TEXT (`<p>a /* b</p>`) or in a regex
// character class (`/[/*]/`). The corpus has neither, both need a real parser, and both
// fail STRICT (state opens, code is dropped), which `stripComments` reports through
// `endState` — see the EOF control in tokenLint.test.ts, which asserts that no source
// file ends outside `code`, and `token_lint_bundle`, which refuses such a file by name.

/** Lexer state that can survive a newline. Single/double-quoted strings cannot (JS
 *  forbids a bare newline inside one); a template literal can, but is deliberately
 *  line-local anyway (decision 2) so its state can never leak to EOF. */
export type SourceState = 'code' | 'block'

export interface StrippedSource {
  /** One entry per input line, in order: that line with comment text removed.
   *  Same length as `text.split('\n')`, so an index is still a line number. */
  code: string[]
  /** The state the scanner ended in. `'block'` means a block comment never closed —
   *  a stuck-open tracker reads as "the rest of the file is clean", which is exactly
   *  the weakening to catch, so callers must assert on this. */
  endState: SourceState
}

/** Split `text` into lines and blank out comment text, carrying block-comment state
 *  across newlines. String CONTENT is kept — strings are not comments, and a raw
 *  colour hex is nearly always string content. */
export function stripComments(text: string): StrippedSource {
  let state: SourceState = 'code'
  const code: string[] = []
  for (const line of text.split('\n')) {
    const kept: string[] = []
    let quote = '' // '' | "'" | '"' | '`' — line-local by construction
    let i = 0
    while (i < line.length) {
      const c = line[i]
      if (state === 'block') {
        if (c === '*' && line[i + 1] === '/') { state = 'code'; i += 2 } else { i += 1 }
        continue
      }
      if (quote) {
        kept.push(c)
        if (c === '\\') { if (i + 1 < line.length) kept.push(line[i + 1]); i += 2; continue }
        if (c === quote) quote = ''
        i += 1
        continue
      }
      if (c === "'" || c === '"' || (c === '`' && line.indexOf('`', i + 1) !== -1)) { quote = c; kept.push(c); i += 1; continue }
      if (c === '/' && line[i + 1] === '/') {
        if (i > 0 && line[i - 1] === ':') { kept.push(c); i += 1; continue } // `https://` — a scheme, not a comment
        break // the rest of the line is a comment
      }
      if (c === '/' && line[i + 1] === '*') { state = 'block'; i += 2; continue }
      kept.push(c)
      i += 1
    }
    code.push(kept.join(''))
  }
  return { code, endState: state }
}
