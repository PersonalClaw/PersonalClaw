import { Edit3, MessageSquare } from 'lucide-react'
import { Button } from '../../ui/Button'
import { EmptyState } from '../../ui/ListScaffold'

/** The route names a chat that does not exist — a dead deep link, or a chat deleted while it
 *  was open. Said plainly, with the two ways forward, instead of an empty chat whose composer
 *  takes a message the server refuses and does not save. A draft that was typed (or sent and
 *  refused) is not dropped: "Start a new chat" carries it into the new chat's composer through
 *  the same `?seed=` pre-fill every other "start a chat with this" launch uses. */
export function MissingChat({ draft, navigate }: { draft: string; navigate: (p: string, opts?: { replace?: boolean }) => void }) {
  const keep = draft.trim()
  return (
    <div className="flex h-full flex-col items-center justify-center gap-s overflow-y-auto px-l">
      <EmptyState icon={MessageSquare} title="This chat doesn’t exist"
        hint={keep
          ? 'It may have been deleted, or the link is out of date. Your message was not sent — it will be waiting in the new chat.'
          : 'It may have been deleted, or the link is out of date.'}
        action={{
          label: 'Start a new chat', icon: Edit3,
          onClick: () => navigate(keep ? `chat/new?seed=${encodeURIComponent(keep)}` : 'chat/new', { replace: true }),
        }} />
      <Button variant="ghost" size="sm" onClick={() => navigate('chat/history')}>See all chats</Button>
    </div>
  )
}
