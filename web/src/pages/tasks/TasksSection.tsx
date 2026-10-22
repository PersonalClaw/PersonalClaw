import { TasksListPage } from './TasksListPage'
import { TaskCreatePage } from './TaskCreatePage'
import { useEditFlag, type RouteProps } from '../../app/useQueryState'

/** Tasks navigation — fully URL-addressable:
 *    #/tasks                       → All tasks: every project's (view/filter/search/sort/scope/list/tag/open via ?query)
 *    #/tasks?view=board&filter=open&q=foo&sort=recent&scope=<proj>&list=<id>&tag=<tag>&open=<id>
 *    #/tasks/new                   → create page
 *  View/edit of an existing task happens in the list page's SidePanel (the
 *  `?open=<id>` query, with `?edit=1` for the edit toggle); only create is its own
 *  page. Search/sort/scope/list refine the view in place → replace (no per-
 *  keystroke/-click history); open + edit are Back-undoable destinations → push. */
export function TasksSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  const seg = (sub || '').split('/')[0]
  if (seg === 'new')
    return <TaskCreatePage onBack={() => navigate('tasks')} onCreated={() => navigate('tasks')} />
  return <TaskList navigate={navigate} query={query} setQuery={setQuery} navEpoch={navEpoch} />
}

/** The task list and board at the address's keys: All tasks at `#/tasks`, or one project's Tasks
 *  view (`inProject`, at `#/projects/<id>?tab=tasks`), which reads the same keys but its scope. */
export function TaskList({ navigate, query, setQuery, navEpoch, inProject }: Pick<RouteProps, 'navigate' | 'query' | 'setQuery' | 'navEpoch'> & {
  inProject?: Parameters<typeof TasksListPage>[0]['inProject']
}) {
  const [editing, setEditing] = useEditFlag(query, setQuery)
  return (
    <TasksListPage key={navEpoch}
      view={query.view || ''} filter={query.filter || 'all'} openId={query.open || null}
      q={query.q || ''} sort={query.sort || ''} scope={query.scope || ''} list={query.list || ''}
      tag={query.tag || ''}
      editing={editing} setEditing={setEditing}
      inProject={inProject}
      onCreate={() => navigate('tasks/new')}
      // view-mode + status filter are in-place refinements → replace,
      // so toggling List/Board/filter doesn't stack Back-undoable history entries.
      setView={(v) => setQuery({ view: v }, { replace: true })}
      setFilter={(f) => setQuery({ filter: f === 'all' ? null : f }, { replace: true })}
      // Opening / switching a task lands in view mode → clear ?edit alongside ?open.
      setOpenId={(id) => setQuery({ open: id, edit: null })}
      setQ={(v) => setQuery({ q: v || null }, { replace: true })}
      setSort={(v) => setQuery({ sort: v || null }, { replace: true })}
      setScope={(v) => setQuery({ scope: v || null }, { replace: true })}
      setList={(v) => setQuery({ list: v || null }, { replace: true })}
      setTag={(v) => setQuery({ tag: v || null }, { replace: true })} />
  )
}
