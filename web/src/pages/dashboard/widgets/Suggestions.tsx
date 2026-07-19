import { useEffect, useState, useCallback } from 'react'
import { motion } from 'framer-motion'
import { Sparkles, RefreshCw, ArrowUpRight } from 'lucide-react'
import { api } from '../../../lib/api'
import { EmptyState } from './kit'
import { spring } from '../../../design/motion'
import type { RouteProps } from '../../../app/useQueryState'

/** Today's Suggestions — LLM prompt-starter cards personalized from memory +
 *  recent activity. One tap launches the suggestion as a fresh chat; a refresh
 *  regenerates (force=1). */
export function Suggestions({ navigate }: RouteProps) {
  const [items, setItems] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback((force = false) => {
    setLoading(true)
    api.suggestions(force)
      .then((d) => setItems(d.suggestions ?? []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load(false) }, [load])

  if (loading && items.length === 0) {
    return (
      <div className="flex flex-col gap-s pt-xs">
        {[0, 1, 2].map((i) => <div key={i} className="skeleton h-10 w-full rounded-lg" style={{ opacity: 1 - i * 0.2 }} />)}
      </div>
    )
  }
  if (items.length === 0) {
    return <EmptyState icon={Sparkles}>No suggestions yet — they build from your activity.</EmptyState>
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      {items.slice(0, 5).map((s, i) => (
        <motion.button
          key={s}
          type="button"
          layout
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0, transition: { ...spring.spatialDefault, delay: i * 0.04 } }}
          whileHover={{ y: -1 }}
          onClick={() => navigate(`chat/new?seed=${encodeURIComponent(s)}`)}
          className="group flex items-center gap-s rounded-lg bg-surface-low px-m py-s text-left transition-colors hover:bg-surface-high"
        >
          <Sparkles size={13} className="shrink-0 text-primary" />
          <span data-type="body-m" className="min-w-0 flex-1 text-on-surface-var group-hover:text-on-surface">{s}</span>
          <ArrowUpRight size={14} className="shrink-0 text-on-surface-low opacity-0 transition-opacity group-hover:opacity-100" />
        </motion.button>
      ))}
      <button
        type="button"
        onClick={() => load(true)}
        className="mt-xs inline-flex items-center gap-xs self-start rounded-pill px-m py-xs text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface"
        data-type="label-m"
      >
        <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
      </button>
    </div>
  )
}
