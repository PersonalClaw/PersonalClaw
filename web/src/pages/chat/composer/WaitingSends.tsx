import { CloudOff, PencilLine } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { clockTime } from '../../../lib/epoch'
import { isStale, type OutboxItem } from '../../../lib/outbox'

// ── What waits for PersonalClaw, and the draft kept from before (#4012) ───────────────────────
//
// A send the gateway could not take shows here as "Waiting to send" until it goes, by itself, when
// the chat's socket is open again. One that has waited a day asks first: Send now, or Remove. A
// draft restored from this browser says so, with Discard draft. Every row's controls are reached by
// Tab, and at a phone's width a row's words and its controls stack.

export function WaitingSends({ items, onSendNow, onRemove }: {
  items: OutboxItem[]
  onSendNow: () => void
  onRemove: (clientMessageId: string) => void
}) {
  if (!items.length) return null
  return (
    <div role="group" aria-label="Waiting to send" className="mb-2 flex flex-col gap-1">
      {items.map((item) => {
        const stale = isStale(item)
        return (
          <div key={item.client_message_id} data-type="caption"
            className="flex flex-col gap-1 rounded-md bg-surface-high px-2.5 py-1.5 text-on-surface-var sm:flex-row sm:items-center sm:gap-2">
            <span className="flex min-w-0 flex-1 items-center gap-1.5">
              <CloudOff size={13} aria-hidden className="shrink-0" />
              <span className="min-w-0 truncate text-on-surface">{item.message}</span>
            </span>
            <span className="flex shrink-0 items-center gap-1.5">
              {stale
                ? <span>Waiting since {clockTime(new Date(item.held_at).toISOString())}. Send now or remove it.</span>
                : <span>Waiting to send</span>}
              {stale && <Button size="xs" variant="ghost" onClick={onSendNow}>Send now</Button>}
              <Button size="xs" variant="ghost" ariaLabel={`Remove “${item.message.slice(0, 40)}” from waiting to send`}
                onClick={() => onRemove(item.client_message_id)}>Remove</Button>
            </span>
          </div>
        )
      })}
    </div>
  )
}

export function KeptDraftLine({ onDiscard }: { onDiscard: () => void }) {
  return (
    <div data-type="caption" className="mb-2 flex items-center gap-1.5 text-on-surface-low">
      <PencilLine size={13} aria-hidden className="shrink-0" />
      <span>Your draft from before is back.</span>
      <Button size="xs" variant="ghost" onClick={onDiscard}>Discard draft</Button>
    </div>
  )
}
