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
// So the BLOCK state is tracked across lines instead. Four scoping decisions, each
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
// 4. A `/*` is an OPENER only when what precedes it could not be an operand (#3347).
//    Same one-character look-behind as decision 3: if the immediately preceding character
//    is ASCII-alphanumeric or `[`, the `/` is division, a glob, or a regex character class.
//    This is the shape `endState` CANNOT see, because it closes: a stray `/*` in JSX text
//    (`<p>3/*off</p>`) or in a class (`/[/*]/`) used to open a block that the next
//    UNRELATED `*/` closed, so `endState` came back to `'code'`, nothing was refused, and
//    every violation inside the blanked span was silently dropped. Measured over the 1559
//    `web/src/**/*.{ts,tsx}` files: 0 verdict, 0 `endState` and 0 stripped-code diffs, because
//    all 54 of the corpus's 6761 alnum/`[`-preceded `/*` occurrences are globs inside a
//    string or behind `//` prose (`/api/*`, `#/settings/*`, `image/*`) where decisions 1
//    and 2 already answer "not a comment". A start-of-file `/*` still opens, so #3337 stays
//    fixed.
//
// Still NOT handled, and the boundary is exactly one character wide: a `/*` preceded by a
// SPACE in JSX text (`<p>use /* as a wildcard</p>`) is indistinguishable from a real opener
// without parser context, so it still opens a block. Decision 4 fixes the alnum/`[`-preceded
// subset, NOT "a `/*` in JSX text" as a category — both halves of that boundary are pinned in
// token_lint_comment_cases.json.
//
// What that residue costs is CONDITIONAL, and the condition is the load-bearing half: with
// nothing lower down to close the span the scan ends in `'block'`, which `token_lint_bundle`
// refuses by name — loud. But an ordinary block comment lower down CLOSES it, so `endState`
// returns to `'code'` and the same bundle comes back `{}`: a clean verdict keeping
// `designSystem: "v2"` while every violation in the blanked span is dropped in silence.
// Measured both ways through the real bundle gate and recorded as the one `gap_*` case in
// token_lint_comment_cases.json — a hole in the corpus is cheaper than the same hole found
// in a badged bundle.
//
// A line whose first non-space characters are `//` is prose in any non-block state.
// That covers the `//` comments inside the embedded-JS templates of `widgetSrcdoc.ts`
// and `ToolOutput.tsx`, which is what the old shape-based skip was doing for them.
//
// What `endState` does and does not cover: it reports the state the scanner ENDED in, which
// is NOT the claim "the tracker read every line correctly". A block that opens wrongly and
// then closes ends in `'code'` and is invisible to it — which is why decision 4 is a
// look-behind here rather than a refusal downstream. `endState` catches only the
// never-closed residue; the EOF control in tokenLint.test.ts asserts no source file ends
// outside `code`, and `token_lint_bundle` refuses such a file by name.

/** A `/*` whose IMMEDIATELY preceding character matches this does not open a block
 *  comment (decision 4). ASCII-only on purpose — the Python twin's class is ASCII, and a
 *  Unicode-aware test on either side would be a silent parity break. */
const NOT_AN_OPENER_AFTER = /[0-9A-Za-z[]/

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
      if (c === '/' && line[i + 1] === '*') {
        // `3/*off`, `/[/*]/` — an operator or a regex class, not a comment opener (#3347)
        if (i > 0 && NOT_AN_OPENER_AFTER.test(line[i - 1])) { kept.push(c); i += 1; continue }
        state = 'block'; i += 2; continue
      }
      kept.push(c)
      i += 1
    }
    code.push(kept.join(''))
  }
  return { code, endState: state }
}
