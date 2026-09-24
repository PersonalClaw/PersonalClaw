import type { UiDoc } from './uiDoc'

// Doc object for Toaster — the global singleton toast host. It listens for the
// `ne:toast` CustomEvent and takes no props.
const doc: UiDoc = {
  name: 'Toaster',
  keywords: ['toast', 'toaster', 'notify', 'transient', 'message', 'snackbar', 'host', 'ne:toast'],
  description:
    'The global toast host. Renders transient messages dispatched via the `ne:toast` CustomEvent — what contributed apps reach through the SDK\'s useNotify, and any host code can use too. Auto-dismisses after 5s; stacks bottom-right with a Sonner-style fan-out (newest in front, capped at 4 visible) and velocity swipe-to-dismiss. Mount once in the app shell; takes no props.',
  props: [],
  bestPractices: [
    { guidance: true, description: 'Mount Toaster exactly once in the app shell (next to UpdateProgressOverlay / DialogHost) — it is a singleton host, not a per-page component.' },
    { guidance: true, description: 'Fire toasts by dispatching the `ne:toast` CustomEvent ({ level, message }) — via the SDK useNotify — rather than rendering toast cards yourself.' },
    { guidance: false, description: "Do not pass a level other than 'info' | 'success' | 'error' — anything else silently falls back to 'info'." },
    { guidance: false, description: 'Do not disable its animation ad hoc — reduced-motion is already honored via the root MotionConfig.' },
  ],
  anatomy: ['fixed bottom-right stack (pointer-events-none container)', 'AnimatePresence (mode="popLayout")', 'per-toast glass card (drag-x swipe-to-dismiss)', 'level icon + message + dismiss button'],
}

export default doc
