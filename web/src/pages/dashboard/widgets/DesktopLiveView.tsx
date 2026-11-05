import { useCallback } from 'react'
import { api, type ComputerUseElement, type ComputerUseLiveView, type ComputerUseTrailPoint } from '../../../lib/api'
import { useQuery } from '../../../lib/data'
import { refreshKinds, useChatSocket, type WsMessage } from '../../../lib/useChatSocket'
import { useVisiblePoll } from '../../../lib/useVisiblePoll'
import { StatusPill, type StatusPillTone } from '../../../ui/StatusPill'
import { relPast } from '../../schedule/scheduleMeta'
import { epochSeconds } from '../../../lib/epoch'
import { WidgetRow } from './kit'

/** Desktop live view + cursor-motion overlay, shown on Home's Overview only while the agent is
 *  driving this desktop.
 *
 *  The human-facing half of desktop computer use: a wireframe of the window the agent last walked
 *  (the accessibility tree the model already read — this system reads trees, not pixels), a FAKE
 *  cursor where each approved action lands, and the feed of every attempt, allowed or refused,
 *  straight off the SEL. The fake cursor exists only in this rendering: it is not the real
 *  pointer, paints nothing on the driven desktop, and exposes nothing a `computer_snapshot` walk
 *  or any capture of that desktop could see. EVERYTHING here is a mirror served by one GET; the
 *  band can observe the capability and cannot exercise it, and the backend census
 *  (`tests/test_computer_use_live_view.py`) pins that the tool surface is unchanged.
 *
 *  🔑 ONLY WHILE LIVE. The gateway says when the desktop is in use (`live`: an approved action or a
 *  walk within the last 30 s, `computer_use/render.is_live`), and rings a `computer_use` refresh
 *  hint at each action, so the band appears when a drive starts without anything polling a desktop
 *  nobody drives. While it is live the band reads the view every few seconds, both the wireframe
 *  and the cursor, until the read says it is not. Whether desktop control is armed, and which apps
 *  it may drive, is said in Settings › Security, where it is configured; this band has no posture,
 *  no switches and no preference of its own. */

/** How fast the view refreshes while the desktop is live. A click trail is stale in seconds; still
 *  visibility-gated, and nothing at all while it is not live. */
const WATCH_POLL_MS = 2500

/** Trail points actually drawn. The server keeps more history; the overlay is about the
 *  motion happening now, and forty fading segments read as noise, not motion. */
const DRAWN_TRAIL = 12

const FEED_ROWS = 6

/** Compact "time ago" over the shared parser pair — ScheduleWidget's guard, same reason:
 *  arithmetic on an unparsed wire timestamp renders NaN, and an absent one must render
 *  nothing rather than an epoch-aged claim. */
function rel(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  return secs == null ? '' : relPast(secs)
}

function outcomeTone(outcome: string): StatusPillTone {
  if (outcome === 'approved' || outcome === 'completed') return 'ok'
  if (outcome === 'denied' || outcome === 'rejected' || outcome === 'failed') return 'danger'
  return 'neutral'
}

/** The desktop live view as the gateway serves it, whether the desktop is live now, and whether the
 *  last read failed. Read once when Home's Overview opens, again at each `computer_use` hint and
 *  after a reconnect, and every few seconds while it is live. A read that fails keeps the last
 *  answer: a band that was live stays, saying its read failed, until a read says otherwise, and one
 *  that was not never appears on a guess. */
export function useDesktopLive(): { view: ComputerUseLiveView | undefined; live: boolean; failed: boolean } {
  // `async`, so a call that throws as it is made is a failed read, never a thrown render.
  const { data, error, refresh } = useQuery('computer-use:live-view', async () => api.computerUseLiveView(), { persist: false })
  const live = data?.live === true
  const onMessage = useCallback((m: WsMessage) => {
    if (refreshKinds(m).includes('computer_use')) refresh()
  }, [refresh])
  useChatSocket(onMessage, refresh)
  // No idle back-off: a live mirror is watched WITHOUT input, and its cadence is the whole product.
  useVisiblePoll(refresh, live ? WATCH_POLL_MS : null, { idleBackoff: false })
  return { view: data, live, failed: Boolean(error) }
}

/** The drawing bounds: every mirrored frame plus every drawn trail point, padded. */
function stageBounds(boxes: ComputerUseElement[], points: ComputerUseTrailPoint[]) {
  const xs: number[] = []
  const ys: number[] = []
  for (const el of boxes) {
    if (!el.frame) continue
    xs.push(el.frame.x, el.frame.x + el.frame.width)
    ys.push(el.frame.y, el.frame.y + el.frame.height)
  }
  for (const p of points) {
    if (p.x == null || p.y == null) continue
    xs.push(p.x); ys.push(p.y)
  }
  if (xs.length === 0) return null
  const pad = 24
  const minX = Math.min(...xs) - pad
  const minY = Math.min(...ys) - pad
  return {
    minX, minY,
    width: Math.max(...xs) - minX + pad,
    height: Math.max(...ys) - minY + pad,
  }
}

