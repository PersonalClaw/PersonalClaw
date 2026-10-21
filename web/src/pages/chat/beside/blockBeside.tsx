import { Code2, FileDiff, SearchX, Table2 } from 'lucide-react'
import type { BesideBlock } from '../../../ui/OpenBeside'
import type { PaneContent } from '../../../ui/PaneHost'

// ── A block of a reply, beside the reading ────────────────────────────────────────────────────
//
// A code block, a diff or a table in a reply opens beside the turns, in the chat's pane host (#3820:
// "Code, tables, diffs, artifacts, and review can use an adjustable split that remembers its
// size"). The address keeps it: `?beside=<block>` is the split, and no `beside` is the thread read in
// its column alone, so the mode and the block are one value and cannot disagree. A block's address
// is its kind and a fingerprint of its content (`ui/OpenBeside`), so a reply names the same block
// live and after a reload. The pane shows the block the transcript registered under that address,
// drawn by the same components, so nothing is fetched to show it.

const ICONS = { code: Code2, diff: FileDiff, table: Table2 } as const

/** The pane for `address`, or null while nothing is open or the chat's turns have not loaded. An
 *  address naming no block of this chat opens a pane that says so, so it is never ignored without
 *  a word. */
export function besidePane({ address, block, ready, onClose }: {
  address: string | null
  /** The block the transcript registered under `address`, if it has one. */
  block: BesideBlock | null
  /** Whether the chat's turns are on screen, so a block missing from them is missing from the chat. */
  ready: boolean
  onClose: () => void
}): PaneContent | null {
  if (!address || !ready) return null
  if (!block) {
    return {
      id: address, title: 'Not in this chat', label: 'block', icon: SearchX, onClose,
      body: <p data-type="body-m" className="px-l py-m text-on-surface-var">That block is not in this chat.</p>,
    }
  }
  return {
    id: address, title: block.title, label: block.label, icon: ICONS[block.kind], onClose,
    body: (
      // The canonical form of a text scroll region: a tab stop, a group, and its own name rather than
      // the block's whole text (`design/scrollRegionNamed.test.tsx`).
      // Its end clears reading mode's folded composer, which floats over the column's foot: stacked,
      // the block is that foot (`--composer-fold-room` is set only on the chat column).
      <div tabIndex={0} role="group" aria-label={`${block.title}, beside the conversation`} className="h-full overflow-auto px-l pt-s"
        style={{ paddingBottom: 'calc(var(--spacing-s) + var(--composer-fold-room, 0px))' }}>
        {block.render()}
      </div>
    ),
  }
}
