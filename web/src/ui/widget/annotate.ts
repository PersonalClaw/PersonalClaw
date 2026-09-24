/** Annotate mode — element-anchored corrections (AMBIENT-SURFACES §4).
 *
 *  Describing a visual change in prose is the slowest loop in design iteration:
 *  "the second card's heading" costs the agent a guess. Annotate mode replaces the
 *  guess with an anchor. The user toggles annotate, clicks the elements that are
 *  wrong, writes one short note each, and the N clicks compose into ONE correction
 *  directive that names every element by selector.
 *
 *  Two halves, deliberately split:
 *   · the CHILD derives the anchor, because only the frame can see the clicked
 *     element (selector priority + context live in widgetSrcdoc's edit-mode script);
 *   · the PARENT composes the directive, here, because only the host knows where a
 *     correction goes.
 *
 *  The correction is **data with provenance, not executed UI**. Nothing here mutates
 *  the artifact: the receiving agent regenerates it. Every field arrives from inside
 *  a sandboxed frame and is therefore untrusted — `readAnnotation` is the only door.
 */

/** One clicked element, as the child reported it. */
export interface WidgetAnnotation {
  /** CSS selector, derived with `data-testid` → `id` → class chain → `nth-child`. */
  selector: string
  /** Lowercased tag name — cheap orientation when the selector is an nth-child path. */
  tag: string
  /** The element's markup, capped. */
  outerHTML: string
  /** The parent element, named the same way — "which card was this in?". */
  parentContext: string
  /** The user's freeform note. Empty until they type one. */
  note: string
}

/** Caps. The directive becomes a conversation turn, so an artifact with a 200 KiB
 *  <table> must not be able to spend the whole budget on one anchor. */
const MAX_SELECTOR = 240
const MAX_OUTER_HTML = 400
const MAX_CONTEXT = 120
const MAX_NOTE = 400
/** More anchors than a single correction can usefully carry. */
export const MAX_ANNOTATIONS = 12

function clean(v: unknown, max: number): string {
  if (typeof v !== 'string') return ''
  // Collapse newlines: the directive is a line-oriented block, and a smuggled
  // newline in a selector could forge a second anchor line.
  return v.replace(/\s+/g, ' ').trim().slice(0, max)
}

/** Validate one child-reported annotation. Returns null when the anchor is unusable
 *  — an annotation with no selector anchors nothing and would tell the agent less
 *  than the user's prose already does. */
export function readAnnotation(raw: unknown): WidgetAnnotation | null {
  if (!raw || typeof raw !== 'object') return null
  const d = raw as Record<string, unknown>
  const selector = clean(d.selector, MAX_SELECTOR)
  if (!selector) return null
  return {
    selector,
    tag: clean(d.tag, 24).toLowerCase(),
    outerHTML: clean(d.outerHTML, MAX_OUTER_HTML),
    parentContext: clean(d.parentContext, MAX_CONTEXT),
    note: '',
  }
}

/** The fenced body of a correction directive.
 *
 *  ONE block for N anchors — the whole point of annotate mode is that the agent
 *  gets a single instruction it can act on in one pass, not N chat turns. The
 *  fence name is stable (`corrections`) because an agent branches on it. */
export function composeCorrectionBody(annotations: WidgetAnnotation[]): string {
  const used = annotations.slice(0, MAX_ANNOTATIONS)
  const lines: string[] = ['```corrections']
  used.forEach((a, i) => {
    lines.push(`${i + 1}. selector: ${a.selector}`)
    if (a.parentContext) lines.push(`   within: ${a.parentContext}`)
    if (a.outerHTML) lines.push(`   element: ${a.outerHTML}`)
    lines.push(`   change: ${clean(a.note, MAX_NOTE) || '(no note — the user marked this element without describing the change)'}`)
  })
  lines.push('```')
  return lines.join('\n')
}

/** The full directive: a one-line summary the transcript reads well, then the
 *  fenced anchors. The `[UI] ` prefix and the C32 "refresh in place" suffix are
 *  added by the caller through the widget bridge's shared composer, so a
 *  correction obeys the SAME clip and living-view rules a widget action does. */
export function composeCorrectionDirective(annotations: WidgetAnnotation[]): string {
  const n = Math.min(annotations.length, MAX_ANNOTATIONS)
  const head = `correction: ${n} element${n === 1 ? '' : 's'} marked — regenerate the artifact applying every change below, and change nothing else`
  return `${head}\n${composeCorrectionBody(annotations)}`
}
