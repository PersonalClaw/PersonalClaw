import { motion } from 'framer-motion'
import { reportingWrite } from '../../app/reportingWrite'
import { Compass, ArrowUpRight, Play, X, RotateCcw } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { spring } from '../../design/motion'
import { EntranceGroup, EntranceRegion } from '../../ui/motion'
import { useQuery } from '../../lib/data'
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
  const { data, error, refresh } = useQuery(
    'discover', () => api.discover(), { persist: false },
  )

  // Dismiss persists server-side; on success refetch so the tip drops from every
  // area (and the "explored everything" empty state shows once the last one goes).
  const dismiss = async (id: string) => {
    if (!(await reportingWrite('dismiss that tip', () => api.dismissDiscoverTip(id)))) return
    refresh()
  }

  // The way back (#452). Dismiss is one unconfirmed X on a card and used to be terminal —
  // "persisted forever" with no API, no list and no reset — so the product's only
  // onboarding surface could be removed by a reflex. Clear-all, not per-id: the user is
  // never shown WHICH tips they hid (that list is out of scope), so a per-id control would
  // ask them to choose from an invisible set. Goes through reportingWrite for the same
  // reason dismiss does: a failed restore must not be reported as a restore.
  const restore = async () => {
    if (!(await reportingWrite('restore your hidden tips', () => api.restoreDiscoverTips()))) return
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
                  className="inline-flex h-5 items-center rounded-pill px-s"
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
          // `visible_count: 0` has TWO causes and this branch used to congratulate the user
          // for the first regardless (#452). A tip auto-hides once you have used its area,
          // and an explicit dismiss hides it for good — so a user who hid all ten in their
          // first minute got "You've explored every part of PersonalClaw", and a user who
          // genuinely used every area got reassured about dismissals they never made. The
          // payload knows which happened; `dismissed_count` is the whole reason it is there.
          // Both sentences stay honest about what is recoverable, which is nothing: hiding a
          // tip is still one-way (see the residual on #452).
          data.dismissed_count > 0 ? (
            // Split again on `restorable_count`, because "you hid N" does not tell the user
            // whether bringing them back would show them anything. Restoring only reveals a
            // tip whose area is ALSO still unengaged, so a user who hid a tip and later used
            // that area has nothing to get back — and the two cases need different sentences,
            // not one hedge. Gating the button on `dismissed_count` here is exactly the inert
            // control the ruling on #452 named: it would write the settings file and change
            // nothing on screen.
            <EmptyState
              icon={Compass}
              title={`No tips left to show — you hid ${data.dismissed_count} of ${data.total}`}
              hint={
                data.restorable_count > 0
                  ? `The rest auto-hid once you used those areas. ${data.restorable_count === 1 ? 'One of the tips you hid is' : `${data.restorable_count} of the tips you hid are`} still unexplored — you can bring ${data.restorable_count === 1 ? 'it' : 'them'} back. The tour above always stays.`
                  : "The rest auto-hid once you used those areas — and you have since used every area you hid a tip for, so there is nothing left to bring back. New tips will appear here as PersonalClaw grows. The tour above always stays."
              }
              action={
                data.restorable_count > 0
                  ? { label: `Restore ${data.restorable_count} hidden ${data.restorable_count === 1 ? 'tip' : 'tips'}`, onClick: restore, icon: RotateCcw }
                  : undefined
              }
            />
          ) : (
            <EmptyState
              icon={Compass}
              title="You've explored every part of PersonalClaw"
              hint="Nice — every tip auto-hid because you have used its area. New tips will appear here as PersonalClaw grows. The tour above stays too."
            />
          )
        ) : (
          // The hub's ENTRANCE GROUP (FLUID-MOTION §S3 T3.2) — the intro and each area
          // band cascade in rather than the whole catalog appearing at once. On THIS
          // surface the regions ARE the data, so the group sits on the loaded column
          // rather than above the branch (the replay rule in `ui/motion/Entrance`);
          // that is safe because a dismiss goes through `refresh()` on an unchanged
          // key, and `useQuery` holds the last value on a same-key revalidation
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
              {/* The restore control also has to live HERE, not only in the empty state where
                  the count it gates on was specified. A user with two tips showing and five
                  hidden never reaches the empty branch, and they are precisely the user who
                  wants a dismissal back — putting the only way back behind "you have run out
                  of tips" would leave issue 452 half fixed. Same `restorable_count` gate, so it
                  appears only when it would actually reveal something. */}
              {data.restorable_count > 0 && (
                <Button variant="ghost" onClick={restore} className="self-start">
                  <RotateCcw size={16} />
                  Restore {data.restorable_count} hidden {data.restorable_count === 1 ? 'tip' : 'tips'}
                </Button>
              )}
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
