import { useState } from 'react'
import { Monitor } from 'lucide-react'
import { api, type ComputerUseElement, type ComputerUseTrailPoint } from '../../../lib/api'
import { useQuery } from '../../../lib/data'
import { useVisiblePoll } from '../../../lib/useVisiblePoll'
import { StatusPill, type StatusPillTone } from '../../../ui/StatusPill'
import { Toggle } from '../../../ui/Toggle'
import { relPast } from '../../schedule/scheduleMeta'
import { epochSeconds } from '../../../lib/epoch'
import { SlotEmptyState, WidgetRow } from './kit'

/** Desktop live view + cursor-motion overlay (DESKTOP-COMPUTER-USE §3.7, DCU-7).
 *
 *  The human-facing half of desktop computer use: an action feed of every attempt (allowed
 *  or refused, straight off the SEL), an optional live-view mirror of the window the agent
 *  last walked (the accessibility tree the model already read — this system reads trees,
 *  not pixels), and an optional cursor-motion overlay that draws a FAKE cursor where each
 *  approved action lands. The fake cursor exists only in this rendering: it is not the real
 *  pointer, paints nothing on the driven desktop, and exposes nothing a `computer_snapshot`
 *  walk or any capture of that desktop could see — which is §3 floor 7's "invisible to
 *  screen capture" satisfied by construction. EVERYTHING here is a mirror served by one GET;
 *  the widget can observe the capability and cannot exercise it, and the backend census
 *  (`tests/test_computer_use_live_view.py`) pins that the tool surface is unchanged with
 *  these views on.
 *
 *  🔑 THE POLL IS AN ARGUED EXCEPTION to DashboardLive's "no widget opens its own poll".
 *  That doctrine exists so the always-on widgets share one connection and one cadence — and
 *  this surface is the opposite shape: OFF by default, polled only while the operator has a
 *  view toggled on AND the tab is visible (`useVisiblePoll(_, null)` otherwise), and its
 *  slice is consumed by no other widget. Folding it into the shared FAST_POLL would charge
 *  every dashboard visitor a per-tick request for a capability most installs never arm. */

const MIRROR_KEY = 'computerUse.liveView'
const OVERLAY_KEY = 'computerUse.cursorOverlay'

/** How fast the mirror refreshes while a view is on. Faster than FAST_POLL because a click
 *  trail is stale in seconds; still visibility-gated, and zero when both views are off. */
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

function readPref(key: string): boolean {
  try { return localStorage.getItem(key) === '1' } catch { return false }
}
function writePref(key: string, on: boolean) {
  try { localStorage.setItem(key, on ? '1' : '0') } catch { /* opaque origin — session-only */ }
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

export function DesktopLiveView() {
  const [mirrorOn, setMirrorOn] = useState(() => readPref(MIRROR_KEY))
  const [overlayOn, setOverlayOn] = useState(() => readPref(OVERLAY_KEY))
  const watching = mirrorOn || overlayOn

  const { data, error, refresh } = useQuery('computer-use:live-view', () =>
    api.computerUseLiveView(), { persist: false },
  )
  // Poll ONLY while a view is on and the tab visible — see the header comment for why this
  // widget carries its own (gated) poll instead of a DashboardLive slice.
  useVisiblePoll(refresh, watching ? WATCH_POLL_MS : null)

  // A failed read must not render as a quiet desktop: "the agent is doing nothing" and
  // "the gateway didn't answer" are different facts, and one of them is about your machine.
  if (!data && error) {
    return (
      <SlotEmptyState icon={Monitor}>
        Couldn&rsquo;t read the desktop live view.
      </SlotEmptyState>
    )
  }
  if (!data) return null

  const setMirror = (on: boolean) => { setMirrorOn(on); writePref(MIRROR_KEY, on); if (on) refresh() }
  const setOverlay = (on: boolean) => { setOverlayOn(on); writePref(OVERLAY_KEY, on); if (on) refresh() }

  // Tolerant reads on every wire array: a gateway older or newer than this widget (or a
  // partially-mocked test double) may omit any of them, and a dashboard widget must never
  // crash the page it decorates — degrade to the empty state instead.
  const newest = (data.snapshots ?? [])[0]
  const boxes = mirrorOn && newest?.elements ? newest.elements.filter((el) => el.frame) : []
  const drawable = (data.trail ?? []).filter((p) => p.x != null && p.y != null)
  const points = overlayOn ? drawable.slice(-DRAWN_TRAIL) : []
  const bounds = watching ? stageBounds(boxes, points) : null
  const cursor = points.length > 0 ? points[points.length - 1] : null
  const feed = (data.feed ?? []).slice(0, FEED_ROWS)

  return (
    <div className="flex min-w-0 flex-col gap-s pt-xs">
      {/* Posture + the two optional views. The pills state facts; the toggles change only
          what THIS page draws — they configure no capability, which is the whole atom. */}
      <div className="flex flex-wrap items-center gap-m">
        <StatusPill tone={data.enabled ? 'ok' : 'neutral'}>
          {data.enabled ? 'Armed' : 'Off'}
        </StatusPill>
        {data.enabled && (
          <span data-type="caption" className="text-on-surface-low">
            {data.allowed_apps.length === 0
              ? 'no apps allowlisted'
              : `may drive: ${data.allowed_apps.join(', ')}`}
          </span>
        )}
        <span className="flex-1" />
        <Toggle size="sm" on={mirrorOn} onChange={setMirror} label="Live view" />
        <Toggle size="sm" on={overlayOn} onChange={setOverlay} label="Cursor overlay" />
      </div>

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
            {overlayOn && ' · the cursor drawn here is a fake — it never appears on your screen'}
          </figcaption>
        </figure>
      )}
      {watching && !bounds && (
        <p data-type="body-s" className="text-on-surface-low">
          Nothing to draw yet — the mirror fills in when the agent walks a window or acts.
        </p>
      )}

      {/* The action feed: every attempt, allowed or refused, straight off the audit log.
          One SlotEmptyState per branch, each with a LITERAL body — the slot-empty-state
          census reads the text between the tags, and a {ternary} body extracts as "". */}
      {feed.length === 0 && data.enabled && (
        <SlotEmptyState icon={Monitor}>
          No desktop activity yet. Actions appear here the moment the agent drives an
          allowlisted app.
        </SlotEmptyState>
      )}
      {feed.length === 0 && !data.enabled && (
        <SlotEmptyState icon={Monitor}>
          Desktop computer use is off. If you arm it (an out-of-band step no agent can
          take), every action shows up here live.
        </SlotEmptyState>
      )}
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
