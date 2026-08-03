import type { DialogField } from '../../ui/dialog'
import type { WorkflowInputParam } from '../../lib/api'

/** Turning a template's declared inputs into a run dialog (WF2 Slice 9b).
 *
 *  The gap this closes: every bundled template declares a REQUIRED input, and the list page's
 *  Run button passed none — so the engine correctly refused with `WF_RUN_MISSING_INPUTS` and
 *  every shipped template was unstartable from the UI. A picker that lists templates you cannot
 *  start is worse than no picker.
 *
 *  Kept as a pure module rather than inline in the page so the mapping is unit-testable: the
 *  interesting behaviour is which fields appear, in what order, with what defaults — and none of
 *  that needs a rendered dialog to assert. */

/** The dialog fields for one template's inputs.
 *
 *  Required inputs come FIRST, because a dialog that opens with three optional fields above the
 *  one that actually blocks the run reads as more work than it is. Within each group the
 *  declaration order is kept: that is the order the template's author chose, and it usually
 *  matches how a person thinks about the task.
 *
 *  A long `help` becomes a textarea's affordance rather than a tooltip nobody hovers: the help
 *  text IS the label's explanation, so it goes in the placeholder where it is visible while
 *  typing. */
export function inputFields(inputs: Record<string, WorkflowInputParam> | undefined): DialogField[] {
  const entries = Object.entries(inputs ?? {})
  const rank = (p: WorkflowInputParam) => (p.required ? 0 : 1)
  return entries
    .map(([name, param], i) => ({ name, param, i }))
    .sort((a, b) => rank(a.param) - rank(b.param) || a.i - b.i)
    .map(({ name, param }) => ({
      name,
      label: param.required ? `${labelFor(name)} *` : labelFor(name),
      placeholder: param.help || '',
      // A declared default is pre-filled, not left blank: the template's author chose it as the
      // sensible value, and making the user retype it is how a default stops being one.
      initial: param.default === undefined || param.default === null ? '' : String(param.default),
      // `boolean` has no checkbox in this dialog primitive, so it renders as text the backend
      // coerces. Deliberate over adding a control here: one shared dialog beats a bespoke form
      // per entity, and a boolean input is rare in a template.
      type: (param.help?.length ?? 0) > 90 ? ('textarea' as const) : ('text' as const),
      required: !!param.required,
    }))
}

/** `context_root` → "Context root". The declared key is a snake_case identifier; showing it raw
 *  makes a run dialog look like a config file. */
export function labelFor(name: string): string {
  const words = name.replace(/[_-]+/g, ' ').trim()
  return words ? words[0].toUpperCase() + words.slice(1) : name
}

/** Coerce the dialog's string answers into the typed values the engine expects.
 *
 *  Every dialog field is a string; a template declaring `rounds: number` would otherwise receive
 *  `"3"` and a `{{inputs.rounds}}` binding into a numeric comparison would compare a string. An
 *  UNPARSEABLE value is passed through verbatim rather than silently becoming 0 — the backend's
 *  own validation should report it, not this function guessing.
 *
 *  Empty optional answers are DROPPED, not sent as "": the engine applies the declared default
 *  when a key is absent, and sending an empty string would override that default with nothing. */
export function coerceInputs(
  answers: Record<string, string>,
  inputs: Record<string, WorkflowInputParam> | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [name, raw] of Object.entries(answers)) {
    const param = (inputs ?? {})[name]
    const text = (raw ?? '').trim()
    if (!text && !param?.required) continue
    switch (param?.type) {
      case 'number': {
        const n = Number(text)
        out[name] = Number.isFinite(n) ? n : text
        break
      }
      case 'boolean':
        out[name] = /^(true|yes|1|on)$/i.test(text)
        break
      default:
        out[name] = text
    }
  }
  return out
}

/** True when a template can be started with no dialog at all.
 *
 *  Worth checking: a template with no required inputs should start on ONE click. Opening an empty
 *  or all-optional dialog to confirm what the user already asked for is friction with no
 *  information in it. */
export function startsWithoutInput(
  inputs: Record<string, WorkflowInputParam> | undefined,
): boolean {
  return !Object.values(inputs ?? {}).some((p) => p.required)
}

/** Deep-link params, validated against a template's DECLARED inputs (TASKS-SOPS §7 R15).
 *
 *  A suggestion card links into the planner as `#/workflows/new?template=X&param=Y`. Those params
 *  arrive from a URL, which is not a trust boundary — a hand-edited or shared link can carry
 *  anything. So this is an ALLOWLIST against the template's own input schema, not a filter of
 *  known-bad keys: a denylist would silently pass whatever it had not been taught about, and the
 *  URL is exactly the place nobody would look for an injected value.
 *
 *  Returns the accepted params AND the rejected keys. Reporting rejections rather than dropping
 *  them silently is what lets a stale link explain itself: a card generated before an input was
 *  renamed should say which parameter no longer exists, not quietly start the run without it.
 */
export function validateDeepLinkParams(
  raw: Record<string, string> | URLSearchParams,
  inputs: Record<string, WorkflowInputParam> | undefined,
): { accepted: Record<string, string>; rejected: string[] } {
  const declared = new Set(Object.keys(inputs ?? {}))
  const entries: Array<[string, string]> =
    raw instanceof URLSearchParams ? Array.from(raw.entries()) : Object.entries(raw ?? {})
  const accepted: Record<string, string> = {}
  const rejected: string[] = []
  for (const [key, value] of entries) {
    // `template` names the destination, not an input — it is consumed by the router, so it is
    // neither accepted as a param nor reported as a rejection (that would be a false alarm on
    // every single link).
    if (key === 'template') continue
    if (!declared.has(key)) {
      rejected.push(key)
      continue
    }
    // An empty value is not a value. Pre-filling a field with '' would make a required input LOOK
    // answered while the engine still refuses the run — the user would see a filled form and an
    // inexplicable rejection.
    if (value === '') continue
    accepted[key] = value
  }
  return { accepted, rejected: rejected.sort() }
}

/** Whether every REQUIRED input is satisfied by these params.
 *
 *  Re-derived from the schema rather than trusted from the link, for the same reason the backend
 *  re-derives `all_filled` instead of believing the extractor: a link claiming to be complete while
 *  omitting a required input produces a run that fails engine validation AFTER the user was told it
 *  was ready to go. */
export function missingRequired(
  params: Record<string, string>,
  inputs: Record<string, WorkflowInputParam> | undefined,
): string[] {
  return Object.entries(inputs ?? {})
    .filter(([name, param]) => param.required && !params[name])
    .map(([name]) => name)
    .sort()
}
