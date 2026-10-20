import { TriggersListPage } from './TriggersListPage'
import { TriggerCreatePage } from './TriggerCreatePage'
import { qget, type RouteProps } from '../../app/useQueryState'

/** Triggers navigation — URL-addressable: `#/triggers` (list; filter/search/open
 *  via ?query), `#/triggers/new` (create page). View/edit happen in the list
 *  page's SidePanel (`?open=<id>`).
 *
 *  A preset on-ramp is `#/triggers/new?kind=schedule&preset=<id>` — the SEED rides
 *  in the URL like `kind`/`pattern` already do, so a seeded create flow is
 *  deep-linkable, back/forward-safe and survives a reload. `#/triggers/new` with no
 *  `preset` is the untouched expert blank path. */
export function TriggersSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  // Keyed on the preset: the page seeds its fields once, when it mounts, so picking a preset on the
  // blank form (or following a link to another) has to mount a new page for every field to take it.
  if ((sub || '').split('/')[0] === 'new')
    return <TriggerCreatePage key={qget(query, 'preset')} onBack={() => navigate('triggers')} onCreated={() => navigate('triggers')} query={query} setQuery={setQuery} />
  return (
    <TriggersListPage
      key={navEpoch}
      onCreate={(presetId) => navigate(presetId ? `triggers/new?kind=schedule&preset=${encodeURIComponent(presetId)}` : 'triggers/new')}
      query={query}
      setQuery={setQuery}
    />
  )
}
