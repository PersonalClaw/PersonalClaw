import type { ProjectItem } from '../../lib/api'
import { NO_PROJECT, type ProjectThreads } from '../../lib/useThreads'
import { byRailOrder, type HistoryGroup } from '../../app/chatHistoryGroups'

/** ── The chats by project, on the Projects page (#5877) ──────────────────────────────────────────
 *
 *  The sidebar groups the chat history by the owner's folders or tags; the chats by project moved
 *  here, so no view is lost. Each project lists its chats, goal loops and code sessions, and the
 *  Projects page's Chats by project view lists every project's (`ChatsByProject`).
 *
 *  · **Groups follow the project list** (the built-in projects first, then by name, as it is
 *    served), each named by its project. Every active project is a group, an empty one included,
 *    so the view says a project is empty rather than leaving it out. An archived project is listed
 *    only while a row of it is, and a project the list does not hold (deleted since, or made since
 *    it was read) comes after the listed ones, named by its id.
 *  · **"No project" comes last**, after every project, and only when a row is in it.
 *  · **Rows within a group follow the sidebar's order** (`byRailOrder`).
 */

/** The name the group of rows with no project is shown under: the word the composer's project
 *  picker uses. */
export const NO_PROJECT_LABEL = 'No project'

/** The project list's fields the groups read. */
export type GroupedProject = Pick<ProjectItem, 'id' | 'name' | 'status'>

export function projectGroups(groups: readonly ProjectThreads[], projects: readonly GroupedProject[] | undefined): HistoryGroup[] {
  const byProject = new Map(groups.map((g) => [g.projectId, [...g.threads].sort(byRailOrder)]))
  const out: HistoryGroup[] = []
  const listed = new Set<string>()
  for (const p of projects ?? []) {
    if (!p || typeof p.id !== 'string' || p.id === NO_PROJECT || listed.has(p.id)) continue
    const threads = byProject.get(p.id) ?? []
    if (p.status === 'archived' && threads.length === 0) continue
    listed.add(p.id)
    out.push({ id: `project:${p.id}`, name: p.name || p.id, threads })
  }
  for (const g of groups) {
    if (g.projectId === NO_PROJECT || listed.has(g.projectId)) continue
    listed.add(g.projectId)
    out.push({ id: `project:${g.projectId}`, name: g.projectId, threads: byProject.get(g.projectId) ?? [] })
  }
  const loose = byProject.get(NO_PROJECT) ?? []
  if (loose.length > 0) out.push({ id: `project:${NO_PROJECT}`, name: NO_PROJECT_LABEL, threads: loose })
  return out
}
