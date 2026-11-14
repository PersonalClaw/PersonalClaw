import type { UiDoc } from './uiDoc'

// Doc object for ProjectPicker — the compact project chooser for create flows.
// Authored: keywords, prose, per-prop descriptions, Do/Don't, anatomy. Prop
// type/required are DERIVED from ProjectPicker.tsx at build time.
const doc: UiDoc = {
  name: 'ProjectPicker',
  keywords: ['project', 'picker', 'chooser', 'dropdown', 'scope', 'listbox', 'create', 'select'],
  description:
    'A compact project chooser for the create flows (a loop, a chat, a secret). The user picks which Project this work scopes under, or leaves it on the empty option (value ""), whose words say what that means for the flow. Its rows are every project list\'s (`pickerProjects`): the unarchived projects, each with its `ProjectMark` (sm), in `projectOrder` (the pins first, in their order, each with its pin at its end, then latest activity by the project\'s own `updated_at`, ties by name), the ones a loop made for itself after the owner\'s own, and "New project…" last, under a rule of its own, which opens the New project dialog and picks what it made, so the list never dead-ends. Controlled by value (the project id; "" = the empty option); loads the project list lazily on first open so the create page paints instantly, and self-heals a stale/deleted id back to "".',
  props: [
    { name: 'value', description: 'The selected project id (controlled); `\'\'` means the empty (auto-new / no-project) option. A value naming a project that no longer exists resets itself to `\'\'`.' },
    { name: 'onChange', description: 'Fires with the chosen project id (or `\'\'` for the empty option). Also called automatically to reset to `\'\'` when a stale value names a deleted project.' },
    { name: 'disabled', description: 'Dim + block the trigger.' },
    { name: 'emptyLabel', description: "Label for the empty (\"\") option (default 'No project': no row but the last starts with \"New project\"). Chat keeps it, since an unbound chat scopes to nothing; the loop composer passes what its kind does with none: 'Its own project (named from the task)' for a code session, 'Its own project (if its plan has sub-goals)' for a goal loop, 'No project' for research, design and general loops (`OWN_PROJECT`)." },
    { name: 'emptyMakesProject', description: "The empty option makes a project of its own (the loop composer's code session and goal loop, `OWN_PROJECT`): it draws the + of making one. Off by default, when it draws the projects glyph, as \"No project\" does." },
    { name: 'emptyHint', description: "Muted suffix beside the empty-option label, none by default: what the caller's flow does with no project, when its label does not already say it." },
    { name: 'openSignal', description: 'Monotonic counter — each increment opens the picker (drives the "/project" slash command); mount / 0 ignored.' },
    { name: 'align', description: "Which edge of the trigger the menu lines up with (default 'left'), as Popover's align: 'right' for a picker at the right of a row, such as a settings row's control, whose menu would otherwise run off the page." },
  ],
  bestPractices: [
    { guidance: true, description: 'Drive it controlled: pass the project id as value and thread the onChange id into the create payload as `project_id`; treat `\'\'` as auto/new.' },
    { guidance: true, description: "Tune the empty option per flow via emptyLabel/emptyHint (a loop: its kind's own words, `OWN_PROJECT`; chat: 'No project') rather than forking the component." },
    { guidance: true, description: 'List projects through `pickerProjects` wherever a list of projects is offered, so every list takes the same order and ends the same way.' },
    { guidance: true, description: 'Open it programmatically by incrementing openSignal (e.g. a "/project" slash command) instead of lifting the open state.' },
    { guidance: false, description: 'Do not hardcode colors or px — surfaces, radius, and shadow route through design tokens (the token-lint ratchet fails the build otherwise).' },
    { guidance: false, description: 'Do not offer archived projects as targets for new work — the picker already filters them out (except an already-selected one, which stays shown but not re-listed).' },
  ],
  anatomy: ['relative wrapper (outside-click / Escape boundary)', 'trigger pill (folder icon + current label + spring chevron)', 'AnimatePresence listbox (overlayEnter)', 'empty option (its flow\'s words)', 'a 1 px rule, then the project rows (ProjectMark sm, check on selected, archived tag)', 'a 1 px rule, then the "New project…" row (opens the New project dialog)'],
}

export default doc
