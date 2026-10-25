// ── Surfaces loaded under a fixed clock: the UI matrix's and the visual spec's ──────────────────

/** Surfaces loaded under a fixed clock, and the instant, for both specs that hold a surface to a
 *  committed baseline (`surfaces.spec.ts`, `visual.spec.ts`): a surface that draws the browser's
 *  today is one picture and one tree on every run only at one instant. The week grid draws the
 *  week of the browser's today, and which hour cells hold a fire (and so are controls) moves with
 *  the clock; a fixed instant makes it the same week, and the same cells, on every run. */
export const FIXED_CLOCK: Record<string, string> = {
  'triggers?view=week': '2026-11-02T09:00:00',
  // The feed groups its notes under Today, Yesterday and Earlier in the browser's zone, and a task
  // row says how long ago it changed. A zoned noon, so the same notes fall on the same days in any
  // zone a runner has.
  'inbox?filter=notifications': '2026-11-02T12:00:00Z',
  // The same page by its own route. It listed the live feed under the browser's real clock: the
  // note the gateway raises at boot fell under Today, and a run that crossed midnight UTC read it
  // under Yesterday (#3905, fd971285's run). It now reads the fixed feed under this clock, as the
  // filter does.
  notifications: '2026-11-02T12:00:00Z',
  'projects/p-0a1b2c3d?tab=tasks': '2026-11-02T12:00:00Z',
  // What waits on you says how long each item has waited.
  'mission-control': '2026-11-02T12:00:00Z',
  companion: '2026-11-02T12:00:00Z',
  'companion?approval=ap-0a1b2c3d': '2026-11-02T12:00:00Z',
  // The week strip names the seven days that end today, so the goldens made on a Saturday read
  // Sun to Sat and a refresh a day later Mon to Sun (run 40150040941, eight Learning shots).
  learning: '2026-11-02T12:00:00Z',
}
