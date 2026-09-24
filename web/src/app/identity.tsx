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
  clearName: () => Promise<void>  // re-triggers onboarding
}

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

const IdentityCtx = createContext<Identity>({ name: '', username: '', onboarded: false, loaded: false, setName: async () => {}, clearName: async () => {} })

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
  const setName = async (n: string, handle?: string) => {
    const trimmed = n.trim()
    setNameState(trimmed)  // optimistic
    const body: Partial<DashboardConfig> = { user_name: trimmed }
    if (handle !== undefined) {
      const slug = handle.trim()
      body.username = slug
      setUsernameState(slug)  // optimistic; the server may normalize it further
    }
    await api.saveDashboardConfig(body).catch(() => {})
  }
  const clearName = async () => {
    setNameState('')
    // The handle is deliberately NOT cleared: restarting onboarding re-asks for the
    // name, and a handle already stamped onto existing records should survive to be
    // offered back rather than silently dropped.
    await api.saveDashboardConfig({ user_name: '' }).catch(() => {})
  }

  return (
    <IdentityCtx.Provider value={{ name, username, onboarded: name.trim().length > 0, loaded, setName, clearName }}>
      {children}
    </IdentityCtx.Provider>
  )
}

export const useIdentity = () => useContext(IdentityCtx)

/** First name for greetings; falls back to a neutral label. */
export function firstNameOf(name: string): string {
  return name.trim().split(/\s+/)[0] || 'there'
}
