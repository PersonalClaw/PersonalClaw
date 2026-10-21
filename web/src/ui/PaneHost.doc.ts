import type { UiDoc } from './uiDoc'

// Doc object for PaneHost: the one host for the detail a surface opens beside its work.
const doc: UiDoc = {
  name: 'PaneHost',
  keywords: ['pane', 'host', 'dock', 'split', 'detail', 'review', 'diff', 'resize', 'beside', 'tile', 'stack', 'compact'],
  description:
    "The shared host for the detail a surface opens beside its work: a review, a workflow detail, an output or a background task. It docks the pane on one side of the work, resizes it by pointer and by arrow keys, remembers its width for that surface, and gives focus back to the control the pane was opened from when it closes. Where its own box is narrower than the pane's smallest width beside the work's 12rem, it stacks the pane under the work, each at the box's full width, with no width handle; it goes back beside the work, at the remembered width, once the box has the room. The feature owns what the pane shows and whether it is open (from its address, where it keeps one); the host only presents it.",
  props: [
    { name: 'surface', description: "The surface the pane belongs to; its width is remembered for this surface alone (localStorage `pane-host-<surface>-w`)." },
    { name: 'dock', description: "The side of the work the pane docks on: 'right' (default) or 'left'. The resize handle sits on the edge facing the work." },
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
    { guidance: false, description: 'Do not write the pane\'s width or dock to the address; the host keeps the width itself, and the address names only the content.' },
    { guidance: true, description: 'Read the layout from `data-pane-layout` (`beside` or `stacked`, while a pane is open) on the box `data-pane-host="<surface>"` names, rather than from the window\'s width: the host decides from its own box, so a sidebar beside it takes its share first.' },
  ],
  anatomy: ['row: the work and the docked pane (a column, the pane under the work, on a compact box)', 'pane region (named by its title heading)', 'pinned header (icon • title • close)', 'body', 'resize separator on the edge facing the work (none while stacked)'],
}

export default doc
