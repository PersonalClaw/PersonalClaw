/** Context-aware routing for text-highlight comments.
 *
 *  Every <ContentSurface> can host the selection→comment layer, but WHERE a
 *  comment goes depends on the surface's context:
 *
 *   - Files/Artifacts page (no active conversation) → spawn a NEW chat session
 *     seeded with the feedback, then navigate to it.
 *   - A content panel open INSIDE an active chat (side panel / inline visual) →
 *     submit to THAT SAME session, so the agent the user is already talking to
 *     picks up the feedback in context.
 *   - A planning artifact during a loop's planning phase → submit to that loop's
 *     PLANNING agent/session.
 *
 *  The mount site constructs the right `CommentTarget`; the surface + comment
 *  layer stay context-agnostic and just call `target.submit(...)`.
 */
import { api } from '../../lib/api'

export interface CommentSubmission {
  /** The formatted feedback message (quoted passages + the user's instructions). */
  message: string
  /** Referenced document/file paths to attach as chat context (`meta.files`). */
  docPaths: string[]
}

export interface CommentTarget {
  /** Short human label for the composer's submit affordance ("Send to chat",
   *  "Send to this chat", "Send to planning"). */
  label: string
  submit: (s: CommentSubmission) => void | Promise<void>
}

/** Files/Artifacts page: comments open a FRESH chat session to address them. */
export function newSessionTarget(
  navigate: (path: string) => void,
  opts?: { name?: string },
): CommentTarget {
  return {
    label: 'Send to a new chat',
    submit: async ({ message, docPaths }) => {
      try {
        const session = await api.createChatSession({ name: opts?.name || 'Document comments' })
        const meta = docPaths.length ? { files: docPaths } : undefined
        await api.sendChat(message, session.key, meta)
        navigate(`chat/${session.key}`)
      } catch {
        navigate('chat/new')
      }
    },
  }
}

/** A content panel open inside an active chat: comments go to THAT session so
 *  the ongoing conversation absorbs the feedback. The host supplies how to
 *  inject into its own session (it already owns the send path + optimistic UI). */
export function sameSessionTarget(
  send: (message: string, docPaths: string[]) => void | Promise<void>,
): CommentTarget {
  return { label: 'Send to this chat', submit: ({ message, docPaths }) => send(message, docPaths) }
}

/** A loop's planning artifact: comments go to the loop's planning agent/session
 *  so the planner revises the plan with the feedback. The host wires `send` to
 *  the loop's planning conversation. */
export function planningTarget(
  send: (message: string, docPaths: string[]) => void | Promise<void>,
): CommentTarget {
  return { label: 'Send to planning', submit: ({ message, docPaths }) => send(message, docPaths) }
}
