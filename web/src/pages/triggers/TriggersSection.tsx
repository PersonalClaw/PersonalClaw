import { TriggersListPage } from './TriggersListPage'
import { TriggerCreatePage } from './TriggerCreatePage'
import type { RouteProps } from '../../app/useQueryState'

/** Triggers navigation — URL-addressable: `#/triggers` (list; filter/search/open
 *  via ?query), `#/triggers/new` (create page). View/edit happen in the list
 *  page's SidePanel (`?open=<id>`). */
export function TriggersSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  if ((sub || '').split('/')[0] === 'new')
    return <TriggerCreatePage onBack={() => navigate('triggers')} onCreated={() => navigate('triggers')} />
  return <TriggersListPage key={navEpoch} onCreate={() => navigate('triggers/new')} query={query} setQuery={setQuery} />
}
