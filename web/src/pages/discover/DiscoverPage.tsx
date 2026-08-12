import { motion } from 'framer-motion'
import { Compass, ArrowUpRight, X } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { EmptyState, ListSkeleton } from '../../ui/ListScaffold'
import { spring } from '../../design/motion'
import { useCachedData } from '../../lib/useCachedData'
import { api, type DiscoverResponse, type DiscoverTip, type DiscoverTryIt } from '../../lib/api'
import type { RouteProps } from '../../app/useQueryState'
import { PageTitle } from '../../ui/PageTitle'
import { accentChip } from '../../design/accent'

/** Discover hub (§6) — the full curated tour of PersonalClaw, grouped by area.
 *  Every tip the dashboard spotlight rotates through lives here at once, each a
 *  one-line lesson with a deep link into the feature and a dismiss X.
 *
 *  Propose-don't-write: a tip only POINTS (deep-links into an existing page) and
 *  HIDES (an explicit dismiss persists forever; an area auto-hides once the user
 *  has actually used it). It never enables or configures anything — the user acts.
 *  The catalog is hand-authored server-side, so this page just renders + dismisses. */
export function DiscoverPage({ navigate }: Pick<RouteProps, 'navigate'>) {
  // Cached for instant paint on revisit; persist:false so a dismiss made on the
  // dashboard (or another tab) never shows a stale tip after a hard reload.
  const { data, refresh } = useCachedData(
    'discover', () => api.discover().catch(() => null as DiscoverResponse | null), { persist: false },
  )

  // Dismiss persists server-side; on success refetch so the tip drops from every
  // area (and the "explored everything" empty state shows once the last one goes).
  const dismiss = (id: string) => { api.dismissDiscoverTip(id).then(() => refresh()).catch(() => {}) }

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={
            <PageTitle className="flex items-center gap-s">
              Discover
              {data && data.enabled && data.visible_count > 0 && (
                <span
                  data-type="label-s"
                  className="inline-flex h-5 items-center rounded-pill px-2"
                  style={accentChip}
                >
                  {data.visible_count}
                </span>
              )}
            </PageTitle>
          }
        />
      }
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {data === undefined ? (
          <ListSkeleton rows={6} />
        ) : !data || !data.enabled ? (
          <EmptyState
            icon={Compass}
            title="Discover is off"
            hint="Curated tips that guide you to the parts of PersonalClaw you haven't tried yet. Turn them back on in Settings › Legibility."
            action={{ label: 'Open Settings', onClick: () => navigate('settings/legibility'), icon: Compass }}
          />
        ) : data.visible_count === 0 ? (
          <EmptyState
            icon={Compass}
            title="You've explored every part of PersonalClaw"
            hint="Nice. New tips will appear here as PersonalClaw grows — and anything you dismissed stays hidden."
          />
        ) : (
          <div className="flex flex-col gap-2xl">
            <p data-type="body-m" className="max-w-[520px] text-on-surface-var">
              A guided tour of the parts of PersonalClaw you haven&rsquo;t tried yet. Each tip links
              straight into the feature; dismiss any you&rsquo;re not interested in.
            </p>
            {data.areas.map((group) => (
              <section key={group.area} className="flex min-w-0 flex-col gap-m">
                <div className="flex items-center gap-s">
                  <h3 data-type="label-l" className="text-on-surface-var">{group.area}</h3>
                  <span className="h-px flex-1 bg-outline-variant/40" />
                </div>
                <div className="flex flex-col gap-s">
                  {group.tips.map((tip, i) => (
                    <TipRow
                      key={tip.id}
                      tip={tip}
                      index={i}
                      onGo={() => navigate(tryItPath(tip.try_it))}
                      onDismiss={() => dismiss(tip.id)}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </WorkbenchLayout>
  )
}

/** One hub row — icon + title + one-line lesson, a "try it" deep link, and a
 *  dismiss X. Mirrors the dashboard TipCard so the spotlight and the hub read the
 *  same; laid out wider here since the hub has the full page column. */
function TipRow({ tip, index, onGo, onDismiss }: { tip: DiscoverTip; index: number; onGo: () => void; onDismiss: () => void }) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, height: 0, marginTop: 0, transition: spring.spatialFast }}
      transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}
      className="group flex items-center gap-m rounded-lg bg-surface-container px-l py-m transition-colors hover:bg-surface-high"
    >
      <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
        <Compass size={19} className="text-primary" />
      </span>
      <div className="min-w-0 flex-1">
        <p data-type="label-l" className="text-on-surface">{tip.title}</p>
        <p data-type="body-m" className="mt-xs text-on-surface-var">{tip.lesson}</p>
      </div>
      <Button variant="tonal" size="sm" onClick={onGo} className="group/go shrink-0">
        {tip.try_it.label}
        <ArrowUpRight size={14} className="transition-transform group-hover/go:translate-x-px group-hover/go:-translate-y-px" />
      </Button>
      <IconButton
        icon={X}
        label="Dismiss — don't suggest this again"
        onClick={onDismiss}
        size={34}
        className="shrink-0 text-on-surface-low"
      />
    </motion.div>
  )
}

/** Turn a `try_it` descriptor into a navigate() path. The route + query come from
 *  the backend, so the deep link stays server-authored — the page serializes it. */
function tryItPath(t: DiscoverTryIt): string {
  const q = new URLSearchParams(t.query ?? {}).toString()
  return q ? `${t.route}?${q}` : t.route
}
