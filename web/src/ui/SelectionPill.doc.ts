import type { UiDoc } from './uiDoc'

// Doc object for SelectionPill — the floating action pill anchored at a text
// selection. The "parent owns detection + positioning, forwards a ref, and
// preventDefault/stopPropagation fire before onPress so the selection survives"
// contract was a source comment, encoded here as machine-readable data.
const doc: UiDoc = {
  name: 'SelectionPill',
  keywords: ['selection', 'pill', 'quote', 'comment', 'floating', 'popover', 'highlight', 'transcript'],
  description:
    'The floating action pill anchored at a text selection inside a scrolling transcript/preview — the small "Quote" / "Comment" affordance that pops above a highlighted passage. The parent owns selection detection and positioning (content-relative x/y within its scroll root) and forwards a ref so it can exclude clicks on the pill from its own selection handlers. It preventDefaults + stopPropagates on mousedown before firing onPress, so the browser selection survives the click that acts on it.',
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
  ],
  anatomy: ['absolute button (rounded-pill surface-highest, shadow, ring), centered above the anchor point', 'primary-tinted leading icon', 'label'],
}

export default doc
