import { motion } from 'framer-motion'
import { Compass, ArrowUpRight, X } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { SlotEmptyState } from './kit'
import { spring } from '../../../design/motion'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import type { PowerUpTryIt } from '../../../lib/api'
import type { RouteProps } from '../../../app/useQueryState'

/** Capability discovery (§6) — one untouched capability at a time, proposed as a
 *  two-sentence mini-lesson with a "try it" deep link and a dismiss. The self-
 *  description machinery (the /api/manifest denominator) makes "capabilities you
 *  have but have never touched" computable; this surfaces the next one.
 *
 *  Propose-don't-write: the card only points (deep-links into an existing page)
 *  and hides (dismiss persists). It never enables or configures anything — the
 *  user acts. Data + dismiss come from the shared DashboardLive feed. */
export function PowerUps({ navigate }: RouteProps) {
  const { powerUps, dismissPowerUp } = useDashboardLive()

  // Kill switch off, or nothing loaded yet: render nothing (the Section wrapper
  // still shows its label; a slot with no proposal reads as "nothing to learn").
  if (!powerUps || !powerUps.enabled) {
    return <SlotEmptyState icon={Compass}>Capability tips are off.</SlotEmptyState>
  }
  const pu = powerUps.power_up
  if (!pu) {
    return (
      <SlotEmptyState icon={Compass}>
        You&rsquo;ve explored every documented capability. Nice.
      </SlotEmptyState>
    )
  }

  const go = () => navigate(tryItPath(pu.try_it))

  return (
    <motion.div
      key={pu.id}
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0, transition: spring.spatialDefault }}
      className="flex flex-col gap-s rounded-lg bg-surface-low px-m py-m"
    >
      <div className="flex items-start gap-s">
        <Compass size={15} className="mt-0.5 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <p data-type="label-l" className="truncate text-on-surface">{pu.title}</p>
          <p data-type="body-m" className="mt-xs text-on-surface-var">{pu.lesson}</p>
        </div>
        <IconButton
          icon={X}
          label="Dismiss — don't suggest this again"
          onClick={() => dismissPowerUp(pu.id)}
          size={28}
          iconSize={14}
          className="-mr-xs shrink-0 text-on-surface-low"
        />
      </div>
      <div className="flex items-center gap-m">
        <Button variant="tonal" size="xs" onClick={go} className="group self-start">
          {pu.try_it.label}
          <ArrowUpRight size={13} className="transition-transform group-hover:translate-x-px group-hover:-translate-y-px" />
        </Button>
        {powerUps.untouched_count > 1 && (
          <span data-type="body-s" className="text-on-surface-low">
            {powerUps.untouched_count - 1} more to explore
          </span>
        )}
      </div>
    </motion.div>
  )
}

/** Turn the manifest-derived `try_it` descriptor into a navigate() path. The
 *  route + query come from the backend (currently `tools?open=<name>`), so the
 *  deep link stays server-authored — the widget just serializes it. */
function tryItPath(t: PowerUpTryIt): string {
  const q = new URLSearchParams(t.query ?? {}).toString()
  return q ? `${t.route}?${q}` : t.route
}
