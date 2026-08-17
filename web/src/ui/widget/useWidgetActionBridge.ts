/** The widget action bridge — ONE wire contract, one validator, one publisher.
 *
 *  A `<widget>` runs in an iframe with `sandbox="allow-scripts"` off a blob (null)
 *  origin, so the only thing it can reach in the host is `parent.postMessage`. That
 *  single channel is the whole trust boundary, and everything crossing it is
 *  untrusted input. The contract (documented in docs/architecture/widgets.md):
 *
 *    child → parent   `widget-height {height, width?}`
 *                     `widget-action {action, payload?}`
 *                     `widget-error  {message}`
 *                     `widget-edit-values  {values}`   (EDITMODE read-back)
 *                     `widget-annotation   {selector, tag, outerHTML, parentContext}`
 *    parent → child   the reserved `__edit_mode_*` namespace (artifact iteration)
 *
 *  Additive only: a new message needs a new `type`, never a re-meant field.
 *
 *  Two invariants keep a widget from driving the host on its own:
 *    · the child's HOST_SCRIPT forwards a `[data-action]` click only when
 *      `e.isTrusted` — a widget's own script cannot synthesize a human gesture;
 *    · the host accepts a message only from THAT frame's own `contentWindow` — a
 *      sibling frame, an extension, or the page itself cannot forge one.
 *  A host whose child document carries no `isTrusted` gate (the react harness) does
 *  not opt into action forwarding at all.
 *
 *  Consumers: a CHAT host claims the bridge while mounted (the action becomes the
 *  next turn in THAT conversation); the app shell's fallback covers every other host
 *  (artifact-library preview, dashboard tile band, …) by opening a chat through the
 *  ONE `ne:launch-chat` path. Exactly one consumer runs per action. */
import { useEffect, useRef, type RefObject } from 'react'
import { launchChat } from '../../app/appSdk'
import { EDIT_KEY_RE } from './editMode'
import { readAnnotation, type WidgetAnnotation } from './annotate'

/** The turn prefix an agent branches on. Stable — do not reword. */
const UI_PREFIX = '[UI] '

/** An adversarial widget must not be able to stuff a conversation turn, so the
 *  payload-bearing text clips here — with a marker, because a silent truncation
 *  would hand the agent a lie about what the user submitted. */
export const MAX_ACTION_TEXT_BYTES = 16 * 1024
const TRUNCATION_MARKER = '…truncated'

/** Parent→child only. A child claiming this namespace is out of contract and is
 *  refused, so the reservation is enforced rather than merely documented. */
const PARENT_TO_CHILD_PREFIX = '__edit_mode_'

/** The window CustomEvent the validated wire republishes onto. One event, one
 *  vocabulary — hosts never listen to raw `message` themselves. */
export const WIDGET_ACTION_EVENT = 'ne:widget-action'

export type WidgetWireMessage =
  | { type: 'widget-height'; height: number; width?: number }
  | { type: 'widget-action'; action: string; payload: unknown }
  | { type: 'widget-error'; message: string }
  | { type: 'widget-edit-values'; values: Record<string, string> }
  | { type: 'widget-edit-ready' }
  | { type: 'widget-annotation'; annotation: WidgetAnnotation }

/** Caps on the EDITMODE read-back. The child answers a request the parent made, so
 *  the key set is already known — these bound a child that answers something else
 *  entirely (a thousand keys, a megabyte value). */
const MAX_EDIT_VALUES = 32
const MAX_EDIT_VALUE_LEN = 200

/** What the host learns about the widget an action came from. */
export interface WidgetActionMeta {
  /** The saved artifact this widget IS, when it is one (the C32 living view). */
  slug?: string
}

/** Validate one raw `message` event against the child→parent contract.
 *
 *  Fail-CLOSED by construction: provenance is checked before shape, and anything
 *  that is not a known type with well-typed required fields returns null. `payload`
 *  is deliberately NOT shape-checked — it becomes text in a chat turn, not a
 *  command, so constraining it would only break legitimate widgets. */
export function readWidgetMessage(
  e: MessageEvent,
  frame: HTMLIFrameElement | null
): WidgetWireMessage | null {
  if (!frame || !e.source || e.source !== frame.contentWindow) return null
  const d = e.data as Record<string, unknown> | null | undefined
  if (!d || typeof d !== 'object') return null
  const type = d.type
  if (typeof type !== 'string' || type.startsWith(PARENT_TO_CHILD_PREFIX)) return null
  if (type === 'widget-height') {
    if (typeof d.height !== 'number' || !Number.isFinite(d.height)) return null
    const w = d.width
    const width = typeof w === 'number' && Number.isFinite(w) && w > 0 ? w : undefined
    return { type, height: d.height, width }
  }
  if (type === 'widget-action') {
    if (typeof d.action !== 'string' || !d.action) return null
    return { type, action: d.action, payload: d.payload }
  }
  if (type === 'widget-error') return { type, message: String(d.message || 'Render error') }
  if (type === 'widget-edit-ready') return { type }
  if (type === 'widget-edit-values') {
    const raw = d.values
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
    const values: Record<string, string> = {}
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      if (Object.keys(values).length >= MAX_EDIT_VALUES) break
      if (!EDIT_KEY_RE.test(k) || typeof v !== 'string') continue
      values[k] = v.slice(0, MAX_EDIT_VALUE_LEN)
    }
    return { type, values }
  }
  if (type === 'widget-annotation') {
    const annotation = readAnnotation(d)
    return annotation ? { type, annotation } : null
  }
  return null
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

