import { StateMark } from '../../ui/StateMark'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { PIP } from '../../ui/ThreadRail'
import { withWeight } from '../../design/fontWeight'
import type { Thread, ThreadKind, ThreadsRead } from '../../lib/useThreads'

/** What each kind of thread is called on its row. */
const KIND: Record<ThreadKind, string> = { chat: 'Chat', loop: 'Goal loop', code: 'Code session' }

/** A project's own threads, in the order the sidebar lists them under it: its chats, goal loops and
 *  code sessions, each with the sidebar's mark (running, waiting on you, failed) and opening at its
 *  own address (#3814). The read is the sidebar's (`useThreads`), so the two never disagree. */
export function ProjectThreads({ projectId, read, onOpen }: {
  projectId: string
  read: ThreadsRead
  onOpen: (route: string) => void
}) {
  const { groups, loading, error, refresh } = read
  const threads = projectThreads(read, projectId)
  if (!groups.length && error) return <LoadError what="threads" error={error} onRetry={refresh} />
  if (!groups.length && loading) return <ListSkeleton rows={3} what="threads" />
  if (!threads.length) {
    return <p className="text-on-surface-low text-[0.8125rem]">No threads yet. A chat, goal loop or code session started in this project is listed here.</p>
  }
  return (
    <ul className="flex flex-col gap-1">
      {threads.map((t) => <ProjectThreadRow key={t.key} thread={t} onOpen={onOpen} />)}
    </ul>
  )
}

/** The threads *read* holds for *projectId*, none while it holds no group for it. */
export function projectThreads(read: Pick<ThreadsRead, 'groups'>, projectId: string): Thread[] {
  return read.groups.find((g) => g.projectId === projectId)?.threads ?? []
}

function ProjectThreadRow({ thread, onOpen }: { thread: Thread; onOpen: (route: string) => void }) {
  const pip = thread.pip ? PIP[thread.pip] : null
  return (
    <li>
      {/* Raw: a full-width list row in row ink, as the Work column's rows are; no primitive draws one. */}
      <button type="button" onClick={() => onOpen(thread.route)} title={thread.title}
        aria-label={`${KIND[thread.kind]}: ${thread.title}${pip ? `, ${pip.word}` : ''}`}
        className="flex w-full items-center gap-2 rounded-md bg-surface-high/60 px-2.5 py-1.5 text-left text-[0.8125rem] text-on-surface-var hover:text-on-surface"
        style={withWeight({ minHeight: 32 }, 400)}>
        <span className="shrink-0 text-on-surface-low text-[0.75rem]">{KIND[thread.kind]}</span>
        <span className="min-w-0 flex-1 truncate">{thread.title}</span>
        {pip && <span data-type="caption" className="shrink-0"><StateMark family={pip.family}>{pip.word}</StateMark></span>}
      </button>
    </li>
  )
}
