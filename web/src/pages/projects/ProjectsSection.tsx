import type { RouteProps } from '../../app/useQueryState'
import { ProjectListPage } from './ProjectList'
import { ProjectPage } from './ProjectPage'

/** Projects: the list of every project, and one project's page.
 *    #/projects        → the list (`ProjectList`)
 *    #/projects/<id>   → the project's page (`ProjectPage`): a box to start a chat in it, then what
 *                        needs you, what is running, what comes next, its tasks and the rest, with
 *                        About this project beside
 *    #/projects/<id>?tab=tasks → its Tasks view (#3818): the task list and board, held to it, with
 *                        the keys `#/tasks` reads (view, filter, q, sort, list, tag, open)
 *    #/projects/<id>?rename=1 → its title in edit mode, from a list row's Rename: dropped at once with
 *                        a replace, and the editor opened when the project arrives (a built-in's
 *                        page ignores it)
 */
export function ProjectsSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  const seg = (sub || '').split('/')[0]
  if (seg) return <ProjectPage key={seg} id={seg} navigate={navigate} query={query} setQuery={setQuery} navEpoch={navEpoch} />
  return <ProjectListPage navigate={navigate} onOpenTasks={() => navigate('tasks')} query={query} setQuery={setQuery} />
}
