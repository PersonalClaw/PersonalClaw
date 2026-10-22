import type { UiDoc } from './uiDoc'

// Doc object for PaneHost: the one host for the detail a surface opens beside its work.
const doc: UiDoc = {
  name: 'PaneHost',
  keywords: ['pane', 'host', 'dock', 'split', 'detail', 'review', 'diff', 'resize', 'beside', 'tile', 'stack', 'compact'],
  description:
    "The shared host for the detail a surface opens beside its work: a review, a workflow detail, an output or a background task. It docks the pane on one side of the work, resizes it by pointer and by arrow keys, expands it over the work and collapses it back, moves it to the other side (Move then Place here, or a keyboard move mode: arrows choose, Enter places, Esc cancels, each said in its live region), remembers its width and side for that surface, closes it on Esc outside a field (collapsing an expanded one first), and gives focus back to the control the pane was opened from when it closes. Where its own box is narrower than the pane's smallest width beside the work's 12rem, it stacks the pane under the work, each at the box's full width, with no width handle; it goes back beside the work, at the remembered width, once the box has the room. The feature owns what the pane shows and whether it is open (from its address, where it keeps one); the host only presents it.",
  props: [
    { name: 'surface', description: "The surface the pane belongs to; its width is remembered for this surface alone (localStorage `pane-host-<surface>-w`)." },
    { name: 'dock', description: "The side of the work the pane docks on until a person moves it: 'right' (default) or 'left'. A side they move it to is remembered for the surface (localStorage `pane-host-<surface>-dock`). The resize handle sits on the edge facing the work." },
    { name: 'size', description: 'The default, smallest and largest pane width in px (default 480, 320 and 960).' },
    { name: 'pane', description: 'What the pane shows — { id, title, label, icon?, onClose, body } — or null while it is closed. A new id is a new place focus came from.' },
    { name: 'stackInto', description: 'Where a stacked pane goes: an element inside the work before what the work keeps at its foot, best a `display: contents` slot in the work\'s column. Without one, a stacked pane goes under the whole work.' },
    { name: 'children', description: 'The surface\'s work, beside the pane. It stays mounted while the pane opens, changes and closes.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Keep the open content in the feature\'s own address and pass the host what the address names, so a reload, Back or a copied link opens the same pane under that feature\'s address rules.' },
    { guidance: true, description: 'Give each surface its own `surface` id, so a width chosen for one surface never resizes another.' },
    { guidance: true, description: 'Name the pane with `label` (e.g. "diff"), which names its Close and Resize controls.' },
    { guidance: false, description: 'Do not put a consequential action (stop, approve, commit) on the host: every host control is presentation only, and such an action stays inside the content.' },
    { guidance: false, description: 'Do not write the pane\'s width or dock to the address; the host keeps both itself, and the address names only the content.' },
    { guidance: true, description: 'Keep what a person writes in the pane (a comment draft) in the feature\'s own store: a move draws the pane on its new side afresh, while the work beside it stays mounted.' },
    { guidance: true, description: 'Read the layout from `data-pane-layout` (`beside` or `stacked`, while a pane is open) on the box `data-pane-host="<surface>"` names, rather than from the window\'s width: the host decides from its own box, so a sidebar beside it takes its share first.' },
  ],
  anatomy: ['row: the work and the docked pane (a column, the pane under the work, on a compact box; the pane alone, the work hidden but mounted, while expanded)', 'pane region (named by its title heading, described by where it is docked)', 'pinned header (icon • title • Move • Expand or Collapse • Close; Place here • Cancel the move while a move is under way)', 'live region saying a move\'s preview, its placing or its cancel', 'body', 'resize separator on the edge facing the work (none while stacked or expanded)'],
}

export default doc
