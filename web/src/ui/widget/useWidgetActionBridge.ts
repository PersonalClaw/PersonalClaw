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
 *  What keeps a widget from driving the host on its own:
 *    · a `widget-action` never sends a turn by itself. The child's HOST_SCRIPT forwards a
 *      `[data-action]` click only when `e.isTrusted`, but that listener lives in the same
 *      document as the widget's own script, which can post the same message directly, and
 *      the parent cannot tell the two apart. So the parent puts the action in front of the
 *      owner unsent: a chat host puts it in an idle composer (its Send, Steer, Queue and
 *      Enter held a moment after) or offers it as a chip, every other host asks in the
 *      parent's own DOM with a confirm that answers only a press begun after a quiet moment,
 *      and only the owner's press there sends it;
 *    · an action counts only while its frame holds focus, as a real click or key in it leaves
 *      it: a post made while the owner works elsewhere in the page is dropped. Whether the
 *      owner gave the frame that focus (a click, a tap or a Tab into it, as the page reads it)
 *      decides only whether a chat fills its composer or offers a chip; nothing a frame does
 *      sends anything;
 *    · a "no" to a frame (Don't send, Dismiss, the draft's ×, an offered draft emptied) drops
 *      that frame's unpressed actions while it stays mounted, and any answer to one (a yes,
 *      an Add, a Send of its draft) makes its next one wait {@link COOLDOWN_MS};
 *    · the host accepts a message only from THAT frame's own `contentWindow` — a sibling
 *      frame, an extension, or the page itself cannot forge one;
 *    · a `widget-action` arriving while the parent holds no transient user activation (no
 *      click a moment ago) is dropped. A second fence only: activation is timing, and a
 *      click elsewhere can coincide with a post;
 *    · a turn that came from a widget says so (`widget_action` in its meta), and the
 *      gateway takes none of its links as the owner's own.
 *  A host whose child document carries no `isTrusted` gate (the react harness) does
 *  not opt into action forwarding at all.
 *
 *  Consumers: a CHAT host claims the bridge while mounted (the action lands in THAT
 *  conversation's composer, or, pressed in the page, as its next turn); the app shell's
 *  fallback covers every other host (artifact-library preview, dashboard tile band, …)
 *  by asking the owner and then opening a chat through the ONE `ne:launch-chat` path.
 *  Exactly one consumer runs per action. */
import { createElement, useEffect, useRef, type RefObject } from 'react'
import { confirm } from '../dialog'
import { WidgetActionAsk } from './WidgetActionAsk'
import {
  WIDGET_ACTION_EVENT,
  composeWidgetActionText,
  publishWidgetAction,
  escapeHidden,
  type WidgetActionMeta,
} from './actionTurn'
import { EDIT_KEY_RE } from './editMode'
import { FRAMES_MAY_HAVE_MOVED } from '../framesMayHaveMoved'
import { newMessageId } from '../../lib/messageIds'
import { readAnnotation, type WidgetAnnotation } from './annotate'

// Re-exported so the bridge stays the ONE public path for the turn dialect its consumers
// already import (`useArtifactIteration`, the genui router, the contract tests).
export {
  MAX_ACTION_TEXT_BYTES,
  WIDGET_ACTION_EVENT,
  composeWidgetActionText,
  finishActionText,
  publishWidgetAction,
} from './actionTurn'
export type { WidgetActionMeta } from './actionTurn'

/** Parent→child only. A child claiming this namespace is out of contract and is
 *  refused, so the reservation is enforced rather than merely documented. */
const PARENT_TO_CHILD_PREFIX = '__edit_mode_'

export type WidgetWireMessage =
  | { type: 'widget-height'; height: number; width?: number }
  | { type: 'widget-action'; action: string; payload: unknown }
  | { type: 'widget-error'; message: string }
  | { type: 'widget-edit-values'; values: Record<string, string> }
  | { type: 'widget-edit-ready' }
  | { type: 'widget-annotation'; annotation: WidgetAnnotation }

/** How much of a frame's `widget-error` text is shown. */
const MAX_ERROR_LEN = 200
/** At most one `widget-height` a frame is applied in this window; the last one in it follows. */
const HEIGHT_GAP_MS = 300
/** At most one `widget-error` a frame is shown in this window. */
const ERROR_GAP_MS = 5000

/** Caps on the EDITMODE read-back. The child answers a request the parent made, so
 *  the key set is already known — these bound a child that answers something else
 *  entirely (a thousand keys, a megabyte value). */
const MAX_EDIT_VALUES = 32
const MAX_EDIT_VALUE_LEN = 200

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
  if (type === 'widget-error') {
    // The frame's own words, shown in a toast: clipped to a sentence's length.
    return { type, message: String(d.message || 'Render error').slice(0, MAX_ERROR_LEN) }
  }
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

export interface WidgetWireHandlers {
  /** Forward `widget-action`, to be put in front of the owner unsent. OPT-IN: only a host
   *  whose child document carries HOST_SCRIPT's `e.isTrusted` click gate forwards actions.
   *  The react harness has no click gate, so it forwards nothing. */
  forwardActions?: boolean
  /** The frame's title, which names it to the owner when its action is put in front of them. */
  title?: string
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
  /** One annotation the frame reports. Not action-gated: an annotation is not a turn on its
   *  own. The host takes one only in annotate mode, and the correction goes out only on the
   *  owner's Send. */
  onAnnotation?: (annotation: WidgetAnnotation) => void
}

/** The page's own id for each mount of a frame host, which a "Don't send" is kept by. */
let hostSeq = 0

/** What the page sees of the owner's way into each widget frame, read by `focusGiven` when one
 *  of them posts an action. One set of page listeners serves every frame mounted now. */
interface FrameTrack {
  /** The frame's mount id, as its posts carry it. */
  host: string
  frame: () => HTMLIFrameElement | null
  /** The iframe element the rest was read on: a new element starts over. */
  el: HTMLIFrameElement | null
  /** The element and document (its src) the frame last finished loading: until its current
   *  document has loaded, the frame is still coming to rest. */
  loaded: { el: HTMLIFrameElement; src: string } | null
  /** The pointer is over this frame's iframe now, and when it last arrived there. */
  over: boolean
  overAt: number
  /** Whether the page saw the pointer itself move just before that arrival. */
  movedIn: boolean
  /** When this frame last mounted, loaded or had a height applied: a pointer that rests where it
   *  now sits did not move onto it. */
  settledAt: number
}
const tracked = new Set<FrameTrack>()
/** An arrival this soon after a mount, a height change or a scroll the owner didn't make is the
 *  frame moving under a resting pointer, unless the page saw the pointer move just before it. */
const SETTLE_MS = 500
/** A page pointer move this soon before an arrival over a frame is the pointer moving onto it. */
const MOVE_GAP_MS = 150
/** A window blur this soon after a Tab is that Tab's own move of focus. */
const TAB_GAP_MS = 100
const page = {
  /** When the owner last pressed a key the page saw (bar a modifier), or typed by `beforeinput`
   *  or a composition. */
  typedAt: Number.NEGATIVE_INFINITY,
  movedAt: Number.NEGATIVE_INFINITY,
  scrolledAt: Number.NEGATIVE_INFINITY,
  ownerScrolledAt: Number.NEGATIVE_INFINITY,
  tab: null as KeyboardEvent | null,
  tabAt: Number.NEGATIVE_INFINITY,
  /** The frame an un-prevented Tab of the owner's moved focus into, until focus is back. */
  tabbedInto: null as HTMLIFrameElement | null,
}
let watchingPage = false

function typingIn(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
}

function trackOf(t: EventTarget | null): FrameTrack | undefined {
  for (const f of tracked) if (t && f.frame() === t) return synced(f)
  return undefined
}

/** *f*, read again from scratch if its frame is a new iframe element now (a src change on the
 *  same element keeps where the pointer is). */
function synced(f: FrameTrack): FrameTrack {
  const el = f.frame()
  if (el !== f.el) {
    f.el = el
    f.loaded = null
    f.over = false
    f.overAt = Number.NEGATIVE_INFINITY
    f.movedIn = false
  }
  return f
}

/** The frames may have moved under a resting pointer (something above them grew or shrank):
 *  every tracked frame is coming to rest again. The transcript calls this as its content
 *  resizes. */
export function widgetFramesMayHaveMoved(): void {
  const at = Date.now()
  for (const f of tracked) f.settledAt = at
}

const MODIFIER_KEYS = new Set(['Shift', 'Control', 'Alt', 'Meta', 'AltGraph', 'CapsLock', 'Fn'])

function watchPage(): void {
  if (watchingPage || typeof document === 'undefined') return
  watchingPage = true
  const now = () => Date.now()
  document.addEventListener('keydown', (e) => {
    if (MODIFIER_KEYS.has(e.key)) return
    // Any key the page sees, a Tab's included: a pointer that arrived over a frame before it did
    // not bring the focus that follows.
    page.typedAt = now()
    if (e.key === 'Tab') { page.tab = e; page.tabAt = now(); return }
    if (['PageUp', 'PageDown', 'Home', 'End', 'ArrowUp', 'ArrowDown', ' '].includes(e.key) && !typingIn(e.target)) page.ownerScrolledAt = now()
  }, true)
  // A Tab whose move landed on something in the page didn't go to a frame.
  document.addEventListener('focusin', (e) => { if (!(e.target instanceof HTMLIFrameElement)) page.tab = null }, true)
  for (const t of ['beforeinput', 'compositionstart', 'compositionupdate'] as const) {
    document.addEventListener(t, (e) => { if (typingIn(e.target)) page.typedAt = now() }, true)
  }
  for (const t of ['pointermove', 'mousemove'] as const) document.addEventListener(t, () => { page.movedAt = now() }, true)
  for (const t of ['wheel', 'touchmove'] as const) document.addEventListener(t, () => { page.ownerScrolledAt = now() }, { capture: true, passive: true })
  document.addEventListener('scroll', () => { page.scrolledAt = now() }, { capture: true, passive: true })
  // ui/Modal, the shell's dialogs, ui/Popover, the command palette, the snip overlay and the
  // tour say so when they go (`framesMayHaveMoved`).
  window.addEventListener(FRAMES_MAY_HAVE_MOVED, () => widgetFramesMayHaveMoved())
  // A frame's document loading (its first, or one it navigated to) can move it under the pointer.
  document.addEventListener('load', (e) => {
    const f = trackOf(e.target)
    if (!f || !f.el) return
    f.settledAt = now()
    f.loaded = { el: f.el, src: f.el.getAttribute('src') ?? '' }
  }, true)
  for (const t of ['pointerover', 'mouseover'] as const) {
    document.addEventListener(t, (e) => {
      const f = trackOf(e.target)
      if (!f || f.over) return
      f.over = true
      f.overAt = now()
      // A new arrival is a new press to tell the owner about.
      toldAt.delete(f.host)
      f.movedIn = f.overAt - page.movedAt <= MOVE_GAP_MS
      // A Tab's grant went with the frame it took focus into: the pointer arriving over another
      // frame is the only trace a click from one frame into the next leaves.
      if (page.tabbedInto && page.tabbedInto !== f.el) page.tabbedInto = null
    }, true)
  }
  for (const t of ['pointerout', 'mouseout'] as const) {
    document.addEventListener(t, (e) => { const f = trackOf(e.target); if (f) f.over = false }, true)
  }
  window.addEventListener('blur', () => {
    // An un-prevented Tab of the owner's whose own move took focus into a frame.
    const tab = page.tab
    const active = document.activeElement
    page.tabbedInto = tab && !tab.defaultPrevented && now() - page.tabAt <= TAB_GAP_MS && active instanceof HTMLIFrameElement
      ? active : null
    // A Tab of the owner's into a frame is a new press to tell them about, as an arrival is.
    if (page.tabbedInto) { const f = trackOf(page.tabbedInto); if (f) toldAt.delete(f.host) }
    page.tab = null
  })
  window.addEventListener('focus', () => { page.tabbedInto = null })
}

/** Whether the owner put focus in *f*'s frame, which holds it now: the pointer arrived over it
 *  after the owner last typed (a click or a tap in it), or an un-prevented Tab of theirs took
 *  focus there. An arrival that only follows the frame moving under a resting pointer (its
 *  mount, a height change, or a scroll the owner didn't make), with no pointer move the page
 *  saw just before it, doesn't count. It decides only where the frame's action goes in a chat:
 *  into an idle composer, or offered as a chip. */
function focusGiven(f: FrameTrack): boolean {
  const el = synced(f).el
  if (!el || document.activeElement !== el || !document.hasFocus()) return false
  if (page.tabbedInto === el) return true
  if (!f.over || f.overAt <= page.typedAt) return false
  if (f.movedIn) return true
  // With no move the page saw, a frame whose current document hasn't finished loading is still
  // coming to rest where it appeared.
  if (!f.loaded || f.loaded.el !== el || f.loaded.src !== (el.getAttribute('src') ?? '')) return false
  // A scroll the owner didn't make (the transcript's own follow) moves frames under the pointer
  // as a mount or a height change does; one that follows their wheel or scroll key is theirs.
  const ownersScroll = page.scrolledAt - page.ownerScrolledAt <= SETTLE_MS
  const settledAt = Math.max(f.settledAt, ownersScroll ? Number.NEGATIVE_INFINITY : page.scrolledAt)
  return f.overAt - settledAt > SETTLE_MS
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
    // This mount's id: the frame reloading itself keeps it; a remount gets a new one.
    const host = `frame-${++hostSeq}`
    watchPage()
    const track: FrameTrack = { host, frame: () => frameRef.current, el: frameRef.current, loaded: null, over: false, overAt: Number.NEGATIVE_INFINITY, movedIn: false, settledAt: Date.now() }
    tracked.add(track)
    const height = { at: 0, timer: 0, pending: null as [number, number | undefined] | null }
    // The last error the frame posted, and when: only a repeat of it is held back, so a new
    // document's (or a new) error always shows.
    const lastError = { message: '', at: Number.NEGATIVE_INFINITY }
    const applyHeight = (value: number, width?: number) => {
      height.at = Date.now()
      // A frame's new height moves every frame after it, as well as this one.
      widgetFramesMayHaveMoved()
      live.current.onHeight?.(value, width)
    }
    const onMessage = (e: MessageEvent) => {
      const frame = frameRef.current
      const msg = readWidgetMessage(e, frame)
      if (!msg) return
      const h = live.current
      if (msg.type === 'widget-height') {
        const since = Date.now() - height.at
        if (since >= HEIGHT_GAP_MS) { height.pending = null; applyHeight(msg.height, msg.width); return }
        height.pending = [msg.height, msg.width]
        if (!height.timer) {
          height.timer = window.setTimeout(() => {
            height.timer = 0
            const next = height.pending
            height.pending = null
            if (next) applyHeight(next[0], next[1])
          }, HEIGHT_GAP_MS - since)
        }
        return
      }
      if (msg.type === 'widget-error') {
        const now = Date.now()
        if (msg.message === lastError.message && now - lastError.at < ERROR_GAP_MS) return
        lastError.message = msg.message
        lastError.at = now
        h.onError?.(`${frameName(h.title, { inQuotes: false })}: ${msg.message}`)
        return
      }
      if (msg.type === 'widget-edit-values') { h.onEditValues?.(msg.values); return }
      if (msg.type === 'widget-edit-ready') { h.onEditReady?.(); return }
      if (msg.type === 'widget-annotation') { h.onAnnotation?.(msg.annotation); return }
      if (!h.forwardActions) return
      if (!ownerActedJustNow()) { dropped(host, 'no click or key of the owner in the last few seconds'); return }
      // Dropped unless the frame holds focus (a real click or key in a frame focuses its iframe):
      // a post made while the owner works elsewhere in the page is not this frame's to make.
      if (document.activeElement !== frame) { dropped(host, 'its frame does not hold focus'); return }
      const earned = focusGiven(track)
      const artifact = h.liveArtifact?.()
      const text = composeWidgetActionText(msg.action, msg.payload, artifact)
      if (!text) {
        // A frame turned down or answered a moment ago says nothing, as its other posts don't.
        if (earned && !declined.has(host) && !((coolingUntil.get(host) ?? 0) > Date.now())) tellOwner(host, h.title?.trim() ? frameName(h.title) : undefined, 'had no message to offer')
        return
      }
      // Never `pressed`: whatever the frame says, it is put in front of the owner unsent. Whether
      // the page reads the frame's focus as the owner's says only how: a chat fills an idle
      // composer with it, or offers it as a chip where the page doesn't read it so.
      publishWidgetAction(text, {
        ...(artifact?.saved ? { slug: artifact.slug } : {}),
        ...(h.title ? { frame: h.title } : {}),
        host,
        ...(earned ? { earned: true } : {}),
      })
    }
    window.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener('message', onMessage)
      tracked.delete(track)
      window.clearTimeout(height.timer)
      forgetWidgetHost(host)
    }
  }, [frameRef])
}

