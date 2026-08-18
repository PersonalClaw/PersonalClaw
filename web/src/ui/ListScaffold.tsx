import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, RotateCcw, type LucideIcon } from 'lucide-react'
import { TopBar } from './TopBar'
import { Spark } from './Spark'
import { Button } from './Button'
import { spring, expr } from '../design/motion'
import { PageTitle } from './PageTitle'

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
      <TopBar left={<PageTitle>{title}</PageTitle>} right={right} />
      <div className="flex-1 overflow-y-auto">
        <div className={bodyClassName ?? 'mx-auto px-l py-2xl'} style={{ maxWidth: 'var(--content-width)' }}>
          {children}
        </div>
      </div>
    </div>
  )
}

/** First-load FAILURE for a list/collection surface — the sibling of `EmptyState`.
 *
 *  A failed fetch and a genuinely empty collection are different facts, and every surface
 *  that renders `EmptyState` on `data === undefined` conflates them: the user is told "you
 *  have none" when the truth is "we could not load it", with no way to retry and nothing
 *  announced. `useCachedData` returns an `error` for exactly this — measured: **3 of 106
 *  call sites read it.**
 *
 *  `role="alert"` because a load failure is unrequested bad news that changes what the
 *  screen means; `EmptyState` deliberately has no live region, since "you have none" is a
 *  normal answer.
 *
 *  Pair with the ONE condition that distinguishes the two states:
 *
 *      {data === undefined && error ? <LoadError what="projects" error={error} onRetry={load} />
 *       : data === undefined      ? <ListSkeleton />
 *       : data.length === 0       ? <EmptyState … />
 *       : …rows}
 */
