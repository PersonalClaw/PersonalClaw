import type { UiDoc } from './uiDoc'

// Doc object for WidthPill — the content-width preset control in the top-right shell
// corner. It takes no props (reads/writes the appearance store directly). The
// hover-expand + absolute-anchoring behavior was a source comment.
const doc: UiDoc = {
  name: 'WidthPill',
  keywords: ['width', 'pill', 'preset', 'content', 'shell', 'corner', 'layout', 'appearance'],
  description:
    'The content-width preset control as a single-icon shell affordance in the top-right corner. Collapsed it shows just the active preset\'s icon (narrow / default / wide / full); on hover it expands vertically into all four options, and picking one re-flows the active page\'s content column and collapses back. Reads and writes the width preset on the appearance store directly, so it takes no props.',
  props: [],
  bestPractices: [
    { guidance: true, description: 'Drop WidthPill into the top-right shell corner cluster as-is — it is self-contained (state lives in the appearance store) and needs no wiring.' },
    { guidance: false, description: 'Do not pass or lift the width state — WidthPill owns it via useAppearance; duplicating it would desync the corner glyph from the applied preset.' },
    { guidance: false, description: 'Do not hardcode the highlight/menu colors — the sliding active indicator and menu surface use design tokens.' },
  ],
  anatomy: ['relative hover container', 'collapsed trigger (active preset icon, key-swap morph)', 'AnimatePresence menu (rounded-pill surface) with 4 option buttons', 'shared layoutId active indicator that slides between options'],
}

export default doc
