import type { UiDoc } from './uiDoc'

// Doc object for ListControls — the on-PAGE list controls bar. The "controls
// belong on the page, not in the header" tenet (once a source comment) is encoded
// here as machine-readable bestPractices.
const doc: UiDoc = {
  name: 'ListControls',
  keywords: ['list', 'controls', 'search', 'filter', 'sort', 'toolbar', 'bar', 'segmented', 'page'],
  description:
    'The canonical on-PAGE controls bar for a list section — a search box, an optional single-select filter strip, and optional extra controls (sort, chips), pinned just below the TopBar and centered to the content width. List controls live HERE, on the page, not in the header (the header keeps only structural view-switches + the primary action). Renders nothing when empty, so it can be passed unconditionally.',
  props: [
    { name: 'search', description: 'Optional search-box config ({ value, onChange, placeholder?, label?, autoFocus? }); omit for a filter-only bar. A stable input name is derived from the label/placeholder.' },
    { name: 'filter', description: 'Optional single-select filter strip ({ value, onChange, options, ariaLabel? }) for status/kind/scope — NOT a view switch. Renders as a Segmented.' },
    { name: 'children', description: 'Extra controls (sort dropdown, filter chips) rendered after search + filter.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Put list search/filter/sort in ListControls, not in the TopBar — the header keeps only structural view-switches and the primary action.' },
    { guidance: true, description: "Render via WorkbenchLayout's `controls` slot (or inline above a body) so the bar scroll-pins below the TopBar and centers to the content width like every other list page." },
    { guidance: true, description: 'Use `filter` for a status/kind/scope narrowing strip; do not repurpose it as a view/mode switch (that belongs in the header).' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['bottom-bordered shrink-0 container', 'centered content-width row', 'SearchField (flex-1)', 'Segmented filter strip', 'extra children (sort / chips)'],
}

export default doc
