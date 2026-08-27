import type { UiDoc } from './uiDoc'

// Doc object for TileButton — the block-level clickable card/tile.
const doc: UiDoc = {
  name: 'TileButton',
  keywords: ['tile', 'card', 'grid', 'gallery', 'clickable', 'library', 'thumbnail'],
  description:
    'A block-level clickable CARD/TILE — the kit\'s home for "the whole card is one click target" (library grids, gallery tiles). Distinct from Button (an inline CTA pill) and QuietButton (a compact toolbar action): a TileButton is a bordered container whose CHILDREN are the content (a preview area, title rows); it owns only the card chrome — border, radius, hover fill, the active selection ring — and the accessible button semantics.',
  props: [
      { name: 'ariaLabel', description: "The accessible name, for a tile whose CONTENT is a document rather than a label. Measured in Chrome's computed accessibility tree on #/artifacts: five tiles named by 438-695 characters of their own rendered markdown preview, heading and emphasis markers included — a button with content takes its name from that content, and `title` loses to it. Pass the thing the tile IS." },
    { name: 'children', description: 'The card content — preview area, title/metadata rows. The tile owns only the chrome around them.' },
    { name: 'onClick', description: 'Activation handler; the whole tile is the click target.' },
    { name: 'active', description: 'Marks the tile as the current selection (primary-tinted border).' },
    { name: 'title', description: 'Supplementary native tooltip.' },
    { name: 'className', description: 'Extra classes (tokens only — no raw hex/px).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Use for grids/galleries where the entire card opens one thing; nested actions inside the tile must stopPropagation.' },
    { guidance: false, description: 'Do not use for inline CTAs (Button) or compact toolbar actions (QuietButton) — a TileButton is a container, not a labeled pill.' },
    { guidance: false, description: 'Do not add your own whileTap/scale press animation at the call site: the primitive already springs in on press (expressiveness-scaled, dropped under reduced motion), and a second transform on the same element fights it.' },
  ],
  anatomy: [
    'bordered rounded-xl motion.button container (surface-container, hover border, active primary ring, expressiveness-scaled press spring on the fast spatial preset)',
    'caller-supplied children (preview + text rows)',
  ],
}

export default doc