export function LoadError({ what, error, onRetry }: {
  /** The thing that failed to load, interpolated into "Couldn't load your <what>". A lowercase,
   *  bare noun — no leading article ("the store catalog" → "Couldn't load your the store catalog").
   *  Singular is fine now that the reassurance no longer reads "Your <what> ARE safe". */
  what: string
  /** The rejection from `useCachedData`; its `message` is shown when present. */
  error?: unknown
  /** Re-runs the fetch. Omit only if the surface genuinely cannot retry. */
  onRetry?: () => void
}) {
  return (
    <div role="alert" className="flex flex-col items-center gap-l py-2xl text-center">
      <AlertTriangle size={32} className="text-danger opacity-70" aria-hidden />
      <div>
        <h2 data-type="headline-s" className="text-on-surface">Couldn't load your {what}</h2>
        <p className="mt-1 max-w-[420px] text-on-surface-low text-[0.9375rem]">
          {/* The fallback used to read "Your ${what} are safe", which is ungrammatical for the many
              singular nouns callers pass ("Your project are safe"). Nothing on this component reads
              the count, so the noun cannot be pluralized reliably — the reassurance is stated once,
              noun-free, and it is just as true. The headline above already names what failed. */}
          {(error as Error)?.message
            || "The server didn't respond — this is just a load error, and nothing was lost."}
        </p>
      </div>
      {onRetry && (
        <Button size="sm" onClick={onRetry}><RotateCcw size={15} /> Retry</Button>
      )}
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
export function ListRow({ index = 0, onClick, children, accent, label }: {
  index?: number
  onClick?: () => void
  children: ReactNode
  accent?: string
  /** What this row IS, for assistive tech — usually the entity's title.
   *
   *  Without it a row's accessible name is computed from its subtree, so AT reads the
   *  whole card as one button name: measured across 170 rows on 7 surfaces, an inbox row
   *  averaged 318 characters and peaked at 2001, and a knowledge row averaged 685. That
   *  is unusable as a name — it is the row's content, announced where its identity
   *  belongs. Naming the row explicitly keeps the announcement to the thing itself; the
   *  content stays readable underneath as ordinary text.
   *
   *  Required in practice for every clickable row. It is optional in the type only
   *  because a handful of rows are non-interactive (no `onClick`), where there is no
   *  button to name. `listRowNaming.test.tsx` holds the interactive call sites to it. */
  label?: string
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
      // The row still HANDLES the click, because every nested control already calls
      // stopPropagation for itself (`ui/forms.tsx`'s Checkbox does it on both onClick and
      // onChange; the tag/run/delete buttons do it inline). Keeping the handler here is
      // what lets the button below stay a zero-content overlay instead of a wrapper that
      // has to re-expose its own descendants.
      onClick={onClick}
      // NO role/aria-label/onKeyDown on the wrapper. A `role="button"` that contains
      // focusable children is `nested-interactive` (axe, serious): AT is told "one button"
      // and then finds a checkbox and three tag filters inside it. 60 nodes across
      // knowledge (26) and workflows (34).
      //
      // tabIndex={-1} is REQUIRED, not leftover: `whileTap` makes Framer Motion set
      // tabindex="0" on the wrapper itself, so dropping the attribute entirely left TWO tab
      // stops per row (measured — Tab went bare-div, then overlay button). -1 keeps the
      // wrapper clickable and hoverable while the overlay owns the single tab stop.
      tabIndex={interactive ? -1 : undefined}
      //
      // The RING IS DRAWN ON THE ROW, keyed off the overlay's focus via `:has()`. Two
      // reasons it cannot live on the overlay itself: the overlay sits at `-z-10` so its
      // own ring paints BEHIND this element's background (measured — `boxShadow: none`
      // reached the screen), and the ring belongs on the row's rounded silhouette anyway.
      // `:has(> button:focus-visible)` is deliberately narrower than `focus-within`, which
      // would also light the row when the checkbox or a tag filter inside it takes focus
      // and double-ring with that control's own indicator.
      className={`group relative flex items-center gap-l overflow-hidden rounded-lg bg-surface-container px-l py-l text-left transition-colors hover:bg-surface-high ${interactive ? 'cursor-pointer has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary/50' : ''}`}
    >
      {accent && <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: accent }} />}
      {/* The row's tab stop and accessible name, as a real <button> SIBLING of the
          content rather than an ancestor of it.

          It is EMPTY and stretched over the row (`absolute inset-0`), which is what makes
          this safe: it owns no descendants, so nothing inside the row needs re-exposing.
          The alternative — keeping the wrapper interactive and marking children
          `pointer-events-auto` — has to enumerate every control type, and silently misses
          the CONDITIONAL ones (workflows' delete button exists only in its `armed` state;
          knowledge's tag filters are `hidden md:flex` and gated on `tags?.length`).
          Enumerating descendants is the part that breaks; owning none is the fix.

          `-z-10` puts it UNDER the row's content in paint order (the motion wrapper's
          transform makes it a stacking context, so a negative z-index child still paints
          above the row's own background). It therefore never covers the checkbox or the
          tag filters, and needs no z-index on any child. It does not have to receive the
          click either: Enter/Space on a real <button> fires a click that BUBBLES to the
          wrapper's onClick, and a pointer click anywhere in the row bubbles the same way. */}
      {interactive && (
        <button
          type="button"
          aria-label={label}
          className="absolute inset-0 -z-10 cursor-pointer outline-none"
        />
      )}
      {children}
    </motion.div>
  )
}

/** The bare-text loading state, for a slot too small or too irregular for a shaped skeleton.
 *
 *  🔴 It was a plain `<div>`: no role, so **nothing announced it** — the same defect cycle 143 fixed
 *  across every `aria-busy` skeleton, hiding in the one loading component that has no `aria-busy` and
 *  was therefore outside that census. Measured mid-load on `#/workflows` with `/api/**` held back:
 *  "Loading…" on screen for 2.8 seconds, `SPOKEN=[]`.
 *
 *  🔑 A live region here needs no sr-only twin: the text is already visible, so `role="status"` makes
 *  the words a sighted user reads the words everyone hears. `what` names the thing, matching
 *  `LoadingStatus`.
 *
 *  🪤 THIS IS THE LESSER IDIOM AND STAYS SO. `Skeleton`'s own doc says it exists "so the page appears
 *  instantly instead of a bare 'Loading…'", and ten list surfaces use `ListSkeleton`. Seven sites still
 *  use this; graduating each to a shaped placeholder is a per-site visual judgment, recorded rather
 *  than guessed at here. */
