import { MessageCircle } from 'lucide-react'
import type { ChatSessionSummary } from '../../lib/api'
import { StatusPill } from '../../ui/StatusPill'

/** Which chat channel a chat came in on — named wherever your chats are listed.
 *
 *  A message on a chat channel (Telegram, Slack, …) starts a chat through the channel door, and
 *  that chat sits in your history. The list serves it with `origin: 'channel'` and the channel's
 *  name as `source_label`, so the history files it under Channels; every row that lists it says
 *  which channel it came from, in these words. */
type ChannelChat = Pick<ChatSessionSummary, 'origin' | 'source_label'>

/** The name of the channel *s* came in on, or `''` for a chat that did not come in on one. */
export function channelOf(s: ChannelChat): string {
  return s.origin === 'channel' ? (s.source_label || 'a chat channel').trim() : ''
}

/** "From <channel>" on a row that lists a chat. Provenance, not a control, so a span. Renders
 *  nothing for a chat that did not come in on a channel. */
export function FromChannel({ s }: { s: ChannelChat }) {
  const name = channelOf(s)
  if (!name) return null
  // Neutral, like "Started by <App>": where a chat came from is not a verdict.
  return (
    <StatusPill tone="neutral" title={`This chat came in on ${name}.`}
      className="min-w-0 max-w-[10rem] gap-xs h-[18px] cursor-default">
      <MessageCircle size={10} className="shrink-0" aria-hidden />
      <span className="truncate">From {name}</span>
    </StatusPill>
  )
}
