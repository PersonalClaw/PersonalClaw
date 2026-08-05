/** Which sessions the terminal's two panes show, and how that pair survives a close.
 *
 *  `active` is the left pane, `split` the right one, and the pair carries an
 *  invariant the "Split right" toolbar control already enforces on the write path:
 *  the split names a LIVE session, DISTINCT from the active one (it picks
 *  `tabs.find((t) => t.id !== active)`). The render leans on the same invariant —
 *  it resolves a pane by testing `active` first, so a session that is both
 *  resolves to 'left' only. A collapsed pair therefore renders ONE pane under a
 *  toolbar that still offers "Close split": no error, nothing to see, only absence.
 *
 *  Closing a tab is the one path that can break the invariant, because closing the
 *  ACTIVE session promotes another tab into `active` and that promotion can land on
 *  the session `split` already shows. Kept as a pure function (no React) so the
 *  promote decision is unit-testable without mounting the xterm/WebSocket-heavy
 *  TerminalView tree, and so both panes resolve in ONE step — the page writes them
 *  as a single query patch, which keeps ?active/?split from ever landing in the
 *  history pointing at the same id. */

export interface PaneSelection {
  /** left pane — `''` once no sessions remain. */
  active: string
  /** right pane, or null when the split is closed. */
  split: string | null
}

/** Resolve both panes after `closed` was removed, given the ids that REMAIN (in
 *  tab-strip order).
 *
 *  Note what is deliberately NOT the test for the split: a tab count.
 *  `remaining.length < 2` catches only the two-tab case. With tabs [a,b,c],
 *  active=c and split=b — reachable by splitting right, then opening a third
 *  session — closing c promotes into the split while two tabs remain, so a count
 *  waves the collapsed pair straight through. Restating the invariant (live AND
 *  distinct) is what holds in every arity, and it subsumes the old
 *  `if (split === id)` guard: a closed session is no longer live. */
export function panesAfterClose(
  remaining: readonly string[],
  closed: string,
  panes: PaneSelection,
): PaneSelection {
  const active = panes.active === closed ? promote(remaining, panes.split) : panes.active
  const live = panes.split !== null && panes.split !== active && remaining.includes(panes.split)
  return { active, split: live ? panes.split : null }
}

/** Pick the session that takes over the vacated left pane: the last tab in strip
 *  order, but preferring one the split isn't already showing. Promoting the plain
 *  last tab would pull the split's session leftward and cost the split for no
 *  reason whenever a free session exists. When the split IS the only session left,
 *  it gets promoted and the split collapses — the caller's `live` test sees
 *  `split === active` and the toolbar honestly reads "Split right" again. */
function promote(remaining: readonly string[], split: string | null): string {
  const free = remaining.filter((id) => id !== split)
  return (free.length ? free : remaining).at(-1) ?? ''
}
