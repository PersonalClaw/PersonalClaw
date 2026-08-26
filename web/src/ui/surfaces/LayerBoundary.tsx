/** The per-layer error boundary (AMBIENT-SURFACES §6).
 *
 *  Every L1 (app) and L2 (user/agent) surface load renders inside one of these: a
 *  broken layer FALLS THROUGH with a named notice, and never blanks the surface it
 *  was composed into. This is the `safe()` pattern the tool-renderer registry already
 *  uses, expressed as a component because a layered surface is a React subtree rather
 *  than a function call.
 *
 *  Why a boundary rather than a try/catch: a component that throws during RENDER (the
 *  common case for a contributed component fed model-authored args) unwinds the whole
 *  React tree above it. Without a boundary at the layer seam, one app component with a
 *  bad prop takes out the entire page — which is exactly the "agent-rewritten surface
 *  bricks the app" failure §6 exists to make structurally impossible. */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import { layerName, type SurfaceLayer } from './layers'

interface Props {
  layer: SurfaceLayer
  /** What broke, in the user's words — an app name, a component name. */
  what: string
  children: ReactNode
  /** Optional replacement for the default inline notice. */
  fallback?: ReactNode
}

interface State {
  failed: boolean
}

export class LayerBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Logged, not swallowed: a layer that fails silently is a layer nobody fixes.
    // eslint-disable-next-line no-console
    console.error(
      `[surfaces] ${layerName(this.props.layer)}-layer surface "${this.props.what}" failed to render`,
      error,
      info.componentStack,
    )
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children
    if (this.props.fallback !== undefined) return this.props.fallback
    return (
      <div
        role="alert"
        className="flex items-start gap-2 rounded-lg px-3 py-2 text-[0.75rem] text-warn"
        style={{ background: 'color-mix(in srgb, var(--color-warn) 8%, transparent)' }}
      >
        <AlertTriangle size={13} className="mt-0.5 shrink-0" />
        <span className="min-w-0 break-words">
          {this.props.what} ({layerName(this.props.layer)} layer) could not render — the rest of this
          surface is unaffected.
        </span>
      </div>
    )
  }
}
