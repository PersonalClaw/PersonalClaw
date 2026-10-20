import type { UiDoc } from './uiDoc'

// Doc object for GraphZoomControls — the shared SVG-canvas zoom cluster.
const doc: UiDoc = {
  name: 'GraphZoomControls',
  keywords: ['zoom', 'pan', 'graph', 'canvas', 'controls', 'fit', 'reset', 'svg', 'overlay'],
  description:
    'Pan/zoom overlay controls shared by the SVG graph canvases (MemoryGraph, KnowledgeGraph): a glass-panel zoom-in / zoom-out / reset-to-fit cluster pinned bottom-right. Consolidated from two identical hand-rolled copies.',
  props: [
    { name: 'onZoomIn', description: 'Called when the + button is pressed — zoom the canvas in one step.' },
    { name: 'onZoomOut', description: 'Called when the − button is pressed — zoom the canvas out one step.' },
    { name: 'onReset', description: 'Called when the reset button is pressed — restore the fit-to-view transform.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for GraphZoomControls on any SVG graph canvas rather than re-hand-rolling zoom buttons — it is the consolidated shared cluster.' },
    { guidance: false, description: "Do not migrate these to the SquareIconButton primitive: they deliberately keep on-glass chrome because the primitive's hover:bg-surface-high would be nearly invisible on the bg-surface-high/90 blur panel." },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
  ],
  anatomy: ['absolutely-positioned glass panel (bottom-right, backdrop-blur)', 'zoom-in button (Plus)', 'zoom-out button (Minus)', 'reset-to-fit button (Maximize2)'],
}

export default doc
