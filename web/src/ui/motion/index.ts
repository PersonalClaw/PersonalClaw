// Shared motion/interaction primitives (component-redesign Slice 0). Import from
// here so per-component work is composition, not reinvention.
export { Expandable } from './Expandable'
export { Bud } from './Bud'
export { ContextMenu, type ContextMenuItem } from './ContextMenu'
export { Reorderable } from './Reorderable'
export { Disintegrate } from './Disintegrate'
export { Morph } from './Morph'
export { LiquidShape, type LiquidShapeName } from './LiquidShape'
// The morph family's shared timing vocabulary (atom FM-4) — exported because a caller
// occasionally has to hand the same curve to a sibling animation so the two don't drift.
export { MORPH_FAMILY, familySpring, familyTween, familyFade } from './vocabulary'
export { EntranceGroup, EntranceRegion } from './Entrance'
