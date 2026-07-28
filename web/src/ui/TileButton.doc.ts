import type { UiDoc } from './uiDoc'

// Doc object for TileButton — the block-level clickable card/tile.
const doc: UiDoc = {
  name: 'TileButton',
  keywords: ['tile', 'card', 'grid', 'gallery', 'clickable', 'library', 'thumbnail'],
  description:
    'A block-level clickable CARD/TILE — the kit\'s home for "the whole card is one click target" (library grids, gallery tiles). Distinct from Button (an inline CTA pill) and QuietButton (a compact toolbar action): a TileButton is a bordered container whose CHILDREN are the content (a preview area, title rows); it owns only the card chrome — border, radius, hover fill, the active selection ring — and the accessible button semantics.',
  props: [
    { name: 'children', description: 'The card content — preview area, title/metadata rows. The tile owns only the chrome around them.' },
    { name: 'onClick', description: 'Activation handler; the whole tile is the click target.' },
    { name: 'active', description: 'Marks the tile as the current selection (primary-tinted border).' },
    { name: 'title', description: 'Supplementary native tooltip.' },
    { name: 'className', description: 'Extra classes (tokens only — no raw hex/px).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Use for grids/galleries where the entire card opens one thing; nested actions inside the tile must stopPropagation.' },
    { guidance: false, description: 'Do not use for inline CTAs (Button) or compact toolbar actions (QuietButton) — a TileButton is a container, not a labeled pill.' },
  ],
  anatomy: ['bordered rounded-xl button container (surface-container, hover border, active primary ring)', 'caller-supplied children (preview + text rows)'],
}

export default doc
