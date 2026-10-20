import { AgentsListPage } from './AgentsListPage'
import { AgentCreatePage } from './AgentCreatePage'
import type { RouteProps } from '../../app/useQueryState'

/** Agents navigation — URL-addressable: `#/agents` (list; search/open via
 *  ?query), `#/agents/new` (native-agent builder). Detail/edit in the list
 *  page's SidePanel (`?open=native:<name>` or `?open=acp:<provider>:<id>`). */
export function AgentsSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  if ((sub || '').split('/')[0] === 'new')
    return <AgentCreatePage onBack={() => navigate('agents')} onCreated={() => navigate('agents')} />
  return <AgentsListPage key={navEpoch} onCreate={() => navigate('agents/new')} query={query} setQuery={setQuery} />
}
