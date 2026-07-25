import type { UiDoc } from './uiDoc'

// Doc object for ProjectPicker — the compact project chooser for create flows.
// Authored: keywords, prose, per-prop descriptions, Do/Don't, anatomy. Prop
// type/required are DERIVED from ProjectPicker.tsx at build time.
const doc: UiDoc = {
  name: 'ProjectPicker',
  keywords: ['project', 'picker', 'chooser', 'dropdown', 'scope', 'listbox', 'create', 'select'],
  description:
    'A compact project chooser for the Goal Loop + Code create flows. The user picks which Project this work scopes under, or leaves it on the empty option (value ""), in which case the backend auto-creates one named from the goal/task at intake. Controlled by value (the project id; "" = auto/new); loads the project list lazily on first open so the create page paints instantly, and self-heals a stale/deleted id back to "".',
  props: [
    { name: 'value', description: 'The selected project id (controlled); `\'\'` means the empty (auto-new / no-project) option. A value naming a project that no longer exists resets itself to `\'\'`.' },
    { name: 'onChange', description: 'Fires with the chosen project id (or `\'\'` for the empty option). Also called automatically to reset to `\'\'` when a stale value names a deleted project.' },
    { name: 'disabled', description: 'Dim + block the trigger.' },
    { name: 'emptyLabel', description: "Label for the empty (\"\") option (default 'New project'). Chat passes 'No project' since an unbound chat scopes to nothing — same component, no dual path." },
    { name: 'emptyHint', description: "Muted suffix beside the empty-option label (default '(auto-named)') explaining the backend auto-create behavior." },
    { name: 'openSignal', description: 'Monotonic counter — each increment opens the picker (drives the "/project" slash command); mount / 0 ignored.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Drive it controlled: pass the project id as value and thread the onChange id into the create payload as `project_id`; treat `\'\'` as auto/new.' },
    { guidance: true, description: "Tune the empty option per flow via emptyLabel/emptyHint (loop: 'New project'/'(auto-named)'; chat: 'No project') rather than forking the component." },
    { guidance: true, description: 'Open it programmatically by incrementing openSignal (e.g. a "/project" slash command) instead of lifting the open state.' },
    { guidance: false, description: 'Do not hardcode colors or px — surfaces, radius, and shadow route through design tokens (the token-lint ratchet fails the build otherwise).' },
    { guidance: false, description: 'Do not offer archived projects as targets for new work — the picker already filters them out (except an already-selected one, which stays shown but not re-listed).' },
  ],
  anatomy: ['relative wrapper (outside-click / Escape boundary)', 'trigger pill (folder icon + current label + spring chevron)', 'AnimatePresence listbox (overlayEnter)', 'empty option (New project / auto-named)', 'grouped "Existing" project rows (check on selected, archived tag)'],
}

export default doc
