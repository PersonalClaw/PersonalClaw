/** Bridge for the tool card's "Show full result" affordance (tool-io-rendering
 *  TC4). A projected tool result retains its raw in the per-session store; this
 *  asks the host (ChatPage, which knows the active session) to fetch + display
 *  the full result via /api/chat/sessions/{session}/tool-result/{rid}. Decoupled
 *  through an event so the leaf ToolCard needn't thread the session id. */

const EVENT = 'ne:tool-result-full'

export interface ToolResultFullRequest { rawRef: string; tool: string }

export function requestToolResultFull(rawRef: string, tool: string): void {
  window.dispatchEvent(new CustomEvent<ToolResultFullRequest>(EVENT, { detail: { rawRef, tool } }))
}

export function onToolResultFull(cb: (req: ToolResultFullRequest) => void): () => void {
  const h = (e: Event) => cb((e as CustomEvent<ToolResultFullRequest>).detail)
  window.addEventListener(EVENT, h)
  return () => window.removeEventListener(EVENT, h)
}
