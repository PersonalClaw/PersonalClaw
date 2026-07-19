import { WorkflowsListPage } from './WorkflowsListPage'
import { WorkflowCreatePage } from './WorkflowCreatePage'
import type { RouteProps } from '../../app/useQueryState'

/** Workflows navigation — URL-addressable: `#/workflows` (list; search/open via
 *  ?query), `#/workflows/new` (create). View/edit in the list SidePanel. */
export function WorkflowsSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  if ((sub || '').split('/')[0] === 'new')
    return <WorkflowCreatePage onBack={() => navigate('workflows')} onCreated={() => navigate('workflows')} />
  return <WorkflowsListPage key={navEpoch} onCreate={() => navigate('workflows/new')} query={query} setQuery={setQuery} />
}
