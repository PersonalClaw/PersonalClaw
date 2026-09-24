import { useState } from 'react'
import { MessageCircleQuestion } from 'lucide-react'
import { IconButton } from './IconButton'
import { investigate } from '../lib/investigate'

/** The one shared investigate affordance (plan 60) — an icon button every entity
 *  row drops in with just `{kind, id}`. Opens a chat pre-loaded with the entity's
 *  server-composed context, in read-only `ask` mode. No surface builds its own
 *  "chat about this" wiring — this is it. */
export function InvestigateButton({ kind, id, backLink, size = 24, label }: {
  kind: string
  id: string
  /** Name override for a per-row instance: "Investigate in chat" alone repeats once per row (83
   *  times on `#/notifications`). Defaults to the bare verb for single-instance use. */
  label?: string
  backLink?: string
  size?: number
}) {
  const [busy, setBusy] = useState(false)
  return (
    <IconButton icon={MessageCircleQuestion} label={label ?? 'Investigate in chat'} title="Investigate in chat"
      // `loading`, not `disabled`: opening the chat is a fetch, and this one prop reaches nine
      // surfaces (inbox, loops ×2, notifications, schedule, audit, doctor, memory, tasks). Every
      // one of them used to dim this button to 40% not-allowed and announce `aria-disabled` for
      // the length of the round trip — "unavailable" for an action that was working.
      size={size} iconSize={Math.max(12, Math.round(size * 0.55))} loading={busy}
      onClick={(e) => {
        e.stopPropagation()
        setBusy(true)
        investigate(kind, id, backLink ? { backLink } : undefined)
          .catch(() => { /* the row stays; a failed open is a no-op */ })
          .finally(() => setBusy(false))
      }} />
  )
}
