import { useCallback, useLayoutEffect, useMemo } from 'react'
import { api, type ProjectItem } from '../lib/api'
import { useQuery } from '../lib/data'
import { useGatewayConnection } from './gatewayConnection'
import { NO_PROJECT, readThreads, toThreads, useThreads, type ProjectThreads, type Thread, type ThreadSources } from '../lib/useThreads'
import { ThreadRail, railSections, readGroupsOpen, shownThreads, type ThreadRailGroup, type ThreadRailStatus } from '../ui/ThreadRail'

/** ── The sidebar's threads, and which one is open (#3814) ───────────────────────────────────────
 *
 *  `useThreads` joins the chats and loops under their projects; this names and orders the groups
 *  the rail shows, and reads which thread the address opens.
 *
 *  · **Groups follow the project list** (the built-in projects first, then by name, as it is
 *    served), each named by its project. Every active project is a group, an empty one included,
 *    so the rail says a project is empty rather than leaving it out. An archived project is listed
 *    only while a thread of it is, and a project the list does not hold (deleted since, or made
 *    since it was read) comes after the listed ones, named by its id.
 *  · **"No project" comes last**, after every project, and only when a thread is in it.
 *  · **Threads within a group follow one named order** (`byRailOrder`): pinned first, then the
 *    latest activity, newest first, ties broken by the thread's key. Status never moves a row.
 *  · **⌘1 to ⌘9 open the rows the rail shows**, top to bottom (`threadNumberRoute`).
 *  · **The open thread is the address.** A thread keeps the address of its kind (`#/chat/<key>`,
 *    `#/loops/<id>`, `#/code/<id>`, a run-backed loop's `#/workflows/runs/<run>`), the issue's
 *    default for the thread address, so every address that opened a thread before still opens it
 *    and the rail keeps no selection of its own: reload, Back, Forward and a copied address light
 *    the thread the page shows.
 */

/** The name the group of threads with no project is shown under: the word the composer's project
 *  picker uses. */
export const NO_PROJECT_LABEL = 'No project'

/** The project list's fields the groups read. */
export type RailProject = Pick<ProjectItem, 'id' | 'name' | 'status'>

/** The sidebar's one order for the threads of a group, chats, goal loops and code sessions alike:
 *  pinned first, then the latest activity, newest first (`Thread.activeAt`), with ties broken by the
 *  thread's key. Status never moves a row: the pips say what needs the owner and ⌘⌥A jumps to it,
 *  so a row does not jump when a turn starts or a run fails. The loops page's "running first" and the
 *  code section's status tiers do not carry into the sidebar. */
export function byRailOrder(a: Thread, b: Thread): number {
  return Number(b.pinned) - Number(a.pinned)
    || b.activeAt - a.activeAt
    || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)
}

export function railGroups(groups: readonly ProjectThreads[], projects: readonly RailProject[] | undefined): ThreadRailGroup[] {
  const byProject = new Map(groups.map((g) => [g.projectId, [...g.threads].sort(byRailOrder)]))
  const out: ThreadRailGroup[] = []
  const listed = new Set<string>()
  for (const p of projects ?? []) {
    if (!p || typeof p.id !== 'string' || p.id === NO_PROJECT || listed.has(p.id)) continue
    const threads = byProject.get(p.id) ?? []
    if (p.status === 'archived' && threads.length === 0) continue
    listed.add(p.id)
    out.push({ projectId: p.id, name: p.name || p.id, threads })
  }
  for (const g of groups) {
    if (g.projectId === NO_PROJECT || listed.has(g.projectId)) continue
    listed.add(g.projectId)
    out.push({ projectId: g.projectId, name: g.projectId, threads: byProject.get(g.projectId) ?? [] })
  }
  const loose = byProject.get(NO_PROJECT) ?? []
  if (loose.length > 0) out.push({ projectId: NO_PROJECT, name: NO_PROJECT_LABEL, threads: loose })
  return out
}

function decoded(route: string): string {
  return route.split('/').map((s) => { try { return decodeURIComponent(s) } catch { return s } }).join('/')
}

/** The key of the listed thread the address `#/<route>/<sub>` opens, else ''. `sub` arrives
 *  decoded, as `useHashRoute` gives it. A loop or code session is also open on its own sub-pages,
 *  and a code session at the loop address it had before; a chat only at its own address, so a
 *  chat whose key extends another's never lights the other. */
