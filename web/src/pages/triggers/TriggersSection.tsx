import { TriggersListPage } from './TriggersListPage'
import { TriggerCreatePage } from './TriggerCreatePage'
import { qget, type RouteProps } from '../../app/useQueryState'

/** The Automations list's navigation (#3816) — URL-addressable: `#/automations` (the list;
 *  filter/search/open via ?query), `#/automations/new` (create page). View/edit happen in the list
 *  page's SidePanel (`?open=<id>`), and the run its history has open is `?run=<run id>`.
 *
 *  `#/triggers` and `#/triggers/new`, the list's addresses before it was Automations, open the same
 *  pages with the same query, so every link that names them still lands; what this page writes now
 *  names `#/automations`.
 *
 *  A preset on-ramp is `#/automations/new?kind=schedule&preset=<id>` — the SEED rides
 *  in the URL like `kind`/`pattern` already do, so a seeded create flow is
 *  deep-linkable, back/forward-safe and survives a reload. `…/new` with no
 *  `preset` is the untouched expert blank path. */
export function TriggersSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  // Keyed on the preset: the page seeds its fields once, when it mounts, so picking a preset on the
  // blank form (or following a link to another) has to mount a new page for every field to take it.
  if ((sub || '').split('/')[0] === 'new')
    return <TriggerCreatePage key={qget(query, 'preset')} onBack={() => navigate('automations')} onCreated={() => navigate('automations')} query={query} setQuery={setQuery} />
  return (
    <TriggersListPage
      key={navEpoch}
      onCreate={(presetId) => navigate(presetId ? `automations/new?kind=schedule&preset=${encodeURIComponent(presetId)}` : 'automations/new')}
      query={query}
      setQuery={setQuery}
    />
  )
}
