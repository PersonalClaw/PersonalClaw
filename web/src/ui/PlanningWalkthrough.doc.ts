import type { UiDoc } from './uiDoc'

// PlanningWalkthrough.tsx exports the walkthrough plus the ArtifactSection helper
// its per-kind renderers share, so its doc default-exports an array.
const docs: UiDoc[] = [
  {
    name: 'PlanningWalkthrough',
    keywords: ['planning', 'walkthrough', 'plan', 'steps', 'gate', 'approve', 'loop', 'artifact', 'planner'],
    description:
      "The shared live-breakdown + stepwise gated planning walkthrough used by BOTH the Code feature and Goal Loop (the vision's \"factored once, serves both\"). Split view: LEFT streams the planner agent's loop events (tool calls + a live sentence ticker); RIGHT is the ordered step rail with the current step's artifact and an Approve / Comment gate — Approve advances, a comment sends the step back for a re-draft, and when every step is approved the plan finalizes and the host hands off to Plan Review / launch. A step whose pass produced no draft shows the server's reason (`PlanStep.error`) with its Retry at once.",
    props: [
      { name: 'id', description: 'The loop/feature id being planned — keys the plan session, the polling loop, and the planner WS key.' },
      { name: 'cfg', description: 'The WalkthroughConfig injecting everything feature-specific: the planner WS session key, the plan API calls (getSession/start/approve/comment/edit/isReady/retry), the copy, and the per-kind artifact renderer. This is what makes one component serve both Code and Goal Loop.' },
      { name: 'onReady', description: 'Called when planning completes (the host entity flips to `review`) so the host hands off to Plan Review / launch.' },
      { name: 'onBack', description: 'Leave because the loop is gone: fired when the plan session 404s out from under the walkthrough (deleted elsewhere). Not fired while a Cancel or a Stop of its own is under way.' },
      { name: 'onCancel', description: 'The Cancel control (cfg.copy.cancel, "Cancel and edit the task"): the host deletes the draft, which ends its planner, and opens the composer with the task as typed. Resolves true once it has left, false when the cancel did not go through and the walkthrough stays.' },
      { name: 'onStop', description: 'The Stop control: the host confirms, stops the loop (its planner with it; the plan so far is kept) and opens the stopped loop\'s page. Resolves true once it has left, false when it did not stop.' },
      { name: 'approvals', description: 'What the planner is waiting on you for, shown at the top of the main column: the host passes the loop\'s approval cards (`LoopApprovals`), so an Attended loop\'s planner is answered on the page you plan it on.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for PlanningWalkthrough for any gated stepwise planning flow rather than rebuilding one — inject the feature specifics through cfg (WS key, API calls, copy, artifact renderer).' },
      { guidance: true, description: 'Provide cfg.api.retry when the host has a retry endpoint — retry CLEARS a recorded design failure so design re-runs, whereas start deliberately does not (so a passive remount cannot re-spawn a stuck planner). Without retry it falls back to start.' },
      { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
    ],
    anatomy: ['TopBar (adaptive header: Planning… / Awaiting your review / Planning paused; Stop and Cancel on the right)', 'MAIN column: planning-steps rail + current-step artifact gate (Approve / Send comment & redraft / in-place Edit)', 'SIDE rail: planner live activity (tool-call list + shimmering sentence ticker)'],
  },
  {
    name: 'ArtifactSection',
    keywords: ['artifact', 'section', 'label', 'header', 'planning', 'renderer'],
    description:
      "A labelled structured section within a planning artifact — an icon + label header over its body. Shared by both features' per-kind artifact renderers to keep sections (stories, decisions, entities…) visually consistent.",
    props: [
      { name: 'icon', description: "The section's leading icon node." },
      { name: 'label', description: 'The section heading text.' },
      { name: 'children', description: 'The section body content.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Use inside a per-kind artifact renderer to give each structured section a consistent labelled header rather than styling headings ad hoc.' },
    ],
    anatomy: ['header row (icon + uppercase-ish label)', 'section body (children)'],
  },
]

export default docs
