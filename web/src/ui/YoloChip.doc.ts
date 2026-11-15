import type { UiDoc } from './uiDoc'

// Doc object for YoloChip, the shell corner's YOLO mark. Self-reads the status, renders nothing
// while YOLO is off.
const doc: UiDoc = {
  name: 'YoloChip',
  keywords: ['yolo', 'approvals', 'permissions', 'warn', 'shell', 'corner', 'disclosure'],
  description:
    "The one YOLO mark: while YOLO is on (your chats run tool calls without asking), a warn-toned chip in the shell's right corner beside what waits on you, on every page and in focus mode. From 768 px it reads \"YOLO active\"; in the corner's fold it is a glyph. At every width it is named \"YOLO active: your chats run tool calls without asking\" and is a disclosure (aria-expanded): a press opens that sentence and \"Change it in Agent defaults\", since a tooltip never opens on touch. It reads GET /api/status on mount, again on the gateway's `refresh` ring of kind `yolo`, when YOLO is switched here (`setAgentYolo` rings `yoloSignal`) and when a timed YOLO's time is up. Renders nothing while YOLO is off.",
  props: [
    { name: 'folded', description: "The corner's fold (below 768 px): the glyph alone, its whole name still said." },
    { name: 'className', description: "Extra classes on the chip, e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`) the corner gives every control." },
  ],
  bestPractices: [
    { guidance: true, description: 'Mount it once, in ShellCornerRight: it is the only place YOLO is marked, so a page draws no notice of its own.' },
    { guidance: false, description: 'Do not give it a native title or a hover-only tooltip: the press opens the sentence for touch and pointer alike.' },
  ],
  anatomy: ['warn-toned pill trigger (ShieldAlert glyph, "YOLO active" from 768 px)', 'popover: the sentence, then a TextLink to Agent defaults'],
}

export default doc
