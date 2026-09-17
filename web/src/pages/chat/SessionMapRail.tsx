import type { SessionMark } from './sessionMap'

/** SESSION MAP RAIL — the marks + track (SEMANTIC-SESSION-MAP §A.1/§A.5, atom SSM-4).
 *
 *  A narrow segmented vertical rail that indexes the transcript: one discrete tick per
 *  `SessionMark` (SSM-1's contract), laid along a subtle chrome spine. It is the visual floor
 *  the later atoms build on — current-region accent (SSM-5), the hover/focus preview card
 *  (SSM-6), keyboard/click jump (SSM-7), the hit-target + reduced-motion pass (SSM-8). This
 *  atom owns ONLY the marks + track: derivation lives in `sessionMapMarks`, so the rail is a
 *  pure function of the marks it is handed and holds no transcript logic of its own.
 *
 *  DESIGN LANGUAGE (web/DESIGN.md):
 *   · The track is `--color-rail` — the same chrome tone `NavRail` uses — so the spine reads as
 *     rail, not content (§A.1). It is a 1px hairline, never a colored side-stripe: the
 *     Tone-Not-Line rule keeps tone in the discrete marks (the nav targets), never a full-height
 *     colored bar (enforced by `sideStripeDoctrine.test.ts`).
 *   · Marks carry only the two-tone accent/history vocabulary this atom is scoped to. The
 *     CURRENT region paints `--color-primary` (One-Voice: coral means "the agent / live /
 *     current"); history paints `--color-on-surface-low` (neutral ink). Colour encodes
 *     position-in-session and nothing else — no decoration (web/DESIGN.md §Semantic). Every
 *     value routes through a token, so `tokenLint.test.ts` passes (no raw hex/px).
 *
 *  CURRENT REGION. Viewport tracking arrives with SSM-5's `IntersectionObserver`. Until then the
 *  newest turn is the current region — which is exactly the on-load state before any scroll, so
 *  this is the correct special case, not a placeholder. SSM-5 generalises it to "whatever is on
 *  screen" by driving the accented set; here it is derived from the marks alone.
 */
export interface SessionMapRailProps {
  /** The ordered marks from `sessionMapMarks` (SSM-1). The rail renders one tick per mark. */
  marks: SessionMark[]
}

export function SessionMapRail({ marks }: SessionMapRailProps) {
  // Self-suppression (§A.1): a map of fewer than two marks indexes nothing worth a rail.
  if (marks.length < 2) return null

  // The newest turn's coordinate is the current region (see CURRENT REGION above); every mark
  // that belongs to it — the turn mark and its sub-events — lights as current.
  const currentVisibleIndex = Math.max(...marks.map((m) => m.visibleIndex))
  const lastIndex = marks.length - 1

  return (
    <nav aria-label="Session map" className="relative flex h-full w-4 shrink-0 justify-center">
      {/* The chrome spine — a hairline in the rail tone. Decorative: the marks are the targets. */}
      <div
        aria-hidden
        data-session-map-track
        className="absolute inset-y-0 w-px rounded-pill"
        style={{ background: 'var(--color-rail)' }}
      />
      {/* One discrete tick per mark, spaced by ordinal (§A.2's pre-measure fallback; SSM-5 adds
          height-weighted spacing). Marks are aria-hidden here — the focusable jump target is
          introduced with the keyboard/click layer (SSM-7). */}
      {marks.map((mark, i) => {
        const isCurrent = mark.visibleIndex === currentVisibleIndex
        const pct = (i / lastIndex) * 100
        return (
          <span
            key={mark.markIndex}
            aria-hidden
            data-session-mark
            data-kind={mark.kind}
            data-current={isCurrent || undefined}
            className="absolute left-1/2 size-1 -translate-x-1/2 -translate-y-1/2 rounded-pill"
            style={{ top: `${pct}%`, background: isCurrent ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }}
          />
        )
      })}
    </nav>
  )
}
