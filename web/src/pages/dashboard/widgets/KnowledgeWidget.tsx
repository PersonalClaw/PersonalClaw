import { BookOpen, Boxes, Share2, Sparkles } from 'lucide-react'
import { api, type KnowledgeStats } from '../../../lib/api'
import { useCachedData } from '../../../lib/useCachedData'
import type { RouteProps } from '../../../app/useQueryState'

function Stat({ icon: Icon, n, label }: { icon: typeof Boxes; n: number; label: string }) {
  return (
    <div className="flex items-center gap-s">
      <Icon size={14} className="shrink-0 text-primary" />
      <span data-type="title-m" className="tabular-nums text-on-surface">{n}</span>
      <span data-type="body-m" className="text-on-surface-low">{label}</span>
    </div>
  )
}

/** Knowledge Pulse — knowledge-base stats (items · entities · relations) + embed
 *  status, with a quick jump to the Knowledge page. */
export function KnowledgeWidget({ navigate }: RouteProps) {
  const { data } = useCachedData<KnowledgeStats>('dashboard:knowledge-stats', () => api.knowledgeStats(), { persist: true })

  return (
    <button type="button" onClick={() => navigate('knowledge')} className="flex h-full w-full flex-col gap-m pt-xs text-left">
      <div className="flex flex-col gap-s">
        <Stat icon={Boxes} n={data?.items ?? 0} label="items" />
        <Stat icon={BookOpen} n={data?.entities ?? 0} label="entities" />
        <Stat icon={Share2} n={data?.relations ?? 0} label="relations" />
      </div>
      {data?.embeddings && (
        <p data-type="body-m" className="mt-auto flex items-center gap-xs text-on-surface-low">
          <Sparkles size={12} className={data.embeddings.enabled ? 'text-ok' : 'text-on-surface-low'} />
          {data.embeddings.enabled
            ? `Embedded${data.embeddings.stale_items ? ` · ${data.embeddings.stale_items} stale` : ''}`
            : 'Embeddings off'}
        </p>
      )}
    </button>
  )
}
