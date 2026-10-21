import type { UiDoc } from './uiDoc'

// Doc object for OpenBesideProvider: the place beside the reading that Markdown's blocks open in.
const doc: UiDoc = {
  name: 'OpenBesideProvider',
  keywords: ['beside', 'split', 'open', 'code', 'table', 'diff', 'pane', 'reading'],
  description:
    'Gives the rendered text under it a place beside the reading. A `Markdown` with `beside` then offers each code block, diff and table an Open beside (on hover and on focus, always on a screen that cannot hover, and not while the text streams) that calls `open` with the block\'s address: its kind and a fingerprint of its parsed content, so a reply names the same block live and after a reload. Each such block registers in `registry` under that address while it is mounted, and the surface draws the registered block (`useBesideBlock`), by the same components. Without a provider no block offers it. The chat provides it and opens the block in its pane host under `?beside=`.',
  props: [
    { name: 'open', description: 'Opens the block the address names beside the reading. Keep it stable (a `useCallback` over a ref): every block that offers Open beside reads it.' },
    { name: 'registry', description: 'Where the blocks on screen register (`new BesideRegistry()`, kept for the surface\'s life). The surface reads the open block from it with `useBesideBlock(registry, address)`.' },
    { name: 'children', description: 'The surface whose rendered text may open blocks beside its reading.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Keep the open block\'s address in the surface\'s own address and draw it from the registry, so a reload, Back and a copied link open the same block once the text is on screen again.' },
    { guidance: false, description: 'Do not offer it where the surface has nowhere beside its reading to put the block; the provider is what turns the offer on.' },
  ],
  anatomy: ['context provider (no markup of its own)'],
}

export default doc
