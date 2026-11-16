import { useState } from 'react'
import { api, type KnowledgeStats } from '../../lib/api'
import { useIsMobile } from '../../app/useIsMobile'
import { TextLink } from '../../ui/TextLink'
import { invalidateKeys, useQuery } from '../../lib/data'
import { notify } from '../../app/appSdk'
import { reportActionFailure } from '../../app/reportingWrite'
import { Button } from '../../ui/Button'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { KnowledgeGraph } from '../knowledge/KnowledgeGraph'
import { DecisionJournal } from '../knowledge/DecisionJournal'
import { TagManager } from '../knowledge/TagManager'
import { ComparePane } from './ComparePane'
import { LibrarySettingsPanel, type Reindex } from './LibrarySettingsPanel'
import { n } from './libraryWords'
import type { PowerTab } from './powerUserView'

// ── The Power user view's tabs other than Items ───────────────────────────────────────────────────
//
// Graph (the default), Tags, Conflicts, Decisions and Library settings, each the panel of its tab.

export function PowerTabPanel({ tab, entity, stats, navigate, onEntity, onShowEntity, onFilterTag, openTab, filterItems, processAll, reindex }: {
  tab: Exclude<PowerTab, 'items'>
  /** The graph's selected entity (`?entity=`). */
  entity?: string
  stats?: KnowledgeStats
  navigate: (path: string) => void
  onEntity: (name: string) => void
  /** Open Items with the items that mention this name, one of them selected when given. */
  onShowEntity: (name: string, item?: string) => void
  onFilterTag: (tag: string) => void
  openTab: (tab: PowerTab) => void
  filterItems: (state: string) => void
  processAll: () => void
  reindex: Reindex
}) {
  const [reading, setReading] = useState(false)
  const phone = useIsMobile()
  const readMissed = async () => {
    setReading(true)
    // A refusal (no model to read with, say) is said in the gateway's own words.
    const res = await api.regenerateKnowledgeIntelligence('missing').catch(reportActionFailure('read missed items'))
    setReading(false)
    if (!res) return
    notify(res.queued ? `Reading ${n(res.queued)} ${res.queued === 1 ? 'item' : 'items'} again. You can keep working.` : 'Nothing was waiting to be read.', 'success')
    invalidateKeys('knowledge', true)
  }
  if (tab === 'graph') {
    return (
      <section aria-labelledby="lib-graph-title" className="flex min-h-0 flex-1 flex-col gap-m px-l pb-l">
        <header className="flex flex-wrap items-start gap-s">
          <div className="min-w-0 flex-1">
            <h2 id="lib-graph-title" data-type="title-l" className="text-on-surface">Graph</h2>
            <p data-type="body-s" className="text-on-surface-var">The people, places and ideas your items mention, and how they connect.</p>
          </div>
          <Button variant="secondary" size="sm" onClick={() => void readMissed()} loading={reading} className={TOUCH_TARGET}>Read missed items</Button>
        </header>
        {phone && !entity && stats?.entities ? <p data-type="caption" className="text-on-surface-low">Tap a name to see where it's mentioned.</p> : null}
        <div className="flex min-h-0 flex-1 gap-m max-md:flex-col">
          <div className="min-h-0 min-w-0 flex-1">
            <KnowledgeGraph selectedId={entity ?? null} onSelect={onEntity} onRegenerate={() => void readMissed()} regenerating={reading}
              enrichment={stats?.enrichment} onSetupModel={() => navigate('settings/models')}
              reloadKey={stats ? `${stats.entities}:${stats.relations}` : ''} />
          </div>
          {entity && <EntityPanel key={entity} name={entity} onEntity={onEntity} onShow={onShowEntity} />}
        </div>
      </section>
    )
  }
  if (tab === 'tags') {
    return (
      <section aria-labelledby="lib-tags-title" className="flex flex-col gap-m px-l pb-l">
        <h2 id="lib-tags-title" data-type="title-l" className="text-on-surface">Tags</h2>
        <TagManager onFilter={onFilterTag} onChanged={() => invalidateKeys('knowledge:library', true)} />
      </section>
    )
  }
  if (tab === 'conflicts') return <ConflictsTab onOpen={(id) => navigate(`knowledge/item/${encodeURIComponent(id)}`)} />
  if (tab === 'decisions') {
    return (
      <section aria-labelledby="lib-decisions-title" className="flex flex-col gap-m px-l pb-l">
        <h2 id="lib-decisions-title" data-type="title-l" className="text-on-surface">Decisions</h2>
        <DecisionJournal onOpenItem={(id) => navigate(`knowledge/item/${encodeURIComponent(id)}`)} onOpenChat={() => navigate('chat')} />
      </section>
    )
  }
  return <LibrarySettingsPanel navigate={navigate} openTab={openTab} filterItems={filterItems} processAll={processAll} reindex={reindex} />
}

/** Every conflict, those waiting on your call first, each with both items and the Compare choices. */
function ConflictsTab({ onOpen }: { onOpen: (itemId: string) => void }) {
  const { data, loading, error, refresh } = useQuery('knowledge:conflicts:500', () => api.knowledgeConflicts(500))
  const pairs = [...new Map((data?.conflicts ?? []).filter((c) => c.id).map((c) => [c.id!, c])).values()]
    .sort((a, b) => Number(!!a.choice) - Number(!!b.choice))
  return (
    <section aria-labelledby="lib-conflicts-title" className="flex flex-col gap-m px-l pb-l">
      <h2 id="lib-conflicts-title" data-type="title-l" className="text-on-surface">Conflicts</h2>
      {loading && !data ? <ListSkeleton rows={3} what="conflicts" />
        : error && !data ? <LoadError what="conflicts" error={error} onRetry={refresh} />
        : !pairs.length ? <p data-type="body-m" className="text-on-surface-low">No two items disagree.</p>
        : pairs.map((c) => (
          <div key={c.id} className="rounded-xl border border-outline-variant/40">
            <ComparePane cid={c.id!} fromTitle={c.item_title} onBack={() => {}} onOpen={onOpen} inList />
          </div>
        ))}
    </section>
  )
}

/** How many items the entity read returns at most (`get_entity_items`). */
const ENTITY_ITEMS_CAP = 50

/** A name picked in the graph: how many items mention it, the names it is linked to, four of the
 *  items, and all of them in Items. */
function EntityPanel({ name, onEntity, onShow }: { name: string; onEntity: (name: string) => void; onShow: (name: string, item?: string) => void }) {
  const items = useQuery(`knowledge:entity-items:${name}`, () => api.knowledgeEntityItems(name))
  const linked = useQuery(`knowledge:entity-related:${name}`, () => api.knowledgeEntityRelated(name).then((r) => r.related))
  const related = linked.data ?? []
  // Unread, it says so in a line rather than draw the name as linked to nothing.
  const unlinked = !!linked.error && linked.data === undefined
  const list = items.data ?? []
  const count = list.length >= ENTITY_ITEMS_CAP ? `${n(ENTITY_ITEMS_CAP)} or more` : n(list.length)
  return (
    <aside aria-labelledby="lib-entity-title" className="flex w-full shrink-0 flex-col gap-m rounded-xl bg-surface-high px-m py-m md:w-[320px]">
      <header>
        <h3 id="lib-entity-title" data-type="title-m" className="text-on-surface">{name}</h3>
        {items.data && <p data-type="body-s" className="text-on-surface-var">{list.length ? `Mentioned in ${count} ${list.length === 1 ? 'item' : 'items'}` : 'No item mentions it now.'}</p>}
      </header>
      {(related.length > 0 || unlinked) && (
        <section aria-labelledby="lib-entity-linked" className="flex flex-col gap-xs">
          <h4 id="lib-entity-linked" data-type="label-l" className="text-on-surface">Linked to</h4>
          {unlinked ? <p data-type="body-s" className="text-on-surface-low">Couldn't read what it is linked to.</p> : (
            <p data-type="body-s" className="flex flex-wrap gap-x-s gap-y-xs">
              {related.slice(0, 12).map((r) => <TextLink key={`${r.name}:${r.relation_type}`} size="sm" onClick={() => onEntity(r.name)}>{r.name}</TextLink>)}
            </p>
          )}
        </section>
      )}
      <section aria-labelledby="lib-entity-where" className="flex flex-col gap-xs">
        <h4 id="lib-entity-where" data-type="label-l" className="text-on-surface">Where it is mentioned</h4>
        {items.loading && !items.data ? <ListSkeleton rows={3} what="items that mention it" />
          : items.error && !items.data ? <LoadError what="items that mention it" error={items.error} onRetry={items.refresh} />
          : (
            <ul className="flex flex-col gap-xs">
              {list.slice(0, 4).map((it) => (
                <li key={it.id}><TextLink size="sm" onClick={() => onShow(name, `knowledge:${it.id}`)}>{it.title || it.url_title || 'Untitled'}</TextLink></li>
              ))}
            </ul>
          )}
      </section>
      {list.length > 0 && (
        <div><Button variant="secondary" size="sm" onClick={() => onShow(name)} className={TOUCH_TARGET}>
          {list.length >= ENTITY_ITEMS_CAP ? 'Show them all in Items' : `Show all ${n(list.length)} in Items`}
        </Button></div>
      )}
    </aside>
  )
}
