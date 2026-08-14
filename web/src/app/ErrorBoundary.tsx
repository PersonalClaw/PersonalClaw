import { Component, type ReactNode } from 'react'
import { fvs } from '../design/fontWeight'
import { AlertTriangle, RotateCcw } from 'lucide-react'
import { treatmentPaint } from '../design/errorTreatments'
import { useErrorTreatment } from './personality'

interface Props { children: ReactNode; resetKey?: string }
interface State { error: Error | null }

const RELOAD_GUARD_KEY = 'pc:chunk-reload-at'
const RELOAD_GUARD_WINDOW_MS = 10_000

/** A failed lazy chunk load means the served bundle was rotated (a redeploy
 *  changed the hashed filenames) after this tab loaded index.html. Re-rendering
 *  re-requests the same missing chunk and fails again — only a full reload
 *  fetches a fresh index.html with the current hashes. */
function isChunkLoadError(err: Error): boolean {
  const msg = `${err?.name ?? ''} ${err?.message ?? ''}`
  return /failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed|ChunkLoadError/i.test(msg)
}

/** The fallback panel, as a FUNCTION component.
 *
 *  It is split out for one reason: an error boundary must stay a class (only a
 *  class can implement `componentDidCatch`/`getDerivedStateFromError`), so hooks
 *  are unavailable inside it — but the personality's error treatment lives in
 *  React context. Rendering the fallback as a child component moves the hook read
 *  to where hooks are legal, without threading a variant prop through all three
 *  `<ErrorBoundary>` call sites and without exposing the personality context to a
 *  legacy `contextType` slot.
 *
 *  The treatment is a SKIN. Copy, the `onRetry` action and the button label are
 *  computed here, identically for every personality; a treatment supplies only
 *  classes and colour tokens. With no treatment (every standard scheme, including
 *  the default identity) this renders byte-identical markup to the pre-personality
 *  version — frozen in `errorTreatmentSkin.test.tsx`. */
function ErrorFallback({ chunk, message, onRetry }: { chunk: boolean; message: string; onRetry: () => void }) {
  const treatment = useErrorTreatment()
  const paint = treatmentPaint(treatment)
  return (
    <div
      className={['flex h-full flex-col items-center justify-center gap-m px-l text-center', treatment?.surfaceClass]
        .filter(Boolean)
        .join(' ')}
      style={paint ?? undefined}>
      <AlertTriangle size={32} className={treatment?.iconClass ?? 'text-on-surface-low'} />
      <div className="text-on-surface text-[1.0625rem]" style={fvs(500)}>
        {chunk ? 'A new version is available' : 'This page hit an error'}
      </div>
      <p className="max-w-md text-on-surface-low text-[0.8125rem]">
        {chunk
          ? 'The app was updated while this tab was open. Reload to load the latest version.'
          : (message || 'Something went wrong rendering this view.')}
      </p>
      <button type="button"
        onClick={onRetry}
        className="inline-flex items-center gap-1.5 rounded-md px-3 h-9 text-[0.8125rem]" style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
        <RotateCcw size={14} /> {chunk ? 'Reload app' : 'Retry'}
      </button>
    </div>
  )
}

/** Per-page error boundary so one thrown render error doesn't blank the whole
 *  app. Resets when `resetKey` changes (e.g. navigating to another page). */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }
  static getDerivedStateFromError(error: Error): State { return { error } }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })
  }

  componentDidCatch(error: Error) {
    // Stale-bundle chunk failure: recover by reloading. Guard against a reload
    // loop — only auto-reload if we haven't already done so in the last few
    // seconds, so a genuinely broken chunk surfaces the message instead.
    if (!isChunkLoadError(error)) return
    let last = 0
    try { last = Number(sessionStorage.getItem(RELOAD_GUARD_KEY)) || 0 } catch { /* ignore */ }
    if (Date.now() - last < RELOAD_GUARD_WINDOW_MS) return
    try { sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now())) } catch { /* ignore */ }
    window.location.reload()
  }

  render() {
    if (this.state.error) {
      const chunk = isChunkLoadError(this.state.error)
      return (
        <ErrorFallback
          chunk={chunk}
          message={this.state.error.message}
          onRetry={() => { if (chunk) window.location.reload(); else this.setState({ error: null }) }}
        />
      )
    }
    return this.props.children
  }
}
