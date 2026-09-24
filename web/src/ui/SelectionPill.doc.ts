import type { UiDoc } from './uiDoc'

// Doc object for SelectionPill — the floating action pill anchored at a text
// selection. The "parent owns detection + positioning, forwards a ref, and
// preventDefault/stopPropagation fire before onPress so the selection survives"
// contract was a source comment, encoded here as machine-readable data.
const doc: UiDoc = {
  name: 'SelectionPill',
  keywords: ['selection', 'pill', 'toolbar', 'quote', 'copy', 'comment', 'floating', 'popover', 'highlight', 'transcript'],
  description:
    'The floating action pill anchored at a text selection inside a scrolling transcript/preview — the small "Quote" / "Comment" affordance that pops above a highlighted passage. The parent owns selection detection and positioning (content-relative x/y within its scroll root) and forwards a ref so it can exclude clicks on the pill from its own selection handlers. It preventDefaults + stopPropagates on mousedown before firing onPress, so the browser selection survives the click that acts on it. The sibling export SelectionToolbar is the multi-action variant (same anchoring + selection-survival contract) carrying 2+ segmented actions (e.g. Quote + Copy) in one pill.',
  props: [
    { name: 'icon', description: 'Leading Lucide icon (tinted primary), e.g. a quote/comment glyph.' },
    { name: 'label', description: 'The action label shown beside the icon (e.g. "Quote").' },
    { name: 'onPress', description: 'Fires on activation; runs AFTER preventDefault + stopPropagation so the text selection is still live when it executes.' },
    { name: 'x', description: 'Content-relative left position within the parent scroll root; the pill is centered horizontally on it.' },
    { name: 'y', description: 'Content-relative top position; the pill sits ABOVE this point (translated up by its full height).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Forward the ref the parent gives you (SelectionPill is forwardRef) so the parent can exclude clicks on the pill from its own mouseup/mousedown selection detection.' },
    { guidance: true, description: 'Position via content-relative x/y within the scroll root — the pill anchors above the passage (center-x, translate-up) so it clears the highlight.' },
    { guidance: false, description: 'Do not act on the selection in a plain onClick — the pill deliberately handles mousedown with preventDefault + stopPropagation so the browser selection is not cleared before onPress runs.' },
    { guidance: true, description: 'For more than one verb on a selection, use SelectionToolbar (segmented multi-action pill) instead of stacking multiple SelectionPills — one anchored surface, not several overlapping ones.' },
  ],
  anatomy: ['absolute button (rounded-pill surface-highest, shadow, ring), centered above the anchor point', 'primary-tinted leading icon', 'label'],
}

// Doc object for SelectionToolbar — the multi-action variant of SelectionPill.
const toolbarDoc: UiDoc = {
  name: 'SelectionToolbar',
  keywords: ['selection', 'toolbar', 'quote', 'copy', 'floating', 'popover', 'highlight', 'transcript', 'multi-action'],
  description:
    'The multi-action floating toolbar anchored at a text selection — the same anchoring + selection-survival contract as SelectionPill (content-relative x/y, ref-forwarded so the parent excludes its own clicks, mousedown preventDefault + stopPropagation so the selection survives), but carrying 2+ segmented actions (e.g. Quote + Copy) in one pill. Reach for it when a selection affords more than one verb; use SelectionPill for a single action.',
  props: [
    { name: 'actions', description: 'The ordered actions rendered as segmented buttons (each { icon, label, onPress }); each onPress runs after preventDefault + stopPropagation so the selection is still live.' },
    { name: 'x', description: 'Content-relative left position within the parent scroll root; the toolbar is centered horizontally on it.' },
    { name: 'y', description: 'Content-relative top position; the toolbar sits ABOVE this point (translated up by its full height).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Forward the ref the parent gives you (forwardRef) so the parent can exclude clicks on the toolbar from its own selection detection.' },
    { guidance: false, description: 'Do not act on the selection in a plain onClick — each action handles mousedown with preventDefault + stopPropagation so the browser selection is not cleared before onPress runs.' },
  ],
  anatomy: ['absolute rounded-pill container (surface-highest, shadow, ring)', 'segmented action buttons split by a hairline divider', 'each: primary-tinted leading icon + label'],
}

const docs: UiDoc[] = [doc, toolbarDoc]

export default docs
