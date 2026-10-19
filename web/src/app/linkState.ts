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

/** `query` with `patch` applied, as the router's `setQuery` would write it. */
export function withLinkState(query: Record<string, string>, patch: Record<string, string | null>): Record<string, string> {
  const next = { ...query }
  for (const [k, v] of Object.entries(patch)) {
    if (v == null || v === '') delete next[k]
    else next[k] = v
  }
  return next
}
