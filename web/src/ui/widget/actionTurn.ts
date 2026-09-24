/** The `[UI]` turn dialect — ONE prefix, ONE clip, ONE publisher (a LEAF module).
 *
 *  Carved out of `useWidgetActionBridge` deliberately, and the reason is a real cycle rather
 *  than tidiness: the bridge's non-chat fallback imports `launchChat` from `app/appSdk`, and
 *  `appSdk` imports the genui renderer → the genui component set → the genui action router,
 *  which needs THIS dialect. With these helpers still on the bridge, that closed a five-module
 *  import cycle and whichever module the bundler entered first lost its exports (measured:
 *  `registerCoreGenUiComponents is not a function` at genui import time). Nothing here reaches
 *  the SDK, so both sides can depend on it and neither depends on the other.
 *
 *  Everything a widget action becomes a conversation turn THROUGH lives here: the prefix an
 *  agent branches on, the byte cap an adversarial payload is clipped to, the CustomEvent name
 *  the host bridge listens on, and the meta a host learns about the widget it came from. */

/** The turn prefix an agent branches on. Stable — do not reword. */
const UI_PREFIX = '[UI] '

/** An adversarial widget must not be able to stuff a conversation turn, so the
 *  payload-bearing text clips here — with a marker, because a silent truncation
 *  would hand the agent a lie about what the user submitted. */
export const MAX_ACTION_TEXT_BYTES = 16 * 1024
const TRUNCATION_MARKER = '…truncated'


/** The window CustomEvent the validated wire republishes onto. One event, one
 *  vocabulary — hosts never listen to raw `message` themselves. */
export const WIDGET_ACTION_EVENT = 'ne:widget-action'


/** What the host learns about the widget an action came from. */
export interface WidgetActionMeta {
  /** The saved artifact this widget IS, when it is one (the C32 living view). */
  slug?: string
  /** The genui dual payload's `humanFriendlyMessage` (§5.4) — the short label the
   *  TRANSCRIPT shows, while `text` (the `llmFriendlyMessage`) is what the model
   *  reads. Absent on the raw-HTML widget path, which has no declared label and
   *  whose turn text is therefore also its visible text. */
  label?: string
}


/** Clip `s` to `max` UTF-8 bytes, appending the truncation marker when it bites.
 *  Byte-exact (a multi-byte payload must not sneak past a character count) and
 *  never leaves a split surrogate pair behind. */
function clipToBytes(s: string, max: number): string {
  const enc = new TextEncoder()
  if (enc.encode(s).length <= max) return s
  const budget = max - enc.encode(TRUNCATION_MARKER).length
  let lo = 0
  let hi = s.length
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2)
    if (enc.encode(s.slice(0, mid)).length <= budget) lo = mid
    else hi = mid - 1
  }
  let cut = s.slice(0, lo)
  // A lone high surrogate would render as U+FFFD — drop the half pair.
  if (/[\uD800-\uDBFF]$/.test(cut)) cut = cut.slice(0, -1)
  return cut + TRUNCATION_MARKER
}

/** The shared tail every `[UI]` turn gets: the byte clip, then the C32 living-view
 *  suffix. Factored out so a correction directive (annotate mode) inherits the SAME
 *  clip and the SAME "refresh in place" rule a widget action has, instead of a
 *  second dialect of both. */
export function finishActionText(body: string, live?: { saved: boolean; slug: string }): string {
  const base = clipToBytes(`${UI_PREFIX}${body}`, MAX_ACTION_TEXT_BYTES)
  // Living view (C32): name the source artifact so the agent refreshes THIS view in
  // place (artifact_update <slug>) instead of spawning a new one. Only meaningful
  // once the widget is saved and therefore has a slug the agent can target.
  return live?.saved && live.slug ? `${base} (refresh artifact "${live.slug}" in place)` : base
}

/** Compose the `[UI]` turn text for one widget action, or null if the payload
 *  cannot be serialized. */
export function composeWidgetActionText(
  action: string,
  payload: unknown,
  live?: { saved: boolean; slug: string }
): string | null {
  let body: string
  try {
    body = payload && Object.keys(payload as object).length > 0
      ? `${action}: ${JSON.stringify(payload)}`
      : action
  } catch {
    // postMessage's structured clone carries cycles that JSON.stringify cannot.
    // Refusing beats throwing: an unhandled throw in a window listener is a widget
    // crashing its host.
    return null
  }
  return finishActionText(body, live)
}

/** Republish a validated action onto the host bridge. */
export function publishWidgetAction(text: string, meta: WidgetActionMeta = {}): void {
  window.dispatchEvent(new CustomEvent(WIDGET_ACTION_EVENT, { detail: { text, ...meta } }))
}