export function Loading({ what }: {
  /** What is loading — renders "Loading <what>…". Omit for a bare "Loading…". */
  what?: string
}) {
  return (
    <div role="status" aria-busy="true" className="text-on-surface-low text-[0.8125rem]">
      {what ? `Loading ${what}…` : 'Loading…'}
    </div>
  )
}

/** A single shimmering placeholder block. Use to render the SHAPE of content while
 *  a (cache-miss) fetch is in flight, so the page appears instantly instead of a
 *  bare "Loading…". `className` controls size/shape (height, width, rounding). */
export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton rounded-md ${className}`} aria-hidden="true" />
}

/** The half of a skeleton a screen reader can perceive.
 *
 *  🔴 Measured on a COLD load with every `/api/**` response held back: `#/tasks`, `#/artifacts` and
 *  `#/knowledge` all showed the skeleton region on screen — `role="status" aria-busy="true"
 *  aria-label="Loading"` — and **announced nothing at any point in the load**. A live region is
 *  announced by its CONTENT changing; the skeleton's content is styled `<div>`s with no text, and an
 *  `aria-label` is a NAME, not an announcement. So the region was perfectly marked up and completely
 *  silent, from the first frame to the moment the data arrived.
 *
 *  🔑 The result-count status (`ui/ListControls`' ResultAnnouncement) does not cover this: it speaks
 *  only when a query or filter is narrowing, which is deliberate — cycle 121 fixed the opposite defect,
 *  an idle surface announcing "39 items" unprompted. So on a first load there was no announcement of
 *  any kind, before OR after.
 *
 *  Renders the sr-only text that gives the region something to say.
 *
 *  🪤 AND IT REPLACES THE `aria-label` RATHER THAN JOINING IT. `role="status"` is NOT named from its
 *  content (name-from-content applies to button/link/heading, not to a live region), so the label was
 *  a SECOND hard-coded string saying the same word — one that can drift from the text that is actually
 *  announced. Measured while writing the rail: `getByRole('status', { name: 'Loading providers…' })`
 *  finds nothing, which is exactly why the announcement had to be content and not a label. */
export function LoadingStatus({ what }: { what?: string }) {
  return <span className="sr-only">{what ? `Loading ${what}…` : 'Loading…'}</span>
}

/** N placeholder rows shaped like ListRow — the default first-load state for list
 *  pages. Matches ListRow's padding/leading-icon so the swap to real data is calm. */
export function ListSkeleton({ rows = 6, what }: { rows?: number
  /** What is loading, for the announcement — "tasks", "prompts". Omit for a bare "Loading…". */
  what?: string }) {
  return (
    <div className="flex flex-col gap-s" role="status" aria-busy="true">
      <LoadingStatus what={what} />
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
export function FormSkeleton({ sections = 2, rows = 3, title = true, what }: { sections?: number; rows?: number; title?: boolean
  /** What is loading, for the announcement — "notification settings". Omit for a bare "Loading…". */
  what?: string }) {
  return (
    <div role="status" aria-busy="true">
      <LoadingStatus what={what} />
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
export function CardGridSkeleton({ cards = 4, cols = 2, title = true, what }: { cards?: number; cols?: number; title?: boolean
  /** What is loading, for the announcement. Omit for a bare "Loading…". */
  what?: string }) {
  return (
    <div role="status" aria-busy="true">
      <LoadingStatus what={what} />
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
