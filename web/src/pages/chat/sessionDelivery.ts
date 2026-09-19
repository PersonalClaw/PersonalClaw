/** THE session-identity resolution for anything the server delivers into an open
 *  chat — WebSocket frames and send-response payloads alike.
 *
 *  Why one function rather than a comparison at each delivery site: agent routing's
 *  first-message suggestion (issue 569) was never surfaced because its ONE transport
 *  could not reach the page. `/api/chat` broadcasts `routing_suggestion` synchronously,
 *  before the run task's first await — so it is the earliest frame of a send — while
 *  creating a session BY that send re-keys `ChatSession` (`new-<epoch>` → the session
 *  key). The remount closes the ChatPage socket and its replacement is still
 *  handshaking, so the frame is delivered to no ChatPage socket at all. The fix adds a
 *  second transport (the send response, which is causally after the request and so
 *  cannot be raced), and a second transport is exactly where a second, subtly
 *  different notion of "is this mine?" gets invented. So identity is resolved here,
 *  once, and every path calls it.
 *
 *  The server owns session identity: it stamps the session on the payload. This side
 *  only decides whether the named session is the one on screen — so a payload is
 *  either shown for the session it names, or deliberately not shown at all. It can
 *  never be applied to a different session.
 */

/** Whether a server payload naming `named` may be applied to the open session `open`.
 *
 *  `named === undefined` means "not session-scoped" — approval, voice and side-chat
 *  frames are keyed by id, not by session, and are deliverable whenever a session is
 *  open. Anything else must match exactly. No open session → nothing is deliverable.
 */
export function deliverableToOpenSession(named: unknown, open: string | null): boolean {
  if (!open) return false
  if (named === undefined) return true
  return named === open
}
