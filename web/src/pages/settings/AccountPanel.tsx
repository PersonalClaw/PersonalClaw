import { useEffect, useState } from 'react'
import { Check, RotateCcw } from 'lucide-react'
import { useIdentity, DEFAULT_USER_NAME, suggestHandle } from '../../app/identity'
import { requestSetupRerun } from '../../app/onboarding/rerun'
import { confirm } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { api } from '../../lib/api'
import { PanelHeader, Section, Field, Row, Toggle } from './settingsUI'
import { TextInput } from '../../ui/forms'
import { Button } from '../../ui/Button'

/** Account / identity settings. Self-hosted single-user → the two identities are
 *  the operator's name (SERVER-side DashboardConfig.user_name, follows the user
 *  across machines) and the assistant's name (agent.bot_name — the {{bot_name}}
 *  prompt var), plus a re-trigger for onboarding.
 *  (Content width is a shell control now — the top-right corner pill — not here.) */
/* `suggestHandle` moved to `app/identity` when first-run onboarding began asking for
   the handle too (TSE-1): two surfaces suggesting a slug from the display name need
   ONE rule, not a copy each. The server stays authoritative either way — it
   re-normalizes whatever we send. */

export function AccountPanel() {
  const { name, setName } = useIdentity()
  const [draft, setDraft] = useState(name)
  const [saved, setSaved] = useState(false)

  // `setName` rejects on a refused write now (it used to swallow, which let first-run setup lose a
  // typed name silently — see the note in `app/identity`). So the ✅ waits for the write, and a
  // failure reports through the same toast the handle field beside it already uses.
  const save = async () => {
    try {
      await setName(draft.trim() || DEFAULT_USER_NAME)
    } catch (e) {
      notify(`Couldn't save your name: ${String((e as Error)?.message || e)}`, 'error')
      return
    }
    setSaved(true); setTimeout(() => setSaved(false), 1800)
  }
  const dirty = draft.trim() !== name

  // Attribution handle (dashboard.username) — stamped onto records this user
  // creates. The server normalizes to the canonical slug, so we show what it
  // stored rather than the raw keystrokes (typing "Jo Smith" saves "jo-smith").
  const [handle, setHandle] = useState('')
  const [handleDraft, setHandleDraft] = useState('')
  const [handleSaved, setHandleSaved] = useState(false)
  useEffect(() => {
    api.dashboardConfig().then((c) => {
      const v = String(c?.username ?? '')
      setHandle(v); setHandleDraft(v)
    }).catch(() => {})
  }, [])
  const handleDirty = handleDraft.trim() !== handle
  const saveHandle = () => {
    api.saveDashboardConfig({ username: handleDraft.trim() })
      .then(() => api.dashboardConfig())
      .then((c) => {
        const stored = String(c?.username ?? '')
        setHandle(stored); setHandleDraft(stored)
        setHandleSaved(true); setTimeout(() => setHandleSaved(false), 1800)
      })
      .catch((e) => {
        notify(`Couldn't save your username: ${String((e as Error)?.message || e)}`, 'error')
      })
  }

  // Assistant name (agent.bot_name) — single-field PATCH. The server refuses a character a name
  // cannot carry (the toast names it) and answers with the whole stored config, so the field shows
  // the name from that ANSWER, as Username does. Setting it from the draft is how "Saved" once sat
  // beside `Chloé's Aide` while the server had stored `Chlos Aide`.
  const [botName, setBotName] = useState('')
  const [botDraft, setBotDraft] = useState('')
  const [botSaved, setBotSaved] = useState(false)
  useEffect(() => {
    api.personalclawConfig().then((c) => {
      const v = String(c?.agent?.bot_name ?? '')
      setBotName(v); setBotDraft(v)
    }).catch(() => {})
  }, [])
  const botDirty = botDraft.trim() !== botName
  const saveBot = () => {
    api.patchConfig('agent.bot_name', botDraft.trim()).then((c) => {
      const stored = String(c?.agent?.bot_name ?? '')
      setBotName(stored); setBotDraft(stored)
      setBotSaved(true); setTimeout(() => setBotSaved(false), 1800)
    }).catch((e) => {
      notify(`Couldn't save the assistant name: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Account" hint="PersonalClaw is self-hosted and single-user. Below: how the system addresses you, and — if you reach this box from outside your home network — an optional password sign-in." />

      <Section title="Identity">
        <Field label="Your name" hint="Used in greetings and where the system refers to you. Saved on the server, so it follows you across browsers and machines.">
          <div className="flex items-center gap-s">
            <div className="flex-1" style={{ maxWidth: 280 }}><TextInput value={draft} onChange={setDraft} placeholder="Your name" /></div>
            {/* `aria-disabled` rather than the native attribute: a natively disabled button leaves
                the tab order, so a keyboard user tabbed past this Save without learning there is
                nothing to save. The dimming has to name BOTH selectors — `disabled:opacity-40`
                cannot match an element that is no longer natively disabled. */}
            {/* Three fields on this panel each had a button whose whole accessible name was "Save",
                measured live as 3 identical entries in the control list — so a reader could not tell
                which one commits which field. `Verb: subject`, the form `design/rowActionNames`
                declares; the visible word stays "Save". */}
            <button type="button" onClick={dirty ? save : undefined} aria-disabled={!dirty || undefined}
              aria-label="Save: Your name"
              title={!dirty ? 'No changes to save' : undefined}
              data-type="body-s" className="inline-flex items-center gap-1 rounded-md px-3 h-9 disabled:opacity-40 aria-disabled:opacity-40"
              style={{ background: dirty ? 'var(--color-primary)' : 'var(--color-surface-high)', color: dirty ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}>
              {saved ? <Check size={14} /> : null} {saved ? 'Saved' : 'Save'}
            </button>
          </div>
        </Field>
        <Field label="Username" hint="A short handle stamped onto things you create (tasks, comments) so contributions stay attributable later. Lowercase letters, digits, - and _ — anything else is normalized. It's a label, not a login. Leave it empty to keep records unattributed.">
          <div className="flex items-center gap-s">
            <div className="flex-1" style={{ maxWidth: 280 }}>
              <TextInput value={handleDraft} onChange={setHandleDraft}
                placeholder={suggestHandle(name) || 'your-handle'} />
            </div>
            {/* The shared Button primitive — the two older Save buttons in this
                panel are hand-rolled, but new chrome adopts the kit. */}
            <Button size="sm" variant={handleDirty ? 'primary' : 'secondary'} ariaLabel="Save: Username"
              disabled={!handleDirty} disabledReason={!handleDirty ? 'No changes to save' : undefined} onClick={saveHandle}>
              {handleSaved ? <Check size={14} /> : null} {handleSaved ? 'Saved' : 'Save'}
            </Button>
          </div>
        </Field>
        <Field label="Assistant name" hint="What the assistant calls itself in prompts and greetings ({{bot_name}}). Empty uses the default, PersonalClaw.">
          <div className="flex items-center gap-s">
            <div className="flex-1" style={{ maxWidth: 280 }}><TextInput value={botDraft} onChange={setBotDraft} placeholder="PersonalClaw" /></div>
            <button type="button" onClick={botDirty ? saveBot : undefined} aria-disabled={!botDirty || undefined}
              aria-label="Save: Assistant name"
              title={!botDirty ? 'No changes to save' : undefined}
              data-type="body-s" className="inline-flex items-center gap-1 rounded-md px-3 h-9 disabled:opacity-40 aria-disabled:opacity-40"
              style={{ background: botDirty ? 'var(--color-primary)' : 'var(--color-surface-high)', color: botDirty ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}>
              {botSaved ? <Check size={14} /> : null} {botSaved ? 'Saved' : 'Save'}
            </button>
          </div>
        </Field>
        {/* 🔴 THIS USED TO CLEAR YOUR NAME. "Restart onboarding" worked by wiping `user_name`,
            because `onboarded` is derived from that field being non-empty and clearing it was what
            forced the route guard to show the flow again. So the only way back into setup was to
            destroy your identity — and the flow then re-asked for a name the install already knew.
            A user who skipped setup and wanted to finish it had to pay for it with a rename.
            It is now a request the guard honours (`onboarding/rerun.ts`), so nothing is cleared: the
            flow opens with this install's name and handle already filled in, and every step it
            already recorded stays recorded. No confirmation, because there is nothing to undo. */}
        <Row label="Run setup again" hint="Walks through first-run setup once more. Your name, handle and everything already set up are kept.">
          <button type="button" onClick={requestSetupRerun}
            data-type="body-s" className="inline-flex items-center gap-1.5 rounded-md px-3 h-9 text-on-surface-var hover:bg-surface-high transition-colors">
            <RotateCcw size={14} /> Run setup
          </button>
        </Row>
      </Section>

      <LoginSection />
    </div>
  )
}

/** Owner login (REMOTE-USER-AUTH T3.4).
 *
 *  Deliberately guarded copy, in the security voice: the toggle can only be turned on once a
 *  password exists (the server refuses otherwise, and offering a form nobody can pass is worse
 *  than no form), and the panel says plainly that the local token link keeps working — that is
 *  the escape hatch, and a user who doesn't know it exists will be afraid to enable this. */
function LoginSection() {
  const [state, setState] = useState<{
    login_enabled: boolean
    credential_configured: boolean
    username: string
    totp_enabled: boolean
    totp_required: boolean
    lockout_threshold: number
    lockout_window: string
  } | null>(null)
  const [userDraft, setUserDraft] = useState('')
  const [pwDraft, setPwDraft] = useState('')
  const [pwConfirm, setPwConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [pwSaved, setPwSaved] = useState(false)

  const load = () => {
    api.authSession().then((s) => {
      setState(s)
      setUserDraft(s.username || '')
    }).catch(() => {})
  }
  useEffect(load, [])

  if (!state) return null

  // A username edit with no password typed is the case that used to vanish: the button now says so.
  const userDirty = userDraft.trim() !== (state.username || '')
  const pwLongEnough = pwDraft.length >= 12
  const pwMatches = pwDraft.length > 0 && pwDraft === pwConfirm
  const canSavePw = pwLongEnough && pwMatches && !busy

  const savePassword = () => {
    setBusy(true)
    api.setLoginPassword(userDraft.trim(), pwDraft)
      .then(() => {
        setPwDraft(''); setPwConfirm('')
        setPwSaved(true); setTimeout(() => setPwSaved(false), 2400)
        load()
      })
      .catch((e) => notify(`Couldn't set the password: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }

  const toggleLogin = async (next: boolean) => {
    // Turning this OFF retires the password sign-in path, and a user who does not have the token
    // link handy could lock themselves out of this dashboard — so OFF asks, as a LOCKOUT warning.
    // ON is the security-relevant direction (a new way in that anyone who can reach the page may
    // try), and the gateway asks its own consent for it through `api.patchConfig`.
    if (!next && !(await confirm({
      title: 'Turn off password sign-in?',
      body: 'Only your token link will work afterward — make sure you have it saved before turning this off, or you could lock yourself out of this dashboard.',
      confirmLabel: 'Turn off',
      danger: true,
    }))) return
    api.patchConfig('auth.login_enabled', next)
      .then(() => load())
      .catch((e) => notify(`Couldn't change sign-in: ${String((e as Error)?.message || e)}`, 'error'))
  }

  const toggleTotp = async (next: boolean) => {
    // Turning this OFF is the relaxing direction: a username and password alone become
    // sufficient to sign in — the authenticator code is no longer required.
    if (!next && !(await confirm({
      title: 'Turn off the 2FA requirement?',
      body: 'Signing in with just a username and password will be enough afterward — the authenticator code will no longer be required.',
      confirmLabel: 'Turn off',
      danger: true,
    }))) return
    // OFF was just consented to above; the flag tells the gateway so, and it does not ask again.
    api.patchConfig('auth.require_totp', next, !next)
      .then(() => load())
      .catch((e) => notify(`Couldn't change the 2FA requirement: ${String((e as Error)?.message || e)}`, 'error'))
  }

  return (
    <Section title="Sign in from outside your network"
      hint="Off by default. Turn this on only if you reach this dashboard over a tunnel or from the internet — on your home network the token link is simpler and safer.">

      {/* 🔴 THE SIGN-IN USERNAME USED TO SIT IN ITS OWN FIELD WITH NO SAVE CONTROL AT ALL, and the
          only writer of it — `POST /api/auth/password` — requires a password in the same call
          (`creds.set_password(username, password)`). Driven: typed a new username, left the panel,
          came back — the field read "" again, and its Field contained zero buttons. An editable box
          on a SECURITY surface that silently discards what you type.
          It now lives in the form whose button commits it, which is what the server's contract
          actually is, and the button says why a username alone cannot be saved. Three controls in one
          Field, so each carries its own name — the case `ui/forms` carves an explicit `ariaLabel` out
          for. */}
      <Field label={state.credential_configured ? 'Change the sign-in username or password' : 'Set a sign-in username and password'}
        hint="Both are saved together, in one step — so changing the username means entering the password again. At least 12 characters: length matters more than symbols. Stored as an argon2id hash; it is never shown again, and never leaves this box.">
        <div className="flex flex-col gap-s" style={{ maxWidth: 280 }}>
          <TextInput value={userDraft} onChange={setUserDraft} placeholder="you" ariaLabel="Sign-in username" />
          <TextInput type="password" value={pwDraft} onChange={setPwDraft} placeholder="New password" ariaLabel="New password" />
          <TextInput type="password" value={pwConfirm} onChange={setPwConfirm} placeholder="Confirm password" ariaLabel="Confirm password" />
          <div className="flex items-center gap-s">
            {/* Names whichever requirement is outstanding. The sibling hint below only appears
                once something has been typed, so the button was the sole affordance for an
                untouched form and it said nothing. */}
            <Button size="sm" variant={canSavePw ? 'primary' : 'secondary'} disabled={!canSavePw} onClick={savePassword}
              disabledReason={busy ? undefined
                : !pwLongEnough
                  ? (userDirty ? 'Enter the password too — the username is saved with it' : 'Use at least 12 characters')
                  : 'Both fields must match'}>
              {pwSaved ? <Check size={14} /> : null} {pwSaved ? 'Saved' : 'Save sign-in'}
            </Button>
            {pwDraft.length > 0 && !pwLongEnough ? (
              <span data-type="caption" style={{ color: 'var(--color-on-surface-low)' }}>
                {12 - pwDraft.length} more characters
              </span>
            ) : null}
            {pwDraft.length > 0 && pwLongEnough && !pwMatches ? (
              <span data-type="caption" style={{ color: 'var(--color-on-surface-low)' }}>
                Passwords don't match
              </span>
            ) : null}
          </div>
        </div>
      </Field>

      <Row label="Offer password sign-in"
        hint={state.credential_configured
          ? 'Adds a sign-in page as another way in. Your token link keeps working — it stays the way back in if you ever forget the password.'
          : 'Set a password first. Turning this on without one would show a form nobody can pass.'}>
        {/* The precondition lives in the row hint above, which a keyboard user never reaches while the
            switch is natively disabled — they tab past a SECURITY control without learning it exists.
            With a reason it keeps its tab stop and announces what would unlock it. */}
        <Toggle on={state.login_enabled} onChange={toggleLogin} disabled={!state.credential_configured}
          disabledReason="Set a password first — a sign-in form nobody can pass is worse than none"
          label="Offer password sign-in" />
      </Row>

      <Row label="Require a 2FA code"
        hint={state.totp_enabled
          ? 'Also ask for a time-based code at sign-in.'
          : 'Enroll an authenticator first with `personalclaw auth totp setup`, then turn this on — verify a code works before requiring it.'}>
        <Toggle on={state.totp_required} onChange={toggleTotp} disabled={!state.totp_enabled}
          disabledReason="Enroll an authenticator first with `personalclaw auth totp setup`"
          label="Require a 2FA code" />
      </Row>

      {state.login_enabled ? (
        <p data-type="caption" className="leading-relaxed" style={{ color: 'var(--color-on-surface-low)' }}>
          After {state.lockout_threshold} failed attempts, sign-in is refused for {state.lockout_window}.
          Every attempt is recorded in the audit log.
        </p>
      ) : null}
    </Section>
  )
}
