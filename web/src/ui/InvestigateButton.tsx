import { useState } from 'react'
import { MessageCircleQuestion } from 'lucide-react'
import { IconButton } from './IconButton'
import { investigate } from '../lib/investigate'

/** The one shared investigate affordance (plan 60) — an icon button every entity
 *  row drops in with just `{kind, id}`. Opens a chat pre-loaded with the entity's
 *  server-composed context, in read-only `ask` mode. No surface builds its own
 *  "chat about this" wiring — this is it. */
export function InvestigateButton({ kind, id, backLink, size = 24 }: {
  kind: string
  id: string
  backLink?: string
  size?: number
}) {
  const [busy, setBusy] = useState(false)
  return (
    <IconButton icon={MessageCircleQuestion} label="Investigate in chat"
      size={size} iconSize={Math.max(12, Math.round(size * 0.55))} disabled={busy}
      onClick={(e) => {
        e.stopPropagation()
        setBusy(true)
        investigate(kind, id, backLink ? { backLink } : undefined)
          .catch(() => { /* the row stays; a failed open is a no-op */ })
          .finally(() => setBusy(false))
      }} />
  )
}
