import type { UiDoc } from './uiDoc'

// Doc objects for the preset-first empty state (PEP-1) — the on-ramp pattern for a
// surface whose create flow front-loads its whole model.
const docs: UiDoc[] = [
  {
    name: 'PresetEmptyState',
    keywords: ['empty', 'preset', 'template', 'onboarding', 'on-ramp', 'starter', 'progressive disclosure'],
    description:
      'The preset-first empty state: a headline, a hint, a grid of PresetCards that SEED the surface\'s existing create flow, and a footer slot for the expert blank path. An empty list is the one moment a newcomer has no model of what the surface makes, so it offers finished examples instead of a blank form over the whole ontology. Distinct from EmptyState (ListScaffold), which states a fact and offers one CTA.',
    props: [
      { name: 'title', description: 'The headline — the same "No <entity>" line EmptyState would show.' },
      { name: 'hint', description: 'One line under the headline saying what the presets are and that they only seed the form.' },
      { name: 'presets', description: 'The preset catalog for this surface (PresetDef<P>[]) — data, not markup, so the cadence/summary can be derived rather than frozen as copy.' },
      { name: 'onPick', description: 'Called with the chosen preset\'s `prefill` verbatim; the surface routes that into its own create flow.' },
      { name: 'footer', description: 'The expert escape hatch rendered under the grid — a blank-create Button. The blank path must never be removed, only joined.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Show presets ONLY for the genuinely-empty case; a filtered-to-nothing list gets the plain no-match EmptyState, because a preset answers a question that user did not ask.' },
      { guidance: true, description: 'Keep the blank-create path reachable (top-bar action and/or `footer`) — presets seed the existing form and never replace it.' },
      { guidance: false, description: 'Do not hand-write the cadence/summary line per locale; derive it from a structured cadence through Intl so the copy is not frozen en-US.' },
    ],
    anatomy: ['centered headline + hint', 'responsive 1/2-column grid of PresetCards', 'footer slot (blank-create escape)'],
  },
  {
    name: 'PresetCard',
    keywords: ['preset', 'card', 'tile', 'template', 'cadence', 'starter'],
    description:
      'One preset card — icon, title, cadence/summary line, description — that hands its `prefill` back on activation. Chrome and button semantics come from TileButton, so it inherits the kit card look and focus-visible ring. The whole card is one tab stop and one click target; its content is text and icons only.',
    props: [
      { name: 'icon', description: 'A lucide icon for the preset, drawn in a primary-tinted rounded square.' },
      { name: 'title', description: 'The preset name ("Morning briefing").' },
      { name: 'summary', description: 'The cadence/summary line under the title ("Every day · 8:00 AM"), in the accent tone.' },
      { name: 'description', description: 'One or two lines on what the preset does once saved.' },
      { name: 'prefill', description: 'The surface-owned payload handed back verbatim to onPick; opaque to the card.' },
      { name: 'onPick', description: 'Activation handler, called with `prefill`.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Keep the card content non-interactive — a button inside a button is nested-interactive (axe, serious) and announces one control while offering two.' },
      { guidance: false, description: 'Do not let the card name itself from its own prose; it passes TileButton an ariaLabel of "<title> — <summary>" so the name is what the card will do.' },
    ],
    anatomy: ['TileButton container (border, hover, focus-visible ring, aria semantics)', 'tinted icon square', 'title', 'cadence/summary line', 'description'],
  },
]

export default docs
