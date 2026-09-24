import type { WsMessage } from '../../lib/useChatSocket'

/** Regex-escape a slug before it goes into a boundary pattern. */
function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** The `slug` argument of a structured tool input, or null when the frame carries
 *  no dict input (ACP providers hand over a stringified blob instead). */
function slugFrom(v: unknown): string | null {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null
  const s = (v as Record<string, unknown>).slug
  return typeof s === 'string' && s ? s : null
}

/** AE-10 — does this WS envelope report that the agent just wrote a new version
 *  of `slug`?
 *
 *  The iterate panel is a `ChatEmbed`: a separate document in a sandboxed iframe
 *  with no postMessage bridge, so the host page cannot learn from the embed that
 *  `artifact_update` landed. It learns from the socket instead, off the
 *  **existing** `tool_call` envelope the chat runner already broadcasts for every
 *  tool call (`dashboard/chat_runner.py` → `broadcast_ws("tool_call", …)`, and
 *  again with `update: True` once the args resolve). That frame already carries
 *  the tool name and its input, which is the whole signal — so this adds no WS
 *  event, no new field, and no widening of what the frame means. It is a pure
 *  read of a stream `ChatPage` has consumed since it shipped.
 *
 *  The slug check keeps a busy socket from refetching an unrelated artifact: an
 *  `artifact_update` on another slug is not this view's business.
 */
export function isArtifactUpdateFor(m: WsMessage, slug: string): boolean {
  if (!slug) return false
  if (m.type !== 'tool_call') return false
  const data = (m.data ?? {}) as Record<string, unknown>
  if (String(data.tool ?? '') !== 'artifact_update') return false
  // The structured input is authoritative when present. `input` is the native
  // provider's redacted arg dict; the later `update: True` frame puts the same
  // dict on `input_preview`, so both shapes are read for it.
  const named = slugFrom(data.input) ?? slugFrom(data.input_preview)
  if (named) return named === slug
  const preview = typeof data.input_preview === 'string' ? data.input_preview : ''
  if (preview) {
    // Stringified args: name the slug inside the blob. Boundaried, or `sales-dash`
    // would match `sales-dashboard` and refresh the wrong artifact.
    return new RegExp(`(^|[^A-Za-z0-9_-])${escapeRe(slug)}([^A-Za-z0-9_-]|$)`).test(preview)
  }
  // Neither form named a slug — an initially-empty `tool_call` frame, which is
  // exactly what agents emit before streaming the real args. Refresh: a spurious
  // refetch costs one GET, a missed one leaves a stale body on screen, which is
  // the defect this closes.
  return true
}