export interface WidgetWireHandlers {
  /** Forward `widget-action` as an `[UI]` turn. OPT-IN: only a host whose child
   *  document carries HOST_SCRIPT's `e.isTrusted` click gate may forward actions.
   *  The react harness has no such gate, so a react widget's own script must not be
   *  able to mint a turn without a human gesture. */
  forwardActions?: boolean
  onHeight?: (height: number, width?: number) => void
  onError?: (message: string) => void
  /** Read the widget's live saved-artifact identity at the moment of the action. */
  liveArtifact?: () => { saved: boolean; slug: string }
  /** The child's answer to `__edit_mode_read_keys` — what the document ACTUALLY
   *  holds, which is what Save writes (never what the rail believes it sent). */
  onEditValues?: (values: Record<string, string>) => void
  /** The child document has installed its iteration script and can receive edits.
   *  The host answers by seeding the artifact's declared values — sending them any
   *  earlier would race the blob load and be dropped. */
  onEditReady?: () => void
  /** One click-annotated element. Not action-gated: an annotation is not a turn on
   *  its own, it accumulates in the rail until the user sends the correction. */
  onAnnotation?: (annotation: WidgetAnnotation) => void
}

/** PRODUCER side: attach the validated child→parent wire to `frameRef`. */
export function useWidgetWire(
  frameRef: RefObject<HTMLIFrameElement | null>,
  handlers: WidgetWireHandlers
): void {
  // Handlers are read live, so the listener binds once and still sees the current
  // render's state (height cache key, saved slug) without rebinding per keystroke.
  const live = useRef(handlers)
  live.current = handlers
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const msg = readWidgetMessage(e, frameRef.current)
      if (!msg) return
      const h = live.current
      if (msg.type === 'widget-height') { h.onHeight?.(msg.height, msg.width); return }
      if (msg.type === 'widget-error') { h.onError?.(msg.message); return }
      if (msg.type === 'widget-edit-values') { h.onEditValues?.(msg.values); return }
      if (msg.type === 'widget-edit-ready') { h.onEditReady?.(); return }
      if (msg.type === 'widget-annotation') { h.onAnnotation?.(msg.annotation); return }
      if (!h.forwardActions) return
      const artifact = h.liveArtifact?.()
      const text = composeWidgetActionText(msg.action, msg.payload, artifact)
      if (text) publishWidgetAction(text, artifact?.saved ? { slug: artifact.slug } : {})
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [frameRef])
}

// ── consumer side ────────────────────────────────────────────────────────────
type WidgetActionConsumer = (text: string, meta: WidgetActionMeta) => void

// In mount order. A chat host claims the bridge while it is mounted; the shell's
// fallback covers every other host. Exactly ONE runs per action — running both
// would send the turn twice.
const chatHosts: WidgetActionConsumer[] = []
const fallbacks: WidgetActionConsumer[] = []

function dispatchToConsumer(e: Event): void {
  const detail = (e as CustomEvent).detail
  const text = detail?.text
  if (typeof text !== 'string' || !text.trim()) return
  const consumer = chatHosts[chatHosts.length - 1] ?? fallbacks[fallbacks.length - 1]
  consumer?.(text, { slug: typeof detail?.slug === 'string' ? detail.slug : undefined })
}

function useConsumer(list: WidgetActionConsumer[], onAction: WidgetActionConsumer): void {
  const live = useRef(onAction)
  live.current = onAction
  useEffect(() => {
    const entry: WidgetActionConsumer = (text, meta) => live.current(text, meta)
    list.push(entry)
    if (chatHosts.length + fallbacks.length === 1) {
      window.addEventListener(WIDGET_ACTION_EVENT, dispatchToConsumer)
    }
    return () => {
      const i = list.indexOf(entry)
      if (i >= 0) list.splice(i, 1)
      if (chatHosts.length + fallbacks.length === 0) {
        window.removeEventListener(WIDGET_ACTION_EVENT, dispatchToConsumer)
      }
    }
  }, [list])
}

/** CHAT host: a widget action becomes the next turn in THIS conversation. Pass the
 *  host's own send — that is its whole job on this wire. */
export function useWidgetActionBridge(onAction: WidgetActionConsumer): void {
  useConsumer(chatHosts, onAction)
}

/** APP SHELL: the fallback for every non-chat host — artifact-library preview,
 *  dashboard tile band, design cockpit. Registered once, at the shell, so a new
 *  widget host inherits routing instead of dropping actions on the floor. */
export function useWidgetActionLauncher(): void {
  useConsumer(fallbacks, launchWidgetActionInChat)
}

/** The `[UI]` text waits HERE, in memory, for the chat host that is about to mount
 *  — deliberately NOT in the URL. A `?send=` query param would turn "auto-send this
 *  prompt" into a link anyone could hand the user; keeping the authority in-process
 *  means only a real widget action can arm it. Drained once, and expired so a
 *  navigation that never reached chat cannot fire a stale turn much later. */
const HANDOFF_TTL_MS = 20_000
let staged: { text: string; at: number } | null = null

function launchWidgetActionInChat(text: string): void {
  staged = { text, at: Date.now() }
  launchChat()
}

/** Drain the staged widget-action turn if one is still fresh. */
export function takePendingWidgetAction(): string | null {
  const p = staged
  staged = null
  if (!p) return null
  return Date.now() - p.at <= HANDOFF_TTL_MS ? p.text : null
}
