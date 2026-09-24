import { TriggersListPage } from './TriggersListPage'
import { TriggerCreatePage } from './TriggerCreatePage'
import type { RouteProps } from '../../app/useQueryState'

/** Triggers navigation — URL-addressable: `#/triggers` (list; filter/search/open
 *  via ?query), `#/triggers/new` (create page). View/edit happen in the list
 *  page's SidePanel (`?open=<id>`).
 *
 *  A preset on-ramp is `#/triggers/new?kind=schedule&preset=<id>` — the SEED rides
 *  in the URL like `kind`/`pattern` already do, so a seeded create flow is
 *  deep-linkable, back/forward-safe and survives a reload. `#/triggers/new` with no
 *  `preset` is the untouched expert blank path. */
export function TriggersSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  if ((sub || '').split('/')[0] === 'new')
    return <TriggerCreatePage onBack={() => navigate('triggers')} onCreated={() => navigate('triggers')} query={query} setQuery={setQuery} />
  return (
    <TriggersListPage
      key={navEpoch}
      onCreate={(presetId) => navigate(presetId ? `triggers/new?kind=schedule&preset=${encodeURIComponent(presetId)}` : 'triggers/new')}
      query={query}
      setQuery={setQuery}
    />
  )
}