export function DesktopLiveView({ view, failed = false }: { view: ComputerUseLiveView; failed?: boolean }) {
  // Tolerant reads on every wire array: a gateway older or newer than this widget (or a
  // partially-mocked test double) may omit any of them, and a dashboard widget must never
  // crash the page it decorates — degrade to drawing less instead.
  const newest = (view.snapshots ?? [])[0]
  const boxes = newest?.elements ? newest.elements.filter((el) => el.frame) : []
  const points = (view.trail ?? []).filter((p) => p.x != null && p.y != null).slice(-DRAWN_TRAIL)
  const bounds = stageBounds(boxes, points)
  const cursor = points.length > 0 ? points[points.length - 1] : null
  const feed = (view.feed ?? []).slice(0, FEED_ROWS)

  return (
    <div className="flex min-w-0 flex-col gap-s pt-xs">
      {/* The PiP stage: a wireframe of the tree the model already read, with the fake
          cursor + motion trail over it. Screen coordinates, so the two layers agree. */}
      {bounds && (
        <figure className="m-0 flex min-w-0 flex-col gap-xs">
          <svg
            role="img"
            aria-label={[
              boxes.length > 0 && newest ? `Live view of ${newest.app}` : 'Cursor motion trail',
              cursor ? `fake cursor over ${cursor.label || cursor.app}` : '',
            ].filter(Boolean).join(' — ')}
            viewBox={`${bounds.minX} ${bounds.minY} ${bounds.width} ${bounds.height}`}
            className="max-h-64 w-full rounded-lg bg-surface-low"
            preserveAspectRatio="xMidYMid meet"
          >
            {boxes.map((el) => (
              <g key={el.index}>
                <rect
                  x={el.frame!.x} y={el.frame!.y}
                  width={el.frame!.width} height={el.frame!.height}
                  rx={3}
                  fill="none"
                  stroke="var(--color-outline-variant)"
                  strokeWidth={1.5}
                  opacity={el.enabled ? 0.9 : 0.4}
                  vectorEffect="non-scaling-stroke"
                />
                {el.title && (
                  <text
                    x={el.frame!.x + 4} y={el.frame!.y + 12}
                    fontSize={10}
                    fill="var(--color-on-surface-low)"
                  >{el.title}</text>
                )}
              </g>
            ))}
            {points.length > 1 && (
              <polyline
                points={points.map((p) => `${p.x},${p.y}`).join(' ')}
                fill="none"
                stroke="var(--color-primary)"
                strokeWidth={1.5}
                strokeDasharray="4 3"
                opacity={0.6}
                vectorEffect="non-scaling-stroke"
              />
            )}
            {points.slice(0, -1).map((p, i) => (
              <circle
                key={p.seq}
                cx={p.x!} cy={p.y!} r={3}
                fill="var(--color-primary)"
                opacity={0.15 + (0.5 * (i + 1)) / points.length}
              />
            ))}
            {cursor && (
              // The FAKE cursor: an arrow at the newest landing. Only ever drawn here.
              <g aria-hidden="true">
                <circle cx={cursor.x!} cy={cursor.y!} r={9} fill="var(--color-primary)" opacity={0.2} />
                <path
                  d={`M ${cursor.x!} ${cursor.y!} l 0 14 l 3.5 -3.5 l 2.5 6 l 3 -1.5 l -2.5 -5.5 l 5 -0.5 z`}
                  fill="var(--color-primary)"
                  stroke="var(--color-surface)"
                  strokeWidth={1}
                />
              </g>
            )}
          </svg>
          <figcaption data-type="caption" className="text-on-surface-low">
            {boxes.length > 0 && newest
              ? `${newest.app} · walked ${newest.age_secs}s ago${newest.expired ? ' · index expired' : ''} · ${newest.element_count} elements`
              : 'Cursor motion trail'}
            {cursor && ' · the cursor drawn here is a fake — it never appears on your screen'}
          </figcaption>
        </figure>
      )}
      {failed && (
        <p role="status" data-type="body-s" className="text-on-surface-low">
          Couldn&rsquo;t read the desktop live view just now: this is what it showed last.
        </p>
      )}

      {/* The action feed: every attempt, allowed or refused, straight off the audit log. */}
      {feed.length > 0 && (
        <div className="flex flex-col gap-xs">
          {feed.map((row, i) => (
            <WidgetRow key={`${row.timestamp}-${i}`}>
              <div className="flex min-w-0 items-center gap-s">
                <StatusPill tone={outcomeTone(row.outcome)}>{row.outcome || 'unknown'}</StatusPill>
                <span data-type="label-m" className="truncate text-on-surface">{row.operation}</span>
                {row.app && (
                  <span data-type="body-s" className="truncate text-on-surface-var">{row.app}</span>
                )}
                <span className="flex-1" />
                {row.error && (
                  <span data-type="caption" className="truncate text-on-surface-low" title={row.error}>
                    {row.error}
                  </span>
                )}
                <span data-type="caption" className="shrink-0 text-on-surface-low">
                  {rel(row.timestamp)}
                </span>
              </div>
            </WidgetRow>
          ))}
        </div>
      )}
    </div>
  )
}
