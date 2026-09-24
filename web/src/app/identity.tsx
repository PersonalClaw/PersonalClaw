import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, type DashboardConfig } from '../lib/api'

/** The operator's identity — INSTANCE-level, not device-level. PersonalClaw is
 *  self-hosted + single-user, so the operator's name is a fact about the
 *  instance and lives on the SERVER (DashboardConfig.user_name via
 *  /api/dashboard/config). That way it follows the user across browsers/machines
 *  and they're never re-onboarded on a new device. Per-device prefs (theme,
 *  width, nav state) stay in localStorage; identity does not.
 *
 *  `onboarded` is DERIVED — a non-empty server name means onboarding is done.
 *  `loaded` gates the first render so we don't flash onboarding before the
 *  server answers. */
interface Identity {
  name: string
  /** The attribution handle already stored (`dashboard.username`), '' when unset.
   *  Read-only here: the two surfaces that WRITE it are first-run onboarding (via
   *  `setName`'s second argument) and Settings → Account, which re-reads the stored
   *  slug after saving because the server normalizes what it sends. */
  username: string
  onboarded: boolean
  loaded: boolean
  setName: (name: string, username?: string) => Promise<void>
}
/* There is deliberately no `clearName`. Re-entering first-run setup used to work by wiping
   `user_name` — `onboarded` is derived from it, so clearing it was what forced the route guard's
   hand — which made the only door back into setup a destructive one. It is now a request the guard
   honours (`app/onboarding/rerun.ts`), so nothing about identity is cleared to reach a setup screen,
   and there is no other caller that wants an operator with no name. */

/** The name identity falls back to when the user declines to give one.
 *
 *  Two places need the SAME word: onboarding's "Skip setup" path (which commits identity
 *  without ever having asked, because a non-empty name is what releases the route guard) and
 *  the Settings → Account field (which refuses to save an empty name). A second literal would
 *  mean skipping setup named you one thing and clearing the field named you another. */
export const DEFAULT_USER_NAME = 'Operator'

/** The attribution handle's length cap, mirrored from `identity.USERNAME_MAX_LEN`.
 *
 *  The suggestion is a client concern, but the LENGTH is the server's rule, so this
 *  number is a copy — and `tests/test_identity.py` rails the two together. Without
 *  that rail the cap lived in exactly one place at suggestion time (here) with nothing
 *  to notice if the server's moved: the suggestion would then propose a handle the
 *  server silently truncates. */
export const USERNAME_MAX_LEN = 32

/** Mirror of the server's slug rule, for the suggested handle only — the server is
 *  authoritative and re-normalizes whatever we send (`slugify_username`, applied at
 *  the `PUT /api/dashboard/config` boundary).
 *
 *  ONE implementation, deliberately. Two surfaces suggest a handle from the display
 *  name — first-run onboarding and Settings → Account's placeholder — and a second
 *  copy would be a second slug convention. The two would agree on `Jo Smith` and
 *  disagree on exactly the inputs nobody types by hand: stacked combining marks, a
 *  name that crosses the cap, a name that ends on a separator once truncated. */
export function suggestHandle(displayName: string): string {
  return displayName
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '')
    .slice(0, USERNAME_MAX_LEN)
    .replace(/[-_]+$/, '')
}

const IdentityCtx = createContext<Identity>({ name: '', username: '', onboarded: false, loaded: false, setName: async () => {} })

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [name, setNameState] = useState('')
  const [username, setUsernameState] = useState('')
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let alive = true
    api.dashboardConfig()
      .then((c) => {
        if (!alive) return
        setNameState(c.user_name || '')
        setUsernameState(c.username || '')
      })
      .catch(() => { /* leave name empty → onboarding */ })
      .finally(() => { if (alive) setLoaded(true) })
    return () => { alive = false }
  }, [])

  /** Commit the operator's name, and the attribution handle ONLY when the caller
   *  passes one.
   *
   *  The optionality is the contract, not a convenience. The server's PUT is
   *  key-presence-gated (`if "username" in body`), so omitting the key leaves the
   *  stored handle untouched — which is what keeps renaming yourself in Settings →
   *  Account from resurrecting a handle the operator deliberately cleared. Only a
   *  surface that actually ASKED for a handle may send one, and today that is first
   *  run. Deriving one from every `user_name` write would have no first-run
   *  discriminator and would silently re-stamp a cleared handle. */
  /* 🔴 THE WRITE COMES FIRST, AND A FAILURE REJECTS. This used to set the name optimistically and
     then `await api.saveDashboardConfig(body).catch(() => {})`, and that combination produced the
     worst dead end on the first screen of the product.

     Measured on a fresh home with the gateway killed: typing a name and clicking the flow's one
     advertised exit — "Skip setup" — returned the user to **step 1 with the field empty and no
     message of any kind** (`alerts: []`). The chain is entirely in these two functions. The
     optimistic `setNameState` flips `onboarded` (derived below from the name being non-empty), so
     the route guard navigates out of the flow; the shell remounts; the config fetch above fails;
     its own `.catch` leaves the name empty; `onboarded` goes back to false; the guard redirects
     back to `#/onboarding` — a fresh component, with nothing the user typed. The failed write was
     discarded, so nothing anywhere could say why.

     It is not a dead-backend edge case either: ANY failure of this PUT does it — a read-only config
     file, a full disk, a rejected value, a gateway mid-restart. The gateway is a local process the
     desktop app wraps, and it can die.

     So `onboarded` never flips on a write that did not land, and the caller learns. The repo's own
     swallowed-write census (`pages/terminal/closeReportsFailure.test.tsx`) listed both of this
     file's `.catch(() => {})` sites as the one open "onboarding-swallow question"; this is the
     answer, and both entries are gone from it. */
  const setName = async (n: string, handle?: string) => {
    const trimmed = n.trim()
    const body: Partial<DashboardConfig> = { user_name: trimmed }
    const slug = handle === undefined ? undefined : handle.trim()
    if (slug !== undefined) body.username = slug
    await api.saveDashboardConfig(body)
    setNameState(trimmed)
    if (slug !== undefined) setUsernameState(slug)  // the server may normalize it further
  }
  return (
    <IdentityCtx.Provider value={{ name, username, onboarded: name.trim().length > 0, loaded, setName }}>
      {children}
    </IdentityCtx.Provider>
  )
}

export const useIdentity = () => useContext(IdentityCtx)

/** First name for greetings; falls back to a neutral label. */
export function firstNameOf(name: string): string {
  return name.trim().split(/\s+/)[0] || 'there'
}
