import { Edit3, MessageSquare } from 'lucide-react'
import { Button } from '../../ui/Button'
import { EmptyState } from '../../ui/ListScaffold'

/** The route names a chat that does not exist — a dead deep link, or a chat deleted while it
 *  was open. Said plainly, with the two ways forward, instead of an empty chat whose composer
 *  takes a message the server refuses and does not save. A draft that was typed (or sent and
 *  refused) is not dropped: "Start a new chat" carries it into the new chat's composer through
 *  the same `?seed=` pre-fill every other "start a chat with this" launch uses. */
export function MissingChat({ draft, fromWidget = false, navigate }: {
  draft: string
  /** The draft is a widget's message: the new chat's composer is told so (`from_widget=1`), and
   *  what is sent from it grants none of its links. Safe in a URL, since it can only take a grant
   *  away. */
  fromWidget?: boolean
  navigate: (p: string, opts?: { replace?: boolean }) => void
}) {
  const keep = draft.trim()
  const marked = fromWidget ? '&from_widget=1' : ''
  return (
    <div className="flex h-full flex-col items-center justify-center gap-s overflow-y-auto px-l">
      <EmptyState icon={MessageSquare} title="This chat doesn’t exist"
        hint={keep
          ? 'It may have been deleted, or the link is out of date. Your message was not sent — it will be waiting in the new chat.'
          : 'It may have been deleted, or the link is out of date.'}
        action={{
          label: 'Start a new chat', icon: Edit3,
          onClick: () => navigate(keep ? `chat/new?seed=${encodeURIComponent(keep)}${marked}` : 'chat/new', { replace: true }),
        }} />
      <Button variant="ghost" size="sm" onClick={() => navigate('chat/history')}>See all chats</Button>
    </div>
  )
}