/** How much of a frame's title names it to the owner. */
const MAX_FRAME_NAME = 80

/** A frame's own title as the page names it to the owner: clipped to {@link MAX_FRAME_NAME}
 *  code points, each hidden character and each quote mark written as its escape (so a title
 *  cannot close the quotes it is shown in, nor hide what it says), and "Widget" for none. */
export function frameName(title: string | undefined, { inQuotes = true } = {}): string {
  const name = [...(title ?? '').trim()].slice(0, MAX_FRAME_NAME).join('')
  if (!name) return 'Widget'
  // Outside quote marks (an error's prefix), a quote mark in the title closes nothing.
  if (!inQuotes) return escapeHidden(name)
  return escapeHidden(name).replace(/["\u201c\u201d\u201e\u201f\u2033\u00ab\u00bb]/gu,
    (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`)
}

/** Say, for whoever reads the console, why a frame's post went nowhere. */
function dropped(host: string, why: string): void {
  console.debug(`A widget's action from ${host} was dropped: ${why}.`)
}

/** When each frame's posts last told the owner something: until the pointer next arrives over
 *  its frame, the owner answers it or tabs into it, or {@link COOLDOWN_MS} passes, that frame
 *  tells nothing more. */
const toldAt = new Map<string, number>()

/** Say where the owner looks why a post they made in a frame did nothing: the chat's notice line,
 *  or a toast on any other host. Only for a post the page reads as the owner's (`earned`), and
 *  once per frame until the pointer next arrives over it, the owner answers it or tabs into it,
 *  or {@link COOLDOWN_MS} passes, so a frame that loops its own posts says it once in that time.
 *  A tell the chat doesn't show (its line holds an error) spends nothing. */
function tellOwner(host: string, name: string | undefined, what: string): void {
  const now = Date.now()
  const last = toldAt.get(host)
  if (last !== undefined && now - last < COOLDOWN_MS) return
  // *name* is the frame's name as the page shows it (`frameName`), or none.
  const said = `${name ? `\u201c${name}\u201d` : 'The widget'} ${what}.`
  const chat = chatHosts[chatHosts.length - 1]
  if (chat?.told) {
    if (chat.told(said) !== false) toldAt.set(host, now)
    return
  }
  try { window.dispatchEvent(new CustomEvent('ne:toast', { detail: { message: said, level: 'info' } })) } catch { /* no window */ }
  toldAt.set(host, now)
}

/** Whether the owner has acted in this page in the last few seconds: a click, a key, a tap
 *  (`navigator.userActivation`, which a click inside a frame also gives its parent). A post with
 *  no such gesture is dropped. It is a second fence only: a post just after a click anywhere in
 *  the page still passes it, and the frame-focus check at receipt is the first. Where the
 *  browser has no such reading the answer is yes, and the action is still only put in front of
 *  the owner. */
function ownerActedJustNow(): boolean {
  const activation = (navigator as Navigator & { userActivation?: { isActive: boolean } })
    .userActivation
  return activation ? activation.isActive : true
}

// ── consumer side ────────────────────────────────────────────────────────────
type WidgetActionConsumer = (text: string, meta: WidgetActionMeta) => void

/** After the owner answers a frame's action (a yes to the ask, an Add, a Send of the draft it
 *  filled), its next unpressed action waits this long. A no drops them all while it is
 *  mounted. */
const COOLDOWN_MS = 5000

/** What a CHAT host does with a widget action. */
export interface WidgetActionHost {
  /** Send it as the next turn: an action the owner pressed in the page, or confirmed. */
  send: WidgetActionConsumer
  /** Put it in front of the owner, unsent: an action a frame raised. The owner's own Send
   *  is then the only thing that sends it. */
  offer: WidgetActionConsumer
  /** Say where the owner looks why a post of theirs in a frame did nothing. False: it wasn't
   *  shown (the line holds something that matters more), so the frame may tell it again. */
  told?: (said: string) => boolean | void
}

// In mount order. A chat host claims the bridge while it is mounted; the shell's
// fallback covers every other host. Exactly ONE runs per action — running both
// would send the turn twice.
const chatHosts: WidgetActionHost[] = []
const fallbacks: WidgetActionConsumer[] = []

/** The frame mounts the owner said no to ("Don't send", or an offered draft emptied or
 *  dismissed): their unpressed actions are dropped until the frame's host remounts. */
const declined = new Set<string>()
/** What an unpressed action that names no frame mount is kept by. */
const UNNAMED_HOST = 'frame-unnamed'
/** Each frame mount's next allowed unpressed action, after the owner's last answer to one. */
const coolingUntil = new Map<string, number>()

/** The owner said no to what *host*'s frame offered: drop its actions while it is mounted. */
export function declineWidgetHost(host: string | undefined): void {
  if (!host) return
  declined.add(host)
  coolingUntil.delete(host)
  // The owner's own answer: the frame's next tell is said at once.
  toldAt.delete(host)
}

/** The owner answered *host*'s frame: its next action waits {@link COOLDOWN_MS}. */
export function coolWidgetHost(host: string | undefined): void {
  if (!host) return
  coolingUntil.set(host, Date.now() + COOLDOWN_MS)
  toldAt.delete(host)
}

/** *host*'s frame is gone: forget what was kept for it. */
export function forgetWidgetHost(host: string): void {
  declined.delete(host)
  coolingUntil.delete(host)
  toldAt.delete(host)
}

function dispatchToConsumer(e: Event): void {
  const detail = (e as CustomEvent).detail
  const text = detail?.text
  if (typeof text !== 'string' || !text.trim()) return
  const meta: WidgetActionMeta = {
    slug: typeof detail?.slug === 'string' ? detail.slug : undefined,
    // The human-friendly label rides through to the chat host so the bubble can show
    // it INSTEAD of the machine payload. Dropped here would mean the transcript
    // showing JSON — the exact defect the dual payload exists to prevent.
    label: typeof detail?.label === 'string' && detail.label.trim() ? detail.label : undefined,
    pressed: detail?.pressed === true,
    // The frame's title is the widget's own words: shown to the owner, so named as `frameName`
    // names it.
    frame: typeof detail?.frame === 'string' && detail.frame.trim() ? frameName(detail.frame) : undefined,
    host: typeof detail?.host === 'string' && detail.host ? detail.host : undefined,
    earned: detail?.earned === true ? true : undefined,
  }
  if (!meta.pressed) {
    // Only a press in the page carries a label. An unpressed action with no host is kept as one
    // unnamed frame, so a no to it holds as to any other.
    meta.label = undefined
    const key = meta.host ?? UNNAMED_HOST
    meta.host = key
    if (declined.has(key)) {
      dropped(key, 'the owner said no to this frame')
      if (meta.earned) tellOwner(key, meta.frame, chatHosts.length ? 'is turned down while this chat is open' : 'is turned down while it is shown here')
      return
    }
    if ((coolingUntil.get(key) ?? 0) > Date.now()) {
      dropped(key, 'the owner answered this frame a moment ago')
      if (meta.earned) tellOwner(key, meta.frame, 'can offer another message in a moment')
      return
    }
  }
  const host = chatHosts[chatHosts.length - 1]
  if (host) {
    if (meta.pressed) host.send(text, meta)
    else host.offer(text, meta)
    return
  }
  fallbacks[fallbacks.length - 1]?.(text, meta)
}

function listenWhileAnyConsumes(): () => void {
  if (chatHosts.length + fallbacks.length === 1) {
    window.addEventListener(WIDGET_ACTION_EVENT, dispatchToConsumer)
  }
  return () => {
    if (chatHosts.length + fallbacks.length === 0) {
      window.removeEventListener(WIDGET_ACTION_EVENT, dispatchToConsumer)
    }
  }
}

/** CHAT host: a widget action lands in THIS conversation. `send` is the host's own send,
 *  for a press in the page; `offer` puts a frame's action in front of the owner, unsent. */
export function useWidgetActionBridge(host: WidgetActionHost): void {
  const live = useRef(host)
  live.current = host
  useEffect(() => {
    const entry: WidgetActionHost = {
      send: (text, meta) => live.current.send(text, meta),
      offer: (text, meta) => live.current.offer(text, meta),
      // Only a host that says it on its own line takes the tell; otherwise it is a toast.
      ...(host.told ? { told: (said: string) => live.current.told?.(said) } : {}),
    }
    chatHosts.push(entry)
    const stop = listenWhileAnyConsumes()
    return () => {
      const i = chatHosts.indexOf(entry)
      if (i >= 0) chatHosts.splice(i, 1)
      stop()
    }
  }, [])
}

/** APP SHELL: the fallback for every non-chat host — artifact-library preview,
 *  dashboard tile band, design cockpit. Registered once, at the shell, so a new
 *  widget host inherits routing instead of dropping actions on the floor. *at* is where the
 *  shell is: a staged turn is dropped as soon as the page goes anywhere but the new chat its
 *  launch opens. */
export function useWidgetActionLauncher(at?: string): void {
  useEffect(() => {
    fallbacks.push(launchWidgetActionInChat)
    const stop = listenWhileAnyConsumes()
    return () => {
      const i = fallbacks.indexOf(launchWidgetActionInChat)
      if (i >= 0) fallbacks.splice(i, 1)
      stop()
    }
  }, [])
  useEffect(() => {
    if (staged && at !== undefined && at !== STAGED_AT) staged = null
  }, [at])
}

/** The `[UI]` text waits HERE, in memory, for the chat host that is about to mount
 *  — deliberately NOT in the URL. A `?send=` query param would turn "auto-send this
 *  prompt" into a link anyone could hand the user; keeping the authority in-process
 *  means only the owner's press can arm it: a press in the page, or their yes to the
 *  ask below. The launch carries a nonce, and only the chat its navigation opens drains it.
 *  Drained once, and expired so a navigation that never reached chat cannot fire a stale
 *  turn much later. */
const HANDOFF_TTL_MS = 20_000
/** Where the launch of a staged turn goes (App's `ne:launch-chat`: a new chat). */
const STAGED_AT = 'chat/new'
/** The shell's one way into a new chat (App.tsx, and `launchChat` in the apps' SDK). */
const LAUNCH_CHAT_EVENT = 'ne:launch-chat'
let staged: { text: string; label?: string; at: number; nonce: string } | null = null

/** The staged turn: the machine text plus (genui only) the label the bubble shows. */
export interface PendingWidgetAction {
  text: string
  label?: string
}

function stageAndLaunch(text: string, meta: WidgetActionMeta): void {
  // Minted from getRandomValues, which every context has: randomUUID exists only in a secure
  // one, and the remote link may be plain http.
  const nonce = newMessageId().slice('msg_'.length)
  staged = { text, label: meta.label, at: Date.now(), nonce }
  // The one `ne:launch-chat` path (App.tsx), as `launchChat` raises it, with the nonce the new
  // chat drains by. Not a `launchChat` option: that is the apps' SDK, and nothing of an app's
  // may name one.
  window.dispatchEvent(new CustomEvent(LAUNCH_CHAT_EVENT, { detail: { widgetAction: nonce } }))
}

/** One ask at a time: a frame that posts a hundred actions gets one question, and the rest
 *  are dropped while it is open. */
let asking = false

function launchWidgetActionInChat(text: string, meta: WidgetActionMeta): void {
  if (meta.pressed) {
    stageAndLaunch(text, meta)
    return
  }
  if (asking) {
    dropped(meta.host ?? 'a frame', 'an ask is already open')
    if (meta.earned) tellOwner(meta.host ?? UNNAMED_HOST, meta.frame, 'can ask once the open question is answered')
    return
  }
  asking = true
  void confirm({
    title: meta.frame ? `A widget titled “${meta.frame}” asks to send this` : 'A widget asks to send this',
    body: createElement(WidgetActionAsk, { text }),
    confirmLabel: 'Send in a new chat',
    cancelLabel: 'Don’t send',
    deliberate: true,
  })
    .then((yes) => {
      if (yes) {
        coolWidgetHost(meta.host)
        stageAndLaunch(text, meta)
      } else {
        declineWidgetHost(meta.host)
      }
    })
    .finally(() => { asking = false })
}

/** Drain the staged widget-action turn if one is still fresh and *nonce* is the one its
 *  launch carried. */
export function takePendingWidgetAction(nonce?: string): PendingWidgetAction | null {
  const p = staged
  if (!p || !nonce || p.nonce !== nonce) return null
  staged = null
  if (Date.now() - p.at > HANDOFF_TTL_MS) return null
  return { text: p.text, label: p.label }
}

/** Whether *nonce* is the staged turn's own: App's `ne:launch-chat` carries a nonce on only that
 *  launch, so nothing an app raises can name one. */
export function isStagedWidgetNonce(nonce: unknown): boolean {
  return typeof nonce === 'string' && !!nonce && staged?.nonce === nonce
}

/** Drop a staged turn no chat drained (tests, and a launch that never reached its chat). */
export function forgetPendingWidgetAction(): void {
  staged = null
}
