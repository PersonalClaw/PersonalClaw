import type { UiDoc } from './uiDoc'

// Doc object for SnipOverlay — the crop step of the composer's screen capture.
const doc: UiDoc = {
  name: 'SnipOverlay',
  keywords: ['snip', 'crop', 'screenshot', 'screen capture', 'region', 'overlay', 'dialog', 'attach'],
  description:
    "Modal crop step for a captured screen frame (CHAT-CRAFT S4a). The frame arrives already frozen — the capture is stopped before this mounts — so it is a still image, not a live preview. The selection starts as the whole frame so Enter is immediately a complete action; arrows move it, Alt+arrows resize it, dragging authors a rectangle, Escape cancels and attaches nothing. Returns the crop in SOURCE pixels; the caller encodes the PNG and hands it to the ordinary upload pipeline.",
  props: [
    { name: 'frame', description: 'The captured frame as a data URL. Already a still — this component never holds a live capture.' },
    { name: 'width', description: "Natural width of the frame in source pixels; the crop rect this returns is in those coordinates." },
    { name: 'height', description: 'Natural height of the frame in source pixels.' },
    { name: 'onCancel', description: 'Dismissed without attaching anything — Escape, Cancel, or a scrim click. Must leave no attachment behind.' },
    { name: 'onConfirm', description: 'Confirmed: receives the crop rectangle in SOURCE pixels (x/y/width/height).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Stop the capture BEFORE mounting this (see grabOneFrame): the overlay is a crop step, not a capture surface, and a live track behind it would keep the browser capture indicator lit while the user takes their time.' },
    { guidance: true, description: 'Keep the whole frame as the default selection. A keyboard user must never have to author a rectangle from zero size before Enter does anything.' },
    { guidance: false, description: 'Do not add a second dimming mask: the outside dim is the base image at low opacity and the selection is the SAME image clipped, so what looks selected and what gets cropped cannot drift apart.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — geometry goes through inline percentages (the frame is laid out to fit the viewport, not at native size) and everything else through design tokens.' },
  ],
  anatomy: ['portaled scrim (click-to-cancel)', 'dialog sheet (aria-modal + focus trap)', 'header with crop icon + title', 'focusable capture stage (dimmed base image + clipped selection with a primary ring)', 'selection-size hint line', 'footer: Cancel + Attach selection'],
}

export default doc
