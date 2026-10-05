import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './app/monacoSetup'  // bind Monaco to the local bundle + workers (no CDN) — before any editor mounts
import './design/tokens.css'
import { App } from './app/App'
import { ErrorBoundary } from './app/ErrorBoundary'
import { ThemeProvider } from './app/theme'
import { AppearanceProvider } from './app/appearance'
import { PersonalityProvider } from './app/personality'
import { IdentityProvider } from './app/identity'
import { installAppSdk } from './app/appSdk'
import { registerServiceWorker } from './app/registerServiceWorker'
import { registerBuiltinContentTypes } from './ui/content/registerBuiltins'

// Define window.__personalclaw_modules so contributed app bundles resolve the
// host SDK (and share this React) before any app page mounts.
installAppSdk()

// Populate the content-type registry — the one source of truth the render/edit
// engine resolves every artifact / file / chat-embed through — before any
// ContentSurface mounts.
registerBuiltinContentTypes()

// Install the service worker (production builds only) so the shell boots offline
// and the companion can be installed to a phone's home screen. Fire-and-forget:
// registration never gates the first render, and it swallows its own failures.
void registerServiceWorker()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <AppearanceProvider>
        {/* Inside AppearanceProvider: a personality applies its colors + density
            THROUGH the appearance store rather than owning its own palette. */}
        <PersonalityProvider>
          <IdentityProvider>
            {/* The last line of defence: a throw that no page or widget boundary caught still
                renders the error panel with Retry, never an empty body. */}
            <ErrorBoundary>
              <App />
            </ErrorBoundary>
          </IdentityProvider>
        </PersonalityProvider>
      </AppearanceProvider>
    </ThemeProvider>
  </StrictMode>,
)
