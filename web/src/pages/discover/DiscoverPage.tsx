import { motion } from 'framer-motion'
import { reportingWrite } from '../../app/reportingWrite'
import { Compass, ArrowUpRight, Play, X } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { spring } from '../../design/motion'
import { EntranceGroup, EntranceRegion } from '../../ui/motion'
import { useCachedData } from '../../lib/useCachedData'
import { api, type DiscoverTip, type DiscoverTryIt } from '../../lib/api'
import type { RouteProps } from '../../app/useQueryState'
import { PageTitle } from '../../ui/PageTitle'
import { requestProductTour } from '../../app/onboarding/tourLaunch'
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
  // No `.catch(() => null)`. Swallowing the rejection made `data` falsy, which this render reads as
  // "Discover is off" — so a failed request did not merely say nothing, it made a FALSE CLAIM ABOUT A
  // SETTING and offered a CTA to "turn them back on in Settings › Legibility", a setting that is
  // already on. Measured against a 500 on `/api/legibility/discover` with a cold sessionStorage.
  // Letting the rejection through is what makes `error` — and the branch below — exist at all.
  const { data, error, refresh } = useCachedData(
    'discover', () => api.discover(), { persist: false },
  )

  // Dismiss persists server-side; on success refetch so the tip drops from every
  // area (and the "explored everything" empty state shows once the last one goes).
  const dismiss = async (id: string) => {
    if (!(await reportingWrite('dismiss that tip', () => api.dismissDiscoverTip(id)))) return
    refresh()
  }

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
      <div className="mx-auto flex flex-col gap-l px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {/* Deliberately OUTSIDE every branch below (T5.2). Discover is the progressive-
            disclosure arm, and the tour is the one thing on it that is never earned and
            never used up: a user who dismissed every tip, or who switched tips off
            entirely, must still be able to be shown around. So it is not a catalog tip —
            a server-authored tip carries a dismiss, and dismissing the tour would remove
            the only replay entry the product has. */}
        <ReplayTourCard />
        {data === undefined && error ? (
          // Before the loading branch, or a failed fetch spins the skeleton forever.
          <LoadError what="tips" error={error} onRetry={refresh} />
        ) : data === undefined ? (
          <ListSkeleton rows={6} what="tips" />
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
            hint="Nice. New tips will appear here as PersonalClaw grows — and anything you dismissed stays hidden. The tour above stays too."
          />
        ) : (
          // The hub's ENTRANCE GROUP (FLUID-MOTION §S3 T3.2) — the intro and each area
          // band cascade in rather than the whole catalog appearing at once. On THIS
          // surface the regions ARE the data, so the group sits on the loaded column
          // rather than above the branch (the replay rule in `ui/motion/Entrance`);
          // that is safe because a dismiss goes through `refresh()` on an unchanged
          // key, and `useCachedData` holds the last value on a same-key revalidation
          // instead of dropping back to `undefined` — so the branch never flips through
          // the skeleton and the group is never remounted. Areas are keyed by name,
          // never by index or count, so re-fetching cannot remount a surviving band.
          <EntranceGroup className="flex flex-col gap-2xl">
            {/* T5.2's copy pass: Discover is named as the disclosure arm beside the S2
                starter rail, so the two mechanisms read as one idea — the rail holds a
                surface back until you reach it, and this is where you find out it exists. */}
            <EntranceRegion>
              <p data-type="body-m" className="max-w-[520px] text-on-surface-var">
                The parts of PersonalClaw you haven&rsquo;t tried yet. Your sidebar starts short and
                grows as you open things &mdash; this is where you find out what else is there. Each
                tip links straight into the feature; dismiss any you&rsquo;re not interested in.
              </p>
            </EntranceRegion>
            {data.areas.map((group) => (
              <EntranceRegion key={group.area} className="min-w-0">
                <section className="flex min-w-0 flex-col gap-m">
                  <div className="flex items-center gap-s">
                    {/* `h2`, not `h3`: this is a section directly under the page's `PageTitle` h1, and
                        the page has no h2 at all, so every one of these five area headings was an
                        `h1 → h3` skip — WCAG 1.3.1, reported at both themes and at 390px. The rung is
                        settled elsewhere in the app: `#/dashboard`, `#/knowledge`, `#/inbox` and
                        `#/settings/sources` all put h2 directly under the h1. The 14 other `h3`s in
                        the tree sit INSIDE panels beneath an h2, where h3 is correct — so this is the
                        one that was drift.

                        Purely structural: the type comes from `data-type="label-l"`, never from the
                        tag, so nothing moves visually. */}
                    <h2 data-type="label-l" className="text-on-surface-var">{group.area}</h2>
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
              </EntranceRegion>
            ))}
          </EntranceGroup>
        )}
      </div>
    </WorkbenchLayout>
  )
}

/** "Replay the tour" (T5.2) — the one entry on this page that is not a catalog tip.
 *
 *  It has no dismiss and no earned/used-up state on purpose: it is the product's only
 *  replay entry for the guided walk, and Discover is the arm that has to keep working for
 *  a user who dismissed everything else. Clicking it hands a request to the shell (see
 *  `app/onboarding/tourLaunch.ts`) — this page does not host the tour, because the tour
 *  walks off this page onto chat, the inbox, the home approvals band and settings.
 *
 *  It also lands focus back here when the tour ends: `useFocusTrap` restores to whatever
 *  was focused before the overlay opened, which is this button. */
function ReplayTourCard() {
  return (
    <div className="flex items-center gap-m rounded-lg bg-surface-container px-l py-m">
      <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-lg"
        style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
        <Play size={18} className="text-primary" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p data-type="label-l" className="text-on-surface">Replay the tour</p>
        <p data-type="body-m" className="mt-xs text-on-surface-var">
          The two-minute walk through the sidebar, chat, the Inbox, approvals and Settings.
          Escape ends it at any point.
        </p>
      </div>
      <Button variant="tonal" size="sm" onClick={requestProductTour} className="shrink-0">
        Start the tour
      </Button>
    </div>
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
