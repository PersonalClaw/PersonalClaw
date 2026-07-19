import { Layers, Clock, Sparkles } from 'lucide-react'
import { api, type MemoryStats } from '../../../lib/api'
import { useCachedData } from '../../../lib/useCachedData'
import type { RouteProps } from '../../../app/useQueryState'

function Stat({ icon: Icon, n, label }: { icon: typeof Layers; n: number; label: string }) {
  return (
    <div className="flex items-center gap-s">
      <Icon size={14} className="shrink-0 text-primary" />
      <span data-type="title-m" className="tabular-nums text-on-surface">{n}</span>
      <span data-type="body-m" className="text-on-surface-low">{label}</span>
    </div>
  )
}

/** Memory Pulse — memory-store stats (durable facts · episodes) + embed coverage,
 *  jumping to the Memory Studio. */
export function MemoryWidget({ navigate }: RouteProps) {
  const { data } = useCachedData<MemoryStats>('dashboard:memory-stats', () => api.memoryStats(), { persist: true })

  return (
    <button type="button" onClick={() => navigate('settings/memory')} className="flex h-full w-full flex-col gap-m pt-xs text-left">
      <div className="flex flex-col gap-s">
        <Stat icon={Layers} n={data?.semantic_active ?? 0} label="facts" />
        <Stat icon={Clock} n={data?.episodic_active ?? 0} label="episodes" />
      </div>
      {data && (
        <p data-type="body-m" className="mt-auto flex items-center gap-xs text-on-surface-low">
          <Sparkles size={12} className={data.embedded_count > 0 ? 'text-ok' : 'text-on-surface-low'} />
          {data.embedded_count > 0 ? `${data.embedded_count} embedded` : 'No embeddings'}
        </p>
      )}
    </button>
  )
}
