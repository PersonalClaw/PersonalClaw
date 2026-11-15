import { isStagedWidgetNonce } from '../ui/widget/stagedAction'

/** State a link names in its own words, written in the words the chat reads.
 *
 *  A `personalclaw://` link to a thread, and an address copied from one, can carry two keys the
 *  chat does not read: `prompt`, a new thread's draft, and `pane=<name>`, the pane to open. The
 *  chat reads its draft from `seed` and each pane from a flag of its own (`activity`, `history`).
 *  This maps the first onto the second and nothing else: the patch only sets state the person could
 *  set by hand, never a key that sends, approves or runs, and the chat then owns that state as if
 *  she had opened it.
 *
 *  - `prompt` becomes `seed` on a new thread (`chat/new`) only, as the desktop's link parser allows,
 *    and never replaces a draft the address already names. Anywhere else on the chat it is dropped.
 *  - `pane` becomes the chat's flag for a pane that page shows: Activity on a thread, Chat history
 *    on a new thread (`chat/new`, or the bare `chat` route, which opens one). Any other pane is
 *    dropped.
 *
 *  Only the chat route is mapped. Every other page owns its keys, `prompt` and `pane` among them
 *  (the Loop cockpit keeps its Prompt card open as `?prompt=1`), and is left as it is.
 *
 *  Returns null when there is nothing to write, so such an address is never rewritten. */
export function linkStatePatch(
  route: string,
  sub: string,
  query: Record<string, string>,
): Record<string, string | null> | null {
  if (route !== 'chat') return null
  const hasPrompt = 'prompt' in query
  const hasPane = 'pane' in query
  if (!hasPrompt && !hasPane) return null

  const seg = (sub || '').split('/')[0]
  const newThread = showsNewThread(route, sub)
  const thread = !newThread && seg !== 'history' && seg !== 'room'

  const patch: Record<string, string | null> = {}
  if (hasPrompt) {
    patch.prompt = null
    if (seg === 'new' && !query.seed && query.prompt) patch.seed = query.prompt
  }
  if (hasPane) {
    patch.pane = null
    const flag = query.pane === 'activity' && thread ? 'activity'
      : query.pane === 'history' && newThread ? 'history'
      : ''
    if (flag && query[flag] !== '1') patch[flag] = '1'
  }
  return patch
}

/** Whether the page at `route/sub` is a new thread: `chat/new`, or the bare `chat` route. */
export function showsNewThread(route: string, sub: string): boolean {
  const seg = (sub || '').split('/')[0]
  return route === 'chat' && (seg === 'new' || seg === '')
}

/** What a chat's address names for it to start from: its launch keys.
 *
 *  Every key here only sets state the person could set by hand in the composer: the draft, the
 *  agent, the project, chips. None sends. The one key that leads to a send, `widget_action`, names
 *  a widget action the owner already said yes to, staged in memory; it is no use without the stage,
 *  and the writer below writes it only for the staged turn's own nonce (`ui/widget/stagedAction`),
 *  whoever calls it and with whatever they pass.
 *
 *  - `seed`: the composer's draft (a new chat, or a chat staged before its first turn);
 *  - `agent`: the agent a new chat starts with;
 *  - `widget_action`: the nonce of that staged action, which the new chat drains and sends;
 *  - `project`: the project a new chat is in;
 *  - `from_widget`: the draft (`seed`) is a widget's message carried over from a chat that is gone
 *    (`MissingChat`), so what is sent from it says so;
 *  - `attach`: `<ref>[,<ref>]` (`lib/itemRef.ts`), library items the composer holds as chips on a
 *    new chat or a chat you were in. They are attached, never sent: the person sends them, or
 *    takes them out. Read together with `project`, and with a draft that already has text. */
export interface ChatLaunch {
  seed: string
  agent: string
  widgetAction: string
  project: string
  /** The draft is a widget's message carried over (`from_widget=1`). */
  fromWidget: boolean
  /** The refs, in the link's order, each once. */
  attach: string[]
}

/** The most refs one link attaches; the rest are said as not attached. */
export const MAX_LINK_ATTACH = 20

const LAUNCH_KEYS = { seed: 'seed', agent: 'agent', widgetAction: 'widget_action', project: 'project', fromWidget: 'from_widget', attach: 'attach' } as const

/** The launch keys `query` names, each '' (or no refs) when it names none. The one reader of them. */
export function readChatLaunch(query: Record<string, string> | undefined): ChatLaunch {
  const q = query ?? {}
  const word = (key: string) => (typeof q[key] === 'string' ? q[key] : '')
  return {
    seed: word(LAUNCH_KEYS.seed),
    agent: word(LAUNCH_KEYS.agent),
    widgetAction: word(LAUNCH_KEYS.widgetAction),
    project: word(LAUNCH_KEYS.project),
    fromWidget: word(LAUNCH_KEYS.fromWidget) === '1',
    attach: [...new Set(word(LAUNCH_KEYS.attach).split(',').map((r) => r.trim()).filter(Boolean))],
  }
}

/** The address of a chat that starts from `launch`: `chat/<session>` when it names one, else a new
 *  chat (`chat/new`), with each key it names. The one writer of them: every page that opens a chat
 *  with something to start from builds its address here. `widgetAction` is written only when it is
 *  the staged turn's own nonce, so no caller can hand a chat a nonce the owner did not stage. */
export function chatLaunchPath(launch: Partial<ChatLaunch> & { session?: string }): string {
  // Each value written as the pages that built a chat's address by hand wrote it (`encodeURIComponent`,
  // a space as `%20`), so a draft's address reads as it did.
  const qs = new Map<string, string>()
  if (launch.seed) qs.set(LAUNCH_KEYS.seed, launch.seed)
  if (launch.agent) qs.set(LAUNCH_KEYS.agent, launch.agent)
  if (isStagedWidgetNonce(launch.widgetAction)) qs.set(LAUNCH_KEYS.widgetAction, launch.widgetAction)
  if (launch.project) qs.set(LAUNCH_KEYS.project, launch.project)
  if (launch.fromWidget) qs.set(LAUNCH_KEYS.fromWidget, '1')
  if (launch.attach?.length) qs.set(LAUNCH_KEYS.attach, launch.attach.join(','))
  const q = [...qs].map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
  const at = launch.session ? `chat/${encodeURIComponent(launch.session)}` : 'chat/new'
  return q ? `${at}?${q}` : at
}

/** `query` with `patch` applied, as the router's `setQuery` would write it. */
export function withLinkState(query: Record<string, string>, patch: Record<string, string | null>): Record<string, string> {
  const next = { ...query }
  for (const [k, v] of Object.entries(patch)) {
    if (v == null || v === '') delete next[k]
    else next[k] = v
  }
  return next
}
