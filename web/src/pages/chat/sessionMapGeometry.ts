/** SESSION MAP GEOMETRY — how long each marker is painted (#4776).
 *
 *  One pure function decides every marker's painted length from three facts: which marker the
 *  pointer, the keyboard or a scrub is on (the TARGET), how far this marker is from it, and whether
 *  this marker's exchange is on screen. The rail, its tests and the design doc read these numbers
 *  from here, so the wave a sweep draws and the length a test measures cannot disagree.
 *
 *  Only the PAINTED line changes. Every row stays the same 32 x 24 target on the same pitch, so a
 *  marker growing never moves another marker's hit area.
 */

/** The longest a marker is painted: the target's length, in px. */
export const MARK_MAX_PX = 26

/** A resting marker whose exchange is off screen, in px. */
export const MARK_REST_PX = 6

/** A resting marker whose exchange is on screen, in px. Longer as well as stronger in tone, so
 *  which exchanges are in view is told by length too, not by colour alone. */
export const MARK_IN_VIEW_PX = 12

/** How much of the way to `MARK_MAX_PX` a marker grows at each distance from the target: the
 *  target itself, then one, two and three markers away. Past three, a marker rests. The falloff
 *  makes a sweep down the rail read as a local wave around the pointer rather than one line
 *  twitching. */
export const MARK_FALLOFF = [1, 0.7, 0.4, 0.2] as const

/** The painted length of marker `index`, in px, while `target` is the marker being pointed at
 *  (`null` when none is). */
export function markLength(index: number, target: number | null, inView: boolean): number {
  const rest = inView ? MARK_IN_VIEW_PX : MARK_REST_PX
  if (target === null) return rest
  const pull = MARK_FALLOFF[Math.abs(index - target)] ?? 0
  return rest + (MARK_MAX_PX - rest) * pull
}
