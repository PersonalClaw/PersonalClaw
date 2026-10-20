import type { UiDoc } from './uiDoc'

// Doc objects for the local-model wait notice. A model on this machine answers one request at a
// time, so a request somebody is waiting for can wait for the call already running; these say why,
// what holds the model, and what happens next. Prop type/required are DERIVED at build time.
const docs: UiDoc[] = [
  {
    name: 'ModelWaits',
    keywords: ['wait', 'local', 'model', 'busy', 'queue', 'notice', 'status', 'reply'],
    description:
      'The live notice for one place: the waits of the chat named by `session`, or, with an empty session, the waits of the pages. Reads GET /api/models/waits and re-reads it on every refresh frame naming model_waits and after a reconnect, so the reason appears as a wait starts and goes when it ends.',
    props: [
      { name: 'session', description: 'The chat session id whose waits to show, or "" for the requests pages make, which wait in no chat.' },
      { name: 'floating', description: 'Over the page, on glass like a toast (the shell\'s notice); otherwise a row in the flow, as the chat shows it under its streaming line.' },
      { name: 'className', description: 'Extra classes (tokens only), e.g. width and centring for the floating notice.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Mount it where the person is waiting: the chat for its own reply and tool steps, the shell once for the pages.' },
      { guidance: false, description: 'Do not show a generic spinner for a request a local model has not started: this says what it waits behind and when its next model is asked.' },
    ],
    anatomy: ['ModelWaitNotice over the waits that belong to this place'],
  },
  {
    name: 'ModelWaitNotice',
    keywords: ['wait', 'local', 'model', 'busy', 'countdown', 'move on', 'status'],
    description:
      'Why a request you are waiting for has not started: the local model it needs is busy, with what, and what happens next. Its next model is asked when the countdown ends, or now with "Ask <next> now"; with no other model it runs once the model is free. Renders nothing for no waits, and is a polite role="status" region so the reason is announced once.',
    props: [
      { name: 'waits', description: 'The waits to show, as useModelWaits returns them (each with movesOnAt in this tab\'s clock).' },
      { name: 'onMoveOn', description: 'Called with a wait\'s id when the person asks its next model now.' },
      { name: 'floating', description: 'Over the page, on glass like a toast; otherwise a row in the flow.' },
      { name: 'className', description: 'Extra classes (tokens only).' },
    ],
    bestPractices: [
      { guidance: true, description: 'Use ModelWaits for a live notice; render ModelWaitNotice directly only with waits you already hold.' },
      { guidance: false, description: 'Do not word a wait as the model being slow: it was never sent the request, and the sentence says what it waits behind.' },
    ],
    anatomy: [
      'role="status" column, one row per wait',
      'Hourglass glyph, the reason sentence and the next-step sentence with its countdown',
      'Button "Ask <next> now" while a next model is there and the wait has not moved on',
    ],
  },
]

export default docs