export function threadAt(route: string, sub: string, groups: readonly { threads: readonly Thread[] }[]): string {
  const all = groups.flatMap((g) => g.threads)
  const path = [route, sub].filter(Boolean).join('/')
  const head = sub.split('/')[0] ?? ''
  if ((route === 'loops' || route === 'code') && head !== '') {
    const loop = all.find((t) => t.kind !== 'chat' && t.id === head)
    if (loop) return loop.key
  }
  const exact = all.find((t) => decoded(t.route) === path)
  if (exact) return exact.key
  const within = all.find((t) => t.kind !== 'chat' && path.startsWith(`${decoded(t.route)}/`))
  return within ? within.key : ''
}

/** The threads ⌘1 to ⌘9 count while no rail shows them, as the expanded rail would with no search:
 *  the groups in their order, each group's threads in the named order, the threads of a group the
 *  owner closed (`open`, as stored) left out. Pure, over the lists the rail reads. */
export function numberedThreads(src: ThreadSources, projects: readonly RailProject[] | undefined, open: Record<string, boolean>): Thread[] {
  return shownThreads(railSections(railGroups(toThreads(src), projects), open, ''))
}

/** The rows the mounted rail shows, top to bottom, or null while no rail shows any it has read. */
let shownByTheRail: readonly Thread[] | null = null

/** The route of thread *n* (1 to 9) of the sidebar, or null when it shows fewer. The rows the rail
 *  shows when one is mounted (its closed groups and its search applied), so ⌘n opens the nth row
 *  the owner sees; else, with the rail collapsed to icons or not up, the same rows read fresh. A
 *  failed read rejects. */
export async function threadNumberRoute(n: number): Promise<string | null> {
  let rows = shownByTheRail
  if (rows === null) {
    const [lists, projects] = await Promise.all([readThreads(), api.projects()])
    rows = numberedThreads(lists, projects, readGroupsOpen())
  }
  return rows[n - 1]?.route ?? null
}

export interface ThreadRailRead {
  groups: ThreadRailGroup[]
  /** The listed thread the address opens, else ''. */
  selectedKey: string
  status: ThreadRailStatus
  /** What failed, as the rail says it, when `status` is 'failed'. */
  failure: string
  /** The failed read's error (the threads', else the projects'), or null. */
  error: unknown
  retry: () => void
}

/** The rail's groups and its open thread, for the shell at `#/<route>/<sub>`. The project list is
 *  read under the key the Projects page reads it by, so the two share one read. */
export function useThreadRail(route: string, sub: string): ThreadRailRead {
  const threads = useThreads()
  const { data: projects, loading: projectsLoading, error: projectsError, refresh: refreshProjects } = useQuery(
    'projects:list', () => api.projects(), { persist: true },
  )
  const groups = useMemo(() => railGroups(threads.groups, projects), [threads.groups, projects])
  const selectedKey = threadAt(route, sub, groups)
  // The gateway not answering at all is said as itself: a read that fails because the gateway is
  // away is not a fault of that read, and the threads it last listed stay on screen.
  const { state: connection, check: checkConnection } = useGatewayConnection()
  const offline = connection === 'disconnected'
  const failure = threads.error ? "Couldn't load your threads." : projectsError ? "Couldn't load your projects." : ''
  const status: ThreadRailStatus = offline ? 'offline' : failure ? 'failed' : threads.loading || projectsLoading ? 'loading' : 'ready'
  const { refresh: refreshThreads } = threads
  const retry = useCallback(() => { checkConnection(); refreshThreads(); refreshProjects() }, [checkConnection, refreshThreads, refreshProjects])
  return { groups, selectedKey, status, failure, error: threads.error || projectsError || null, retry }
}

/** The rail's threads as the shell mounts them, in the `threads` slot of `NavRail`. A component of
 *  its own, so the lists are read only while a rail that shows them is mounted: not in the
 *  collapsed rail, an embedded page, the companion or setup. `onLit` tells the shell whether a
 *  thread is lit, so the destination its route belongs to is not lit beside it. */
export function ShellThreadRail({ route, sub, onOpen, onLit }: {
  route: string
  sub: string
  onOpen: (route: string) => void
  /** Called with whether a thread is lit; with false when the rail unmounts. Keep it stable. */
  onLit: (lit: boolean) => void
}) {
  const rail = useThreadRail(route, sub)
  const lit = rail.selectedKey !== ''
  useLayoutEffect(() => { onLit(lit) }, [lit, onLit])
  useLayoutEffect(() => () => onLit(false), [onLit])
  const onShown = useCallback((rows: Thread[] | null) => { shownByTheRail = rows }, [])
  useLayoutEffect(() => () => { shownByTheRail = null }, [])
  return (
    <ThreadRail groups={rail.groups} selectedKey={rail.selectedKey} status={rail.status}
      failure={rail.failure} onOpen={onOpen} onRetry={rail.retry}
      onNewThread={() => onOpen('chat/new')} onShown={onShown} />
  )
}
