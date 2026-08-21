import type { UiDoc } from './uiDoc'

const doc: UiDoc = {
  name: 'WindowedList',
  keywords: [
    'window', 'windowing', 'virtualize', 'virtualization', 'overscan', 'long list',
    'performance', 'scroll', 'jank', 'rows', 'setsize', 'posinset', 'anchor', 'deep link',
  ],
  description:
    'The shared windowing primitive: renders only the rows in view plus a small overscan, so a list stops '
    + 'degrading as it grows. Measured on a real 5,000-row store under a 4x CPU throttle, an un-windowed list '
    + 'costs 35 DOM nodes per row and crosses one dropped scroll frame at 250 rows, three at 1,000 and 273ms '
    + 'per wheel event at 5,000. Below 64 rows it renders everything and is byte-identical to the plain '
    + '<div> it replaces — windowing 12 rows is pure overhead. Above it, the primitive keeps the five things '
    + 'naive virtualization silently breaks: keyboard nav reaches rows that were never rendered, a focused row '
    + 'survives scrolling out and back, aria-setsize stays the TRUE total, anchorKey deep-links to any row, and '
    + 'the one thing that cannot be preserved — browser find-in-page — is stated rather than dropped.',
  props: [
    { name: 'items', description: 'The FULL collection, never pre-sliced. Count semantics, keyboard reach and the anchor all read items.length; a pre-sliced list re-introduces the "20 of 5,000" defect this exists to prevent.' },
    { name: 'rowKey', description: 'Stable identity per row. Focus survival and anchorKey are keyed on it, so an index-derived key breaks both the moment the list re-sorts.' },
    { name: 'rowHeights', description: 'DECLARED per surface, never inferred: \'variable\' measures every mounted row with a ResizeObserver (log lines wrap; row padding rides the user\'s --space-scale), \'uniform\' takes estimateRowHeight as exact.' },
    { name: 'estimateRowHeight', description: 'Row height in px. Exact under \'uniform\'; under \'variable\' it is the first-paint estimate for rows not yet measured, which is what gives the scroll bar a sane length before anything has scrolled.' },
    { name: 'gap', description: 'The vertical gap the container\'s own class applies (gap-s → 8, gap-xs → 4). Folded into the offsets so the windowed scroll height matches what the un-windowed list produced.' },
    { name: 'noun', description: 'Plural noun for the count sentence — "chats", "items", "runs", "lines".' },
    { name: 'findHint', description: 'REQUIRED, and it must name a real in-app affordance ("use the Search chats field above"). Ctrl+F cannot see un-rendered rows and that cannot be fixed, so the alternative is STATED, announced with the true total, instead of a search box quietly going half-blind.' },
    { name: 'anchorKey', description: 'rowKey of a row to scroll to and focus once — the deep-link path. Changing it re-anchors; clearing it leaves the user\'s scroll alone.' },
    { name: 'overscan', description: 'Rows kept mounted above and below the viewport (default 8). This is what stops a fast scroll flashing empty; every extra row is back on the cost curve.' },
    { name: 'className', description: 'Applied to the list container — pass the same layout classes the un-windowed <div> had (flex flex-col gap-s), so a short list is unchanged.' },
    { name: 'enableRowKeyboard', description: 'Set false only for a list whose rows are not individually focusable (a log tail). Otherwise the primitive owns Arrow/Home/End/PageUp/PageDown and reaches un-rendered rows.' },
    { name: 'children', description: 'Render function (item, index, ctx). ctx.windowed says whether the window is engaged — adopters pass index={ctx.windowed ? 0 : i} to ListRow, because a windowed row REMOUNTS on every scroll-back and an index-keyed entrance stagger would replay the fade forever.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Declare rowHeights honestly. Four of the five adopting surfaces are \'variable\': ListRow has no h-* class, its padding is calc(16px * var(--space-scale)) with density presets at 0.8 and 0.68, and its meta line wraps. Only a list you have measured to be fixed may claim \'uniform\'.' },
    { guidance: true, description: 'Pass the FULL array and let the primitive window. A caller that slices first defeats the count semantics and the keyboard reach at once.' },
    { guidance: true, description: 'Keep the container class you had. The primitive renders one <div> with your className; short lists then produce the same DOM as before, which is why adopting it does not move a visual baseline.' },
    { guidance: false, description: 'Do not reach for an off-the-shelf virtualizer instead. The five preserved behaviours above are the reason this is ~300 lines of local code rather than a dependency: keyboard reach past the window edge, focus parking, true aria-setsize, anchor scroll and a stated find-in-page alternative are exactly what the popular libraries leave to the caller.' },
    { guidance: false, description: 'Do not announce the rendered count anywhere. aria-setsize is the true total by construction; a surface that also says "showing 28" has re-created the accessibility regression.' },
  ],
  anatomy: [
    'sr-only count + find-alternative sentence (windowed only)',
    'div role="list" tabIndex=-1 with paddingTop/paddingBottom standing in for the un-rendered rows',
    'div role="listitem" aria-posinset aria-setsize per rendered row',
  ],
}

export default doc
