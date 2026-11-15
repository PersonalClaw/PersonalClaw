import { useMemo, useState } from 'react'
import { MessageSquare, Search } from 'lucide-react'
import { useThreads, type Thread } from '../../lib/useThreads'
import { rowOrder } from '../../app/chatListView'
import { searched } from '../../ui/ThreadRail'
import { Modal } from '../../ui/Modal'
import { MenuRow } from '../../ui/Popover'
import { SearchField } from '../../ui/SearchField'
import { ResultAnnouncement } from '../../ui/ListControls'
import { EmptyState, LoadError } from '../../ui/ListScaffold'

/** The chats a picker lists: the chats the sidebar lists (not goal loops or code sessions, and not
 *  an archived chat), in its Recent activity order (`chatListView.rowOrder('activity')`), pinned
 *  first. */
export function pickableChats(groups: readonly { threads: readonly Thread[] }[]): Thread[] {
  return groups.flatMap((g) => g.threads)
    .filter((t) => t.kind === 'chat' && !t.archived)
    .sort(rowOrder('activity'))
}

/** Pick a chat: what "Attach to another chat…" opens. The chats come from the sidebar's own read
 *  (`useThreads`), in its order, and the search matches a chat by name as the sidebar's search
 *  does (`ThreadRail.searched`), so a chat is found here where it is found there. */
export function ChatPicker({ title = 'Attach to another chat', onPick, onClose }: {
  title?: string
  /** The chat picked, by its key; the picker closes after. */
  onPick: (key: string) => void
  onClose: () => void
}) {
  const { groups, loading, error, refresh } = useThreads()
  const [q, setQ] = useState('')
  const chats = useMemo(() => pickableChats(groups), [groups])
  const shown = useMemo(() => searched(chats, q), [chats, q])
  const words = q.trim()
  return (
    <Modal title={title} icon={<MessageSquare size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-m">
        <SearchField value={q} onChange={setQ} autoFocus placeholder="Search chats" ariaLabel="Search chats" />
        <ResultAnnouncement count={shown.length} noun="chats" active={!!words && !loading} />
        {error && chats.length === 0 ? (
          <LoadError what="chats" error={error} onRetry={refresh} />
        ) : !loading && chats.length === 0 ? (
          <p data-type="body-s" className="text-on-surface-low">No chats yet.</p>
        ) : (
          <div className="flex max-h-80 flex-col gap-1 overflow-y-auto">
            {shown.map((t) => (
              <MenuRow key={t.key} icon={<MessageSquare size={14} />} label={t.title || 'Untitled chat'}
                onClick={() => { onPick(t.id); onClose() }} />
            ))}
            {words && !loading && shown.length === 0 && (
              <EmptyState icon={Search} title={`No chats match “${words}”`}
                hint={`You have ${chats.length} chat${chats.length === 1 ? '' : 's'} — just none matching the search.`}
                action={{ label: 'Clear search', onClick: () => setQ('') }} />
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}
