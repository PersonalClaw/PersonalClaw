import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './app/monacoSetup'  // bind Monaco to the local bundle + workers (no CDN) — before any editor mounts
import './design/tokens.css'
import { App } from './app/App'
import { ThemeProvider } from './app/theme'
import { AppearanceProvider } from './app/appearance'
import { IdentityProvider } from './app/identity'
import { installAppSdk } from './app/appSdk'
import { registerBuiltinContentTypes } from './ui/content/registerBuiltins'

// Define window.__personalclaw_modules so contributed app bundles resolve the
// host SDK (and share this React) before any app page mounts (A6).
installAppSdk()

// Populate the content-type registry — the one source of truth the render/edit
// engine resolves every artifact / file / chat-embed through — before any
// ContentSurface mounts.
registerBuiltinContentTypes()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <AppearanceProvider>
        <IdentityProvider>
          <App />
        </IdentityProvider>
      </AppearanceProvider>
    </ThemeProvider>
  </StrictMode>,
)
