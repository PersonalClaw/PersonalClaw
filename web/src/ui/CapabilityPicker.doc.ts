import type { UiDoc } from './uiDoc'

// CapabilityPicker.tsx exports two related components (the picker row + its preview
// modal), so its doc default-exports an array — one UiDoc per exported component.
const docs: UiDoc[] = [
  {
    name: 'CapRow',
    keywords: ['capability', 'skill', 'workflow', 'row', 'checkbox', 'select', 'suggested', 'peek', 'picker'],
    description:
      'The shared selectable capability row (a skill or workflow) for the goal-loop and code-loop plan reviews. A checkbox + icon + name/description with an optional "suggested" chip; the full-width row body toggles selection, and an optional peek button (Eye) opens the CapabilityPeekModal to study the capability before committing it.',
    props: [
      { name: 'id', description: 'Stable identifier for the capability (skill key or workflow id); used as the row key.' },
      { name: 'name', description: 'Capability display name (shown bold, truncates).' },
      { name: 'description', description: 'Optional one-line summary shown under the name.' },
      { name: 'checked', description: 'Whether the capability is currently selected (drives the checkbox + selected ring/tint).' },
      { name: 'suggested', description: 'Show the "suggested" chip — a recommended capability the planner surfaced.' },
      { name: 'onToggle', description: 'Fires when the row body is clicked to toggle selection.' },
      { name: 'onPeek', description: 'Optional — renders the Eye peek button (only when provided) and opens the preview; stops propagation so it never also toggles selection.' },
      { name: 'icon', description: 'Leading icon node distinguishing skill vs workflow.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for CapRow for any skill/workflow selection row rather than hand-rolling a checkbox row — toggle, suggested chip, and the peek affordance come together.' },
      { guidance: true, description: 'Pass onPeek (paired with CapabilityPeekModal) so users can study a capability before committing it; omit it only when preview is unavailable.' },
      { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['row container (selected ring + tint)', 'toggle button (checkbox • icon • name/description • suggested chip)', 'peek button (Eye, hover/focus-revealed)'],
  },
  {
    name: 'CapabilityPeekModal',
    keywords: ['capability', 'preview', 'peek', 'skill', 'workflow', 'modal', 'markdown', 'steps'],
    description:
      "Previews the full content of a skill or workflow so the user can study it before committing it to a loop. Skills fetch and render their SKILL.md body; workflows render their steps (scope/tags + numbered instructions) from the in-hand item. Paired with CapRow's onPeek.",
    props: [
      { name: 'peek', description: "The capability to preview: { kind: 'skill' | 'workflow', skill?, workflow? }." },
      { name: 'onClose', description: 'Dismiss the modal.' },
    ],
    bestPractices: [
      { guidance: true, description: "Open it from CapRow's onPeek so the row and its preview stay in sync." },
      { guidance: true, description: 'Pass the matching skill or workflow item alongside kind — a skill lazy-loads its SKILL.md body, a workflow renders its steps from the passed item.' },
    ],
    anatomy: ['Modal (title + skill/workflow icon)', 'scroll region', 'skill branch: Markdown body (with loading state)', 'workflow branch: description + scope/tags chips + numbered steps'],
  },
]

export default docs
