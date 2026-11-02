import { MessageSquare, Search } from 'lucide-react'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import type { ThreadsRead } from '../../lib/useThreads'
import { ProjectThreadRow } from './ProjectThreads'
import { projectGroups, type GroupedProject } from './projectChatGroups'
import type { HistoryGroup } from '../../app/chatHistoryGroups'
import type { ProjectThreads } from '../../lib/useThreads'

/** The groups the view shows for *query*: every project's (`projectGroups`), or while a search is
 *  typed, only the rows whose name holds it, in the projects that have one. */
export function shownChatGroups(
  groups: readonly ProjectThreads[], projects: readonly GroupedProject[] | undefined, query: string,
): HistoryGroup[] {
  const words = query.trim().toLocaleLowerCase()
  const all = projectGroups(groups, projects)
  if (words === '') return all
  return all
    .map((g) => ({ ...g, threads: g.threads.filter((t) => t.title.toLocaleLowerCase().includes(words)) }))
    .filter((g) => g.threads.length > 0)
}

/** The Projects page's Chats by project view (`#/projects?view=chats`, #5877): every project with
 *  its chats, goal loops and code sessions, in the sidebar's order, "No project" last. The sidebar
 *  groups the chat history by the owner's folders or tags, so the view by project lives here. The
 *  read is the sidebar's (`useThreads`), so the two never disagree, and the page's search finds a
 *  row by its name (`shownChatGroups`, which the page also counts its matches by). */
export function ChatsByProject({ read, groups, searching, onOpen }: {
  read: ThreadsRead
  /** The groups to show, as `shownChatGroups` makes them. */
  groups: readonly HistoryGroup[]
  /** Whether a search is typed, so an empty view says nothing matched. */
  searching: boolean
  onOpen: (route: string) => void
}) {

  if (!read.groups.length && read.error) return <LoadError what="chats" error={read.error} onRetry={read.refresh} />
  if (!read.groups.length && read.loading) return <ListSkeleton rows={5} what="chats" />
  if (groups.length === 0) {
    return searching
      ? <EmptyState icon={Search} title="No matching chats" hint="Try a different term." />
      : <EmptyState icon={MessageSquare} title="No chats yet" hint="A chat, goal loop or code session started in a project is listed under it here." />
  }
  return (
    <div className="flex flex-col gap-l">
      {groups.map((g) => {
        const heading = `chats-by-project-${g.id}`
        return (
          <section key={g.id} aria-labelledby={heading} className="flex flex-col gap-xs">
            <h2 id={heading} data-type="label-l" className="text-on-surface">
              {g.name} <span className="text-on-surface-low">· {g.threads.length}</span>
            </h2>
            {g.threads.length === 0
              ? <p data-type="body-s" className="text-on-surface-low">No chats yet</p>
              : <ul className="flex flex-col gap-1">{g.threads.map((t) => <ProjectThreadRow key={t.key} thread={t} onOpen={onOpen} />)}</ul>}
          </section>
        )
      })}
    </div>
  )
}
