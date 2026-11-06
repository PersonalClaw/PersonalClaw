import type { UiDoc } from './uiDoc'

// Doc object for HeaderSearch — the page's search, as a member of the header's action cluster.
const doc: UiDoc = {
  name: 'HeaderSearch',
  keywords: ['header', 'search', 'filter', 'query', 'field', 'topbar', 'cluster', 'responsive'],
  description:
    "The search that filters a page, in the page header's action cluster (HeaderActions), first in its row. It sheds with the cluster: a 256 px field at the FULL tier, a 192 px field at TEXT, and at ICON and OVERFLOW a Search button that opens the field in a popover beside it. It is never pushed into the … menu, since a search hidden in a menu is not found, and it ranks primary so it is among the last controls the row gives up. While it holds a query and shows as a button, the button carries a dot and names the query. Inside the cluster's measuring probes it draws an empty box of the field's width, never a second input.",
  props: [
    { name: 'value', description: 'The query.' },
    { name: 'onChange', description: 'Fires with the new query on every keystroke.' },
    { name: 'placeholder', description: "What the field searches, in the page's words (default 'Search')." },
    { name: 'label', description: 'The accessible name, where it should differ from the placeholder; defaults to it.' },
    { name: 'autoFocus', description: 'Focus the field on mount, at the tiers that draw one.' },
    { name: 'className', description: "Added to the shed form's Search button. Both forms have the compact widths' 44 px hit box (`TOUCH_TARGET`) already." },
    { name: 'tour', description: 'A product-tour anchor (`data-tour`), on the live field or its button and never on a probe, so the tour rings what is on screen (Settings home: `settings`).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Put the page search here, the first child of the HeaderActions cluster, and announce what it leaves with a ListStatus beside the body.' },
    { guidance: true, description: 'Keep the placeholder short enough for the 256 px field: it says what is searched, not how.' },
    { guidance: false, description: 'Do not draw a SearchField under the header for a page-level search: every page-level control lives in the header (app/pageControlsAreInTheHeader.test.ts).' },
  ],
  anatomy: ['tier-aware field (SearchField, lg)', 'shed form: Search button with a set dot', 'portaled Popover holding the field'],
}

export default doc
