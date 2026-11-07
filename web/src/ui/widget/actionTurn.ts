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
  /** The saved artifact this widget IS, when it is one (the living view). */
  slug?: string
  /** The genui dual payload's `humanFriendlyMessage` — the short label the
   *  TRANSCRIPT shows, while `text` (the `llmFriendlyMessage`) is what the model
   *  reads. Absent on the raw-HTML widget path, which has no declared label and
   *  whose turn text is therefore also its visible text. */
  label?: string
  /** The owner pressed a control the PAGE rendered in its own DOM (a genui button or form, the
   *  correction rail's Send), so the press is already the owner's decision and the turn may go.
   *  Never set for an action a frame raised: nothing a frame's own document does can stand for
   *  a press, so that action is put in front of the owner unsent (useWidgetActionBridge.ts). */
  pressed?: boolean
  /** The title of the frame an action came from, for the host that shows it to the owner. */
  frame?: string
  /** The page's own id for the mount of the frame an action came from (never the frame's
   *  title, which the widget writes): what a "Don't send" and its cooldown are kept by. */
  host?: string
  /** The owner put the focus that frame holds there (a click, a tap or a Tab into it, as the
   *  page reads it). It decides only how a chat puts the action in front of the owner: into an
   *  idle composer, or as a chip they can Add. Nothing a frame does sends anything. */
  earned?: boolean
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

/** The shared tail every `[UI]` turn gets: the byte clip, then the living-view
 *  suffix. Factored out so a correction directive (annotate mode) inherits the SAME
 *  clip and the SAME "refresh in place" rule a widget action has, instead of a
 *  second dialect of both. */
export function finishActionText(body: string, live?: { saved: boolean; slug: string }): string {
  const base = clipToBytes(`${UI_PREFIX}${body}`, MAX_ACTION_TEXT_BYTES)
  // Living view: name the source artifact so the agent refreshes THIS view in
  // place (artifact_update <slug>) instead of spawning a new one. Only meaningful
  // once the widget is saved and therefore has a slug the agent can target.
  return live?.saved && live.slug ? `${base} (refresh artifact "${live.slug}" in place)` : base
}

/** Characters that change what text shows without showing themselves: the format ones (zero
 *  width, bidi overrides and isolates), the C0 and C1 controls, and every other default-ignorable
 *  one (variation selectors, the combining grapheme joiner, the Hangul fillers). One text or
 *  emoji presentation selector straight after a pictograph is kept: it shows as that emoji. */
const HIDDEN_CHARS = /(\p{Extended_Pictographic}[\uFE0E\uFE0F])|[\p{Cc}\p{Cf}\p{DI}]/gu

/** The same set, for an editor that marks each one it holds (MarkdownInput's
 *  `highlightSpecialChars`): every default-ignorable and format character, DEL and the braille
 *  blank. Ordinary text carries some of them, so they stay unmarked: the joiners an emoji
 *  sequence or a Persian word is spelled with (U+200C, U+200D), the presentation and the
 *  ideographic variation selectors (U+FE0E, U+FE0F, VS17-256), and the tags of the three tag
 *  flags emoji has (England, Scotland, Wales: U+1F3F4, the tags of gbeng, gbsct or gbwls, then
 *  CANCEL TAG). Every other tag is marked, a run shaped like a flag's included. */
const tagOf = (c: string) => `\\u{${(0xe0000 + (c.codePointAt(0) ?? 0)).toString(16).toUpperCase()}}`
/** Each of the three flags' tags, as regex escapes, CANCEL TAG last. */
const FLAG_TAGS = ['gbeng', 'gbsct', 'gbwls'].map((id) => [...[...id].map(tagOf), '\\u{E007F}'])
/** At a tag: it is one of the three flags', right where that flag has it. */
const IN_A_FLAG = FLAG_TAGS.flatMap((tags) => tags.map((_, k) => `(?<=\\u{1F3F4}${tags.slice(0, k).join('')})${tags.slice(k).join('')}`)).join('|')
export const HIDDEN_CHAR_CLASS = new RegExp(
  `(?![\\u200C\\u200D\\uFE0E\\uFE0F\\u{E0100}-\\u{E01EF}\\u{E0020}-\\u{E007F}])[\\p{Cf}\\p{DI}\\u007f\\u2800]|(?!${IN_A_FLAG})[\\u{E0020}-\\u{E007F}]`,
  'u',
)

/** *s* with each hidden character dropped (an emoji's own presentation selector kept). */
function dropHidden(s: string): string {
  return s.replace(HIDDEN_CHARS, (_c, emoji: string | undefined) => emoji ?? '')
}

/** The action name as the owner reads it: each run of whitespace one space, hidden characters
 *  dropped, then the runs collapsed again (a run the hidden ones split is one run). A frame's
 *  leading newlines cannot push the words out of view, and a bidi override cannot make the text
 *  read otherwise than it sends. */
function plainAction(action: string): string {
  // The braille blank shows as nothing too: it collapses as whitespace does.
  return dropHidden(action.replace(/[\s\u2800]+/gu, ' ')).replace(/[\s\u2800]+/gu, ' ').trim()
}

/** *c* as `\uXXXX` escapes, one per UTF-16 unit. */
function escaped(c: string): string {
  return c.split('').map((unit) => `\\u${unit.charCodeAt(0).toString(16).padStart(4, '0')}`).join('')
}

/** *json* with every hidden character (as above, DEL too), C1 control and line or paragraph
 *  separator written as its `\uXXXX` escape, and so is every space but a lone or paired U+0020
 *  (a run of three or more, and every other kind): the same value to the model, and nothing
 *  of it out of the owner's view. (C0 controls JSON escapes already.) */
function visibleJson(json: string): string {
  return json
    .replace(/(\p{Extended_Pictographic}[\uFE0E\uFE0F])|[\p{Cf}\p{DI}\u007f-\u009f\u2028\u2029\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000\u2800]|\u0020{3,}/gu,
      (c, emoji: string | undefined) => emoji ?? escaped(c))
}

/** Show *name* (a frame's own title) with each hidden character as its `\uXXXX` escape. */
export function escapeHidden(name: string): string {
  return name.replace(HIDDEN_CHARS, (c, emoji: string | undefined) => emoji ?? escaped(c))
}

/** Compose the `[UI]` turn text for one widget action, or null if the payload
 *  cannot be serialized or the action names nothing. Normalized here, so the text the owner
 *  is shown is the text that is sent (`plainAction`, `visibleJson`). */
export function composeWidgetActionText(
  action: string,
  payload: unknown,
  live?: { saved: boolean; slug: string }
): string | null {
  const name = plainAction(action)
  if (!name) return null
  let body: string
  try {
    body = payload && Object.keys(payload as object).length > 0
      ? `${name}: ${visibleJson(JSON.stringify(payload))}`
      : name
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

