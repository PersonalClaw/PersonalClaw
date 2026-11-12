import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { PIP } from '../../ui/ThreadRail'
import { OrbMark } from '../../ui/orb/OrbMark'
import { Tooltip } from '../../ui/Tooltip'
import { ORB_STATE_OF_PIP } from '../../lib/orbState'
import { withWeight } from '../../design/fontWeight'
import type { Thread, ThreadKind, ThreadsRead } from '../../lib/useThreads'

/** What each kind of thread is called on its row. */
const KIND: Record<ThreadKind, string> = { chat: 'Chat', loop: 'Goal loop', code: 'Code session' }

/** A project's own chats, in the sidebar's order: its chats, goal loops and code sessions, each with
 *  the sidebar's mark (running, waiting on you, failed) and opening at its own address (#3814). The
 *  read is the sidebar's (`useThreads`), so the two never disagree. */
export function ProjectThreads({ projectId, read, onOpen }: {
  projectId: string
  read: ThreadsRead
  onOpen: (route: string) => void
}) {
  const { groups, loading, error, refresh } = read
  const threads = projectThreads(read, projectId)
  if (!groups.length && error) return <LoadError what="chats" error={error} onRetry={refresh} />
  if (!groups.length && loading) return <ListSkeleton rows={3} what="chats" />
  if (!threads.length) {
    return <p className="text-on-surface-low text-[0.8125rem]">No chats yet. A chat, goal loop or code session started in this project is listed here.</p>
  }
  return (
    <ul className="flex flex-col gap-1">
      {threads.map((t) => <ProjectThreadRow key={t.key} thread={t} onOpen={onOpen} />)}
    </ul>
  )
}

/** The rows *read* holds for *projectId*, none while it holds no group for it. */
export function projectThreads(read: Pick<ThreadsRead, 'groups'>, projectId: string): Thread[] {
  return read.groups.find((g) => g.projectId === projectId)?.threads ?? []
}

/** One chat, goal loop or code session of a project, as its hub and the Chats by project view list
 *  it: its kind, its name and its mark. */
export function ProjectThreadRow({ thread, onOpen }: { thread: Thread; onOpen: (route: string) => void }) {
  const pip = thread.pip ? PIP[thread.pip] : null
  return (
    <li>
      {/* Raw: a full-width list row in row ink, as the Work column's rows are; no primitive draws one.
          Its tooltip, the app's own, is its whole title, which its name already says: it shows only
          once the title is cut short, beside the row, or on its own row where neither side has room,
          so it covers no other row of its list. */}
      <Tooltip text={thread.title} describes={false} side="end" shows={titleIsCut}>{(trigger) => (
        <button type="button" onClick={() => onOpen(thread.route)} {...trigger}
          aria-label={`${KIND[thread.kind]}: ${thread.title}${pip ? `, ${pip.word}` : ''}`}
          className="flex w-full items-center gap-2 rounded-md bg-surface-high/60 px-2.5 py-1.5 text-left text-[0.8125rem] text-on-surface-var hover:text-on-surface"
          style={withWeight({ minHeight: 32 }, 400)}>
          <span className="shrink-0 text-on-surface-low text-[0.75rem]">{KIND[thread.kind]}</span>
          <span className="min-w-0 flex-1 truncate">{thread.title}</span>
          {pip && thread.pip && (
            <span data-type="caption" className="shrink-0 text-on-surface">
              {/* The sidebar's mark for the pip, in the family's shared state token (`ui/orb/OrbMark.tsx`). */}
              <OrbMark state={ORB_STATE_OF_PIP[thread.pip].state} badge={ORB_STATE_OF_PIP[thread.pip].badge} tone={`--color-state-${pip.family}`} family={pip.family}>{pip.word}</OrbMark>
            </span>
          )}
        </button>
      )}</Tooltip>
    </li>
  )
}

/** Whether a row's title is cut short, so its tooltip says something the row does not show. */
function titleIsCut(row: HTMLElement): boolean {
  const title = row.querySelector<HTMLElement>('.truncate')
  return title !== null && title.scrollWidth > title.clientWidth
}
