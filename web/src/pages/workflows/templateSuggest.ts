import { KIND_TO_TEMPLATE, templateForKind } from './containerKey'

/** "Start from template": turning a plain-language intent into a template suggestion
 *  (LOOPS-EVOLUTION criterion 11).
 *
 *  The gap this closes: the templates tab lists a dozen bundled workflows by name, and a
 *  user who knows what they want to DO ("fix this bug", "research a topic") should not have
 *  to already know that a coding job is called `code-implementation` and a research job
 *  `deep-research`. The picker offering names only makes the taxonomy the user's problem.
 *
 *  A pure module rather than inline in the page, because the interesting behaviour is which
 *  kind an intent maps to and which template that kind resolves to — and neither needs a
 *  rendered dialog to assert.
 *
 *  Resolution goes intent → legacy loop-kind → template through the SAME alias table the
 *  cockpit uses (`KIND_TO_TEMPLATE`, drift-tested against the backend manifest). The plan
 *  names `code-project` as the coding suggestion; that template was deferred as a product
 *  decision and never shipped, and the alias deliberately points `code` at the
 *  `code-implementation` template that DID ship — so honouring the intent means suggesting
 *  the working template the alias resolves to, not a name no provider can start. */

/** Keyword cues per legacy loop-kind, matched case-insensitively as whole words.
 *
 *  Ordered most-specific first within a kind, but the ORDER OF KINDS here is the tie-break
 *  priority when an intent hits more than one: a "refactor the research pipeline" mentions
 *  both, and `code` winning is the right call — the verb is the action, the noun is its
 *  subject. `general` carries no cues; it is the fallback when nothing else matches, not a
 *  thing to keyword-match. */
const KIND_CUES: Array<[kind: string, cues: string[]]> = [
  ['code', ['code', 'coding', 'implement', 'refactor', 'bug', 'fix', 'feature', 'endpoint', 'api', 'function', 'test', 'debug', 'compile', 'build']],
  ['design', ['design', 'ui', 'ux', 'mockup', 'wireframe', 'layout', 'component', 'visual', 'prototype', 'figma']],
  ['research', ['research', 'investigate', 'sources', 'literature', 'survey', 'compare', 'find out', 'deep dive', 'evidence', 'cite']],
  ['goal', ['goal', 'achieve', 'pursue', 'until', 'reach', 'objective']],
]

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** Score how strongly an intent points at one kind — the count of distinct cues present.
 *
 *  Whole-word (or whole-phrase) matches only, so "scode" does not count as "code" and a
 *  passing mention inside a longer token cannot swing the suggestion. */
function scoreKind(text: string, cues: string[]): number {
  const hay = ` ${text.toLowerCase()} `
  let hits = 0
  for (const cue of cues) {
    // A multi-word cue ("find out") is matched as a substring with word boundaries around
    // the whole phrase; a single word gets \b on both sides.
    const re = new RegExp(`(^|\\W)${escapeRe(cue)}(\\W|$)`, 'i')
    if (re.test(hay)) hits += 1
  }
  return hits
}

/** The legacy loop-kind an intent most resembles, or '' when nothing matches.
 *
 *  Returns '' rather than defaulting to `general`: the caller distinguishes "matched
 *  general" (a real signal) from "matched nothing" (fall back to letting the user browse),
 *  and collapsing them here would hide that difference. A user who typed a legacy kind word
 *  directly ("start a research loop") lands on that kind through the same cue match. */
export function intentKind(text: string): string {
  const cleaned = (text ?? '').trim()
  if (!cleaned) return ''
  let best = ''
  let bestScore = 0
  for (const [kind, cues] of KIND_CUES) {
    const score = scoreKind(cleaned, cues)
    // Strictly greater keeps the first kind (highest tie-break priority) on a tie.
    if (score > bestScore) {
      best = kind
      bestScore = score
    }
  }
  return best
}

/** The template a plain-language intent suggests, filtered to what actually ships.
 *
 *  `available` is the set of template names the picker can actually start (the def list);
 *  a suggestion for a template no provider has is a dead menu entry, so a resolved template
 *  absent from `available` is dropped rather than offered. Returns '' when the intent maps
 *  to nothing startable — the caller then lets the user browse the full list instead of
 *  starting a workflow they did not choose. */
export function suggestTemplate(text: string, available: Iterable<string>): string {
  const have = available instanceof Set ? available : new Set(available)
  const kind = intentKind(text)
  if (kind) {
    const template = templateForKind(kind)
    if (template && have.has(template)) return template
  }
  return ''
}

/** Every kind→template suggestion the picker can make, for a "what can I ask for" hint.
 *
 *  Restricted to templates present in `available` for the same reason `suggestTemplate`
 *  filters: advertising a resolution the backend cannot start teaches the user a name that
 *  fails when they use it. */
export function availableSuggestions(available: Iterable<string>): Array<{ kind: string; template: string }> {
  const have = available instanceof Set ? available : new Set(available)
  return Object.keys(KIND_TO_TEMPLATE)
    .map((kind) => ({ kind, template: templateForKind(kind) }))
    .filter((s) => s.template && have.has(s.template))
}
