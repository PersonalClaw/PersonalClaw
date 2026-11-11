import { useChatList } from '../../../app/useChatList'
import { LoadError } from '../../../ui/ListScaffold'
import { ThreadRail } from '../../../ui/ThreadRail'

/** Body of the new chat's Chat history panel: the sidebar's chat list (`ThreadRail`, one list for
 *  both), with its own search and its own Sort and filter view, opening at the defaults and kept
 *  nowhere, so a filter used here once never changes the sidebar or ⌘1 to ⌘9. It reads what the
 *  sidebar reads (`useChatList`), so the two never say different things about the same chats, and
 *  it lists goal loops, code sessions and channel chats as the sidebar does, kept live by the same
 *  frames. No New chat: the page it opens on is one. Its expand control opens the Chat history page
 *  (`ChatPanes`).
 *
 *  Connected with nothing in hand, a failed chats read is the honest-state `LoadError`, with the
 *  archive's reader still mounted; a failed archive read is the list's own failure row, "Couldn't
 *  load your archived chats.", beside Retry and the menu. Otherwise the list says Offline, or shows
 *  the failure row over the rows it has. */
export function ChatHistorySidePanelBody({ onOpen }: { onOpen: (route: string) => void }) {
  const list = useChatList('panel')
  if (list.status === 'failed' && list.all.length === 0 && list.chatsError) {
    return <>{list.archive}<LoadError what="chats" error={list.chatsError} onRetry={list.retry} /></>
  }
  return (
    <>
      {list.archive}
      <ThreadRail surface="panel" label="Chats" rows={list.rows} selectedKey="" status={list.status}
        failure={list.failure} onOpen={onOpen} onRetry={list.retry}
        menu={list.sections} filtered={list.filtered} onClearFilters={list.clearFilters}
        markArchived={list.view.status === 'all'} />
    </>
  )
}
