import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { TopBar } from './TopBar'
import { Spark } from './Spark'
import { Button } from './Button'
import { spring, expr } from '../design/motion'

/** Shared shell for the workspace/build list PAGES (design Tenet 2: list as a
 *  destination page, not a cramped panel). Centered column at the customizable
 *  content width, sparse top bar, and uniform loading / empty states so every
 *  entity surface reads as one family. `right` fills the top-bar action slot. */
export function ListScaffold({ title, right, children, bodyClassName }: {
  title: ReactNode
  right?: ReactNode
  children: ReactNode
  bodyClassName?: string
}) {
  return (
    <div className="flex h-full flex-col">
      <TopBar left={<span data-type="title-l" className="text-on-surface">{title}</span>} right={right} />
      <div className="flex-1 overflow-y-auto">
        <div className={bodyClassName ?? 'mx-auto px-l py-2xl'} style={{ maxWidth: 'var(--content-width)' }}>
          {children}
        </div>
      </div>
    </div>
  )
}

/** Uniform empty state — claw mark, headline, subline, optional CTA. */
export function EmptyState({ icon: Icon, title, hint, action }: {
  icon?: LucideIcon
  title: string
  hint?: string
  action?: { label: string; onClick: () => void; icon?: LucideIcon }
}) {
  return (
    <div className="flex flex-col items-center gap-l py-2xl text-center">
      {Icon ? <span className="inline-flex size-12 items-center justify-center rounded-xl" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}><Icon size={26} className="text-primary" /></span> : <Spark size={36} />}
      <div>
        <h2 data-type="headline-s" className="text-on-surface">{title}</h2>
        {hint && <p className="mt-1 max-w-[420px] text-on-surface-low text-[0.9375rem]">{hint}</p>}
      </div>
      {action && (
        <Button onClick={action.onClick}>{action.icon && <action.icon size={16} />} {action.label}</Button>
      )}
    </div>
  )
}

/** Animated row wrapper — staggered rise+fade in, and (when clickable) a physical
 *  hover-lift + press so rows feel like liftable cards, not flat strips. Lift/press
 *  depth scale through the expressiveness knob; exit collapses so removals animate.
 *  Consistent across every list page. */
export function ListRow({ index = 0, onClick, children, accent }: {
  index?: number
  onClick?: () => void
  children: ReactNode
  accent?: string
}) {
  const interactive = !!onClick
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, height: 0, marginTop: 0, transition: spring.spatialFast }}
      transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}
      // hover lifts the row toward the viewer + a hair of shadow; press settles it
      // back. Depth scales via expr() (bold lifts more, refined barely). Only for
      // clickable rows — static rows stay put.
      whileHover={interactive ? { y: -expr(3, 0.3), boxShadow: 'var(--shadow-lift)' } : undefined}
      whileTap={interactive ? { scale: 1 - expr(0.01, 0.3) } : undefined}
      onClick={onClick}
      className={`group relative flex items-center gap-l overflow-hidden rounded-lg bg-surface-container px-l py-l text-left transition-colors hover:bg-surface-high ${interactive ? 'cursor-pointer' : ''}`}
    >
      {accent && <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: accent }} />}
      {children}
    </motion.div>
  )
}

export function Loading() {
  return <div className="text-on-surface-low text-[0.875rem]">Loading…</div>
}

/** A single shimmering placeholder block. Use to render the SHAPE of content while
 *  a (cache-miss) fetch is in flight, so the page appears instantly instead of a
 *  bare "Loading…". `className` controls size/shape (height, width, rounding). */
export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton rounded-md ${className}`} aria-hidden="true" />
}

/** N placeholder rows shaped like ListRow — the default first-load state for list
 *  pages. Matches ListRow's padding/leading-icon so the swap to real data is calm. */
export function ListSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-s" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-l rounded-lg bg-surface-container px-l py-l">
          <Skeleton className="size-10 shrink-0 rounded-lg" />
          <div className="flex-1 min-w-0 space-y-2">
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="h-3 w-2/3" />
          </div>
        </div>
      ))}
    </div>
  )
}

/** First-load placeholder for a settings FORM panel: a title block + N sections,
 *  each a heading and a few label/control rows. Shaped like the Section/Row chrome
 *  so the swap to the real form is calm. Use as the loading gate on config panels
 *  fetched via useCachedData (Chat, Voice, Inbox, Notifications, Agent defaults…). */
export function FormSkeleton({ sections = 2, rows = 3, title = true }: { sections?: number; rows?: number; title?: boolean }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {title && (
        <div className="mb-l space-y-2">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      )}
      {Array.from({ length: sections }).map((_, s) => (
        <section key={s} className="mb-2xl">
          <Skeleton className="mb-m h-4 w-32" />
          <div className="rounded-lg bg-surface-container px-4 py-1">
            {Array.from({ length: rows }).map((_, r) => (
              <div key={r} className="flex items-center justify-between gap-4 border-b border-outline-variant/20 py-3 last:border-0">
                <div className="min-w-0 flex-1 space-y-1.5"><Skeleton className="h-3.5 w-1/3" /><Skeleton className="h-3 w-1/2" /></div>
                <Skeleton className="h-6 w-16 shrink-0 rounded-pill" />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

/** First-load placeholder for a stat/hub panel: a title block + a grid of N stat
 *  cards. Use on the read-only dashboard-style panels (Overview, Security). */
export function CardGridSkeleton({ cards = 4, cols = 2, title = true }: { cards?: number; cols?: number; title?: boolean }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {title && (
        <div className="mb-l space-y-2">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      )}
      <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
        {Array.from({ length: cards }).map((_, i) => (
          <div key={i} className="rounded-lg bg-surface-container px-4 py-4 space-y-3">
            <div className="flex items-center gap-2"><Skeleton className="size-5 rounded" /><Skeleton className="h-3.5 w-24" /></div>
            <Skeleton className="h-7 w-20" />
            <Skeleton className="h-3 w-2/3" />
          </div>
        ))}
      </div>
    </div>
  )
}
