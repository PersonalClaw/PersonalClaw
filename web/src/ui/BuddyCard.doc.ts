import type { UiDoc } from './uiDoc'

// Doc object for BuddyCard — the buddy's expanded card (docs/design/buddy/surfaces.md, #3893).
const doc: UiDoc = {
  name: 'BuddyCard',
  keywords: ['buddy', 'card', 'expanded', 'focused work', 'step window', 'in sight', 'running', 'approval', 'question', 'alert'],
  description:
    "The buddy's card, beside its orb: the focused work's name, its state in the machine's words and its exact phase; its run as a three-line step window (the step before, the current one, the next when known) with a trail of up to eight done marks and +N (lib/buddy/steps.ts); the alert when one shows, as the app's approval prompt unchanged (the waiting-list row's own fields and the host's choices, on the app's existing answer path), the chat's question card, or an error's Open and Dismiss, with +N waiting; every other piece of live work in sight, each with its agent orb, its kind's glyph, its one line, its exact phase, its last update and the route that opens it; and Open in PersonalClaw. It wires no Allow or Deny of its own, and nothing in it sends. When the focused work finishes, the step window goes and the work in sight takes its room. It stacks at 320, 390 and 412 px with 44 px targets there.",
  props: [
    { name: 'focus', description: 'The focused work, with its running-list kind, last update and route; null when nothing is in focus.' },
    { name: 'line', description: "The machine's line for what the card is about, cut to the card's 72 characters (lib/buddy/lines.ts)." },
    { name: 'state', description: "The focused orb's state, when it is not the focused work's phase (an alert's, a system state)." },
    { name: 'steps', description: "The focused work's step window, folded from its phase events." },
    { name: 'alert', description: "What waits on the owner: an approval's waiting-list row with the host's choices, a question's segment, or an error's work, reason and route." },
    { name: 'waiting', description: 'How many more wait behind the alert that shows.' },
    { name: 'inSight', description: 'Every other piece of live work, in start order; null leaves the section out, where one piece of work owns the screen (the phone’s view two).' },
    { name: 'finished', description: "The running list's last endings, each row's phase saying how it ended." },
    { name: 'interrupted', description: 'Work a restart cut off, which the gateway kept no ending of.' },
    { name: 'list', description: "The running list's own state: live, reconnecting or failed." },
    { name: 'countdown', description: 'Seconds before the card folds, while its countdown line shows.' },
    { name: 'openHref', description: 'Where Open in PersonalClaw goes.' },
    { name: 'orbSize', description: "The focused work's orb at the card's head: none beside the buddy's own orb (the default), 96 where one piece of work owns the screen (the phone's view two)." },
    { name: 'orbId', description: "The head orb's shared-element id, where the view before it drew the same orb (the phone's list row)." },
    { name: 'onAnswer', description: "Raises a question's reply or an error's dismiss; the buddy's presence turns it into the machine's input. An approval answers through its own choices." },
    { name: 'onHold', description: 'Says while the pointer or the keyboard focus is on the card, so a fold on a timer waits.' },
  ],
  bestPractices: [
    { guidance: true, description: "Pass the alert's waiting-list row whole: the brief is approvalRowFields' mapping, the same as the phone companion's." },
    { guidance: true, description: 'Fold the step window with lib/buddy/steps.ts, so a recorded run replays the same.' },
    { guidance: false, description: 'Do not build Allow or Deny in the card: pass the choices the app already answers with, unchanged.' },
    { guidance: false, description: 'Do not let the step window grow into a log: it is three lines and a trail.' },
  ],
  anatomy: [
    'div[role=group][data-buddy-card][data-card-done] — named by its header',
    'header — BuddyOrb at orbSize when set (a Morph when orbId is set), h3 name, p[data-card-line], the phase and last update',
    'div[data-step-window] — p[data-step-trail] (marks and +N), ol li[data-step-place=before|current|next]',
    'div[data-card-alert=approval|input|error] — ApprovalPrompt in a scrolling brief, QuestionCard, or Open and Dismiss; +N waiting',
    'section[data-in-sight-list] — li[data-in-sight][data-kind]: AgentOrb, glyph, the line as a link, the phase and last update; Just finished',
    'footer — Open in PersonalClaw, and the countdown line',
  ],
}

export default doc
