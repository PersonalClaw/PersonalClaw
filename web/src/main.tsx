import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './app/monacoSetup'  // bind Monaco to the local bundle + workers (no CDN) — before any editor mounts
import './design/tokens.css'
import { App } from './app/App'
import { ThemeProvider } from './app/theme'
import { AppearanceProvider } from './app/appearance'
import { PersonalityProvider } from './app/personality'
import { IdentityProvider } from './app/identity'
import { installAppSdk } from './app/appSdk'
import { registerServiceWorker } from './app/registerServiceWorker'
import { registerBuiltinContentTypes } from './ui/content/registerBuiltins'

// Define window.__personalclaw_modules so contributed app bundles resolve the
// host SDK (and share this React) before any app page mounts (A6).
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
            <App />
          </IdentityProvider>
        </PersonalityProvider>
      </AppearanceProvider>
    </ThemeProvider>
  </StrictMode>,
)
