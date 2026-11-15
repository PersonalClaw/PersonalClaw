import { useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { api, type KnowledgeStats } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { notify } from '../../app/appSdk'
import { TextLink } from '../../ui/TextLink'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { KnowledgeGraph } from '../knowledge/KnowledgeGraph'
import { ConflictPanel } from '../knowledge/ConflictPanel'
import { DecisionJournal } from '../knowledge/DecisionJournal'
import { TagManager } from '../knowledge/TagManager'

// ── `?view=graph | tags | conflicts | decisions`, drawn in place ────────────────────────
//
// Until the power-user view gives them tabs, the old Knowledge page's four views open inside the
// library at the same key, which `knowledge?view=…` carries over: its graph, its tag tree, its
// conflicts and its decision journal.

/** The views the library draws in place. */
export const IN_PLACE_VIEWS = ['graph', 'tags', 'conflicts', 'decisions'] as const
export type InPlaceViewKey = (typeof IN_PLACE_VIEWS)[number]

export const isInPlaceView = (v: string | undefined): v is InPlaceViewKey => (IN_PLACE_VIEWS as readonly string[]).includes(v ?? '')

const TITLES: Record<InPlaceViewKey, string> = { graph: 'Graph', tags: 'Tags', conflicts: 'Conflicts', decisions: 'Decisions' }

export function InPlaceView({ view, entity, stats, navigate, onBack, onEntity, onFilterTag }: {
  view: InPlaceViewKey
  /** The graph's selected entity (`?entity=`). */
  entity?: string
  stats?: KnowledgeStats
  navigate: (path: string) => void
  onBack: () => void
  onEntity: (name: string) => void
  onFilterTag: (tag: string) => void
}) {
  const [reading, setReading] = useState(false)
  const regenerate = async () => {
    setReading(true)
    try {
      const { queued } = await api.regenerateKnowledgeIntelligence('missing')
      notify(queued ? `Reading ${queued} items again. You can keep working.` : 'Nothing was waiting to be read.', 'success')
      invalidateKeys('knowledge', true)
    } catch { notify("Couldn't start reading your items again.", 'error') } finally { setReading(false) }
  }
  return (
    <section role="region" aria-label={TITLES[view]} className="flex min-h-0 flex-1 flex-col gap-m px-l pb-l">
      <TextLink onClick={onBack} className={`self-start ${TOUCH_TARGET}`}><ArrowLeft size={14} aria-hidden /> Library</TextLink>
      <h2 data-type="title-l" className="text-on-surface">{TITLES[view]}</h2>
      <div className="min-h-0 flex-1">
        {view === 'graph' && (
          <KnowledgeGraph selectedId={entity ?? null} onSelect={onEntity} onRegenerate={() => void regenerate()} regenerating={reading}
            enrichment={stats?.enrichment} onSetupModel={() => navigate('settings/models')}
            reloadKey={stats ? `${stats.entities}:${stats.relations}` : ''} />
        )}
        {view === 'tags' && <TagManager onFilter={onFilterTag} onChanged={() => invalidateKeys('knowledge:library', true)} />}
        {view === 'conflicts' && <ConflictPanel />}
        {view === 'decisions' && (
          <DecisionJournal onOpenItem={(id) => navigate(`knowledge/item/${encodeURIComponent(id)}`)} onOpenChat={() => navigate('chat')} />
        )}
      </div>
    </section>
  )
}
