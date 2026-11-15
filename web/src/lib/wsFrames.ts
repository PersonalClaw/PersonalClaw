/** The socket's frames as a reader reads them, apart from the hook that opens the socket
 *  (`useChatSocket`), so a component reads a frame by the one rule whatever a test does with the
 *  hook. */

export interface WsMessage { type: string; data: Record<string, unknown> }

/** The kinds a `refresh` frame names (`DashboardState.push_refresh`: `loops`, `crons`,
 *  `workflow_runs`, …), or `[]` for any other frame. The gateway's listing-refresh hint: a page
 *  re-reads the listings it names instead of polling them. */
export function refreshKinds(m: WsMessage): string[] {
  const kinds = m.type === 'refresh' ? m.data?.kinds : undefined
  return Array.isArray(kinds) ? kinds.map(String) : []
}
