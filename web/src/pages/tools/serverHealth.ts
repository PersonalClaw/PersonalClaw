import type { McpServer } from '../../lib/api'

/** A tool server's state in words, with its tone and the gateway's reason: the Tools page's card
 *  says it, and so does a chat's session inspector (its Connectors section). */
export function serverHealth(s: McpServer): { state: string; tone: string; detail?: string } {
  // Before `enabled`: a server that waits is off until its Allow, which also switches it on.
  if (s.status === 'waiting') return { state: 'waiting for your Allow', tone: 'var(--color-warn)', detail: s.error }
  if (!s.enabled) return { state: 'disabled', tone: 'var(--color-on-surface-low)' }
  if (s.status === 'ready' || s.status === 'ok' || s.status === 'connected') return { state: 'ready', tone: 'var(--color-ok)' }
  // It failed to start again and again, and nothing starts it until Retry (`mcp_status.STOPPED`).
  if (s.status === 'stopped') return { state: 'failed — stopped retrying', tone: 'var(--color-danger)', detail: s.error }
  if (s.status === 'error') return { state: 'error', tone: 'var(--color-danger)', detail: s.error }
  if (s.status === 'unserved') return { state: "agents can't call it", tone: 'var(--color-warn)', detail: s.error }
  if (s.status === 'signin') return { state: 'sign-in needed', tone: 'var(--color-warn)', detail: s.error }
  // The gateway is checking it, after a change or for the first time: no result is shown for it
  // yet, because the last one was about something else (`mcp_discovery._get_cached`). Or it is
  // still starting, left to finish installing what it runs, and the gateway's sentence says so.
  if (s.status === 'probing') return { state: 'checking', tone: 'var(--color-on-surface-low)', detail: s.error || undefined }
  return { state: s.status || 'unknown', tone: 'var(--color-warn)', detail: s.error }
}
