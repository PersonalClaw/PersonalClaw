import { motion } from 'framer-motion'
import { Compass, ArrowUpRight, ArrowRight, X } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { SlotEmptyState } from './kit'
import { spring } from '../../../design/motion'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import type { DiscoverTip, DiscoverTryIt } from '../../../lib/api'
import type { RouteProps } from '../../../app/useQueryState'

// The dashboard spotlight shows only the first few tips; the rest live in the hub.
const SPOTLIGHT = 3

/** Discover (§6) — the dashboard's curated spotlight: the first few parts of the
 *  system the user hasn't tried yet, each a one-line lesson with a deep link and a
 *  dismiss. The full grouped list lives on the dedicated Discover hub (a "See all"
 *  link jumps there). Data + dismiss come from the shared DashboardLive feed.
 *
 *  Propose-don't-write: a tip only points (deep-links into an existing page) and
 *  hides (dismiss persists; an area auto-hides once used). It never enables or
 *  configures anything — the user acts. */
export function Discover({ navigate }: RouteProps) {
  const { discover, dismissDiscoverTip } = useDashboardLive()

  // Kill switch off, or nothing loaded yet: a slot with no tips reads as "nothing
  // to learn" (the Section wrapper still shows its label).
  if (!discover || !discover.enabled) {
    return <SlotEmptyState icon={Compass}>Discover tips are off.</SlotEmptyState>
  }
  const tips = discover.areas.flatMap((a) => a.tips)
  if (tips.length === 0) {
    return (
      <SlotEmptyState icon={Compass}>
        You&rsquo;ve explored every part of PersonalClaw. Nice.
      </SlotEmptyState>
    )
  }

  const spotlight = tips.slice(0, SPOTLIGHT)
  const remaining = tips.length - spotlight.length

  return (
    <div className="flex flex-col gap-s">
      {spotlight.map((tip) => (
        <TipCard key={tip.id} tip={tip} onGo={() => navigate(tryItPath(tip.try_it))} onDismiss={() => dismissDiscoverTip(tip.id)} />
      ))}
      <Button variant="ghost" size="xs" onClick={() => navigate('discover')} className="group self-start text-on-surface-var">
        {remaining > 0 ? `See all ${tips.length} in Discover` : 'Open Discover'}
        <ArrowRight size={14} className="transition-transform group-hover:translate-x-px" />
      </Button>
    </div>
  )
}

/** One spotlight tip — icon + title + one-line lesson, a "try it" deep link, and a
 *  dismiss X. Mirrors the hub row so the two surfaces read the same. */
function TipCard({ tip, onGo, onDismiss }: { tip: DiscoverTip; onGo: () => void; onDismiss: () => void }) {
  return (
    <motion.div
      key={tip.id}
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0, transition: spring.spatialDefault }}
      className="flex flex-col gap-s rounded-lg bg-surface-low px-m py-m"
    >
      <div className="flex items-start gap-s">
        <Compass size={15} className="mt-0.5 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <p data-type="label-l" className="truncate text-on-surface">{tip.title}</p>
          <p data-type="body-m" className="mt-xs text-on-surface-var">{tip.lesson}</p>
        </div>
        <IconButton
          icon={X}
          label="Dismiss — don't suggest this again"
          onClick={onDismiss}
          size={28}
          iconSize={14}
          className="-mr-xs shrink-0 text-on-surface-low"
        />
      </div>
      <Button variant="tonal" size="xs" onClick={onGo} className="group self-start">
        {tip.try_it.label}
        <ArrowUpRight size={13} className="transition-transform group-hover:translate-x-px group-hover:-translate-y-px" />
      </Button>
    </motion.div>
  )
}

/** Turn a `try_it` descriptor into a navigate() path. The route + query come from
 *  the backend, so the deep link stays server-authored — the widget serializes it. */
function tryItPath(t: DiscoverTryIt): string {
  const q = new URLSearchParams(t.query ?? {}).toString()
  return q ? `${t.route}?${q}` : t.route
}
