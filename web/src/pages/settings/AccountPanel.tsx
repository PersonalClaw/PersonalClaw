import { useEffect, useState } from 'react'
import { Check, RotateCcw } from 'lucide-react'
import { useIdentity } from '../../app/identity'
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
/** Mirror of the server's slug rule, for the placeholder suggestion only — the
 *  server is authoritative and re-normalizes whatever we send. */
function suggestHandle(displayName: string): string {
  return displayName
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '')
    .slice(0, 32)
    .replace(/[-_]+$/, '')
}

export function AccountPanel() {
  const { name, setName, clearName } = useIdentity()
  const [draft, setDraft] = useState(name)
  const [saved, setSaved] = useState(false)

  const save = () => { setName(draft.trim() || 'Operator'); setSaved(true); setTimeout(() => setSaved(false), 1800) }
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

  // Assistant name (agent.bot_name) — single-field PATCH; server sanitizes.
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
    const v = botDraft.trim()
    api.patchConfig('agent.bot_name', v).then(() => {
      setBotName(v); setBotSaved(true); setTimeout(() => setBotSaved(false), 1800)
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
            <button type="button" onClick={save} disabled={!dirty}
              className="inline-flex items-center gap-1 rounded-md px-3 h-9 text-[0.8125rem] disabled:opacity-40"
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
            <Button size="sm" variant={handleDirty ? 'primary' : 'secondary'}
              disabled={!handleDirty} onClick={saveHandle}>
              {handleSaved ? <Check size={14} /> : null} {handleSaved ? 'Saved' : 'Save'}
            </Button>
          </div>
        </Field>
        <Field label="Assistant name" hint="What the assistant calls itself in prompts and greetings ({{bot_name}}). Empty uses the default, PersonalClaw.">
          <div className="flex items-center gap-s">
            <div className="flex-1" style={{ maxWidth: 280 }}><TextInput value={botDraft} onChange={setBotDraft} placeholder="PersonalClaw" /></div>
            <button type="button" onClick={saveBot} disabled={!botDirty}
              className="inline-flex items-center gap-1 rounded-md px-3 h-9 text-[0.8125rem] disabled:opacity-40"
              style={{ background: botDirty ? 'var(--color-primary)' : 'var(--color-surface-high)', color: botDirty ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}>
              {botSaved ? <Check size={14} /> : null} {botSaved ? 'Saved' : 'Save'}
            </button>
          </div>
        </Field>
        <Row label="Restart onboarding" hint="Clears your name and re-runs the first-run setup flow.">
          <button type="button" onClick={async () => { if (await confirm({ title: 'Restart onboarding?', body: 'This clears your name and shows the setup flow again.', confirmLabel: 'Restart' })) clearName() }}
            className="inline-flex items-center gap-1.5 rounded-md px-3 h-9 text-[0.8125rem] text-on-surface-var hover:bg-surface-high transition-colors">
            <RotateCcw size={14} /> Restart
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

  const toggleLogin = (next: boolean) => {
    api.patchConfig('auth.login_enabled', next)
      .then(() => load())
      .catch((e) => notify(`Couldn't change sign-in: ${String((e as Error)?.message || e)}`, 'error'))
  }

  const toggleTotp = (next: boolean) => {
    api.patchConfig('auth.require_totp', next)
      .then(() => load())
      .catch((e) => notify(`Couldn't change the 2FA requirement: ${String((e as Error)?.message || e)}`, 'error'))
  }

  return (
    <Section title="Sign in from outside your network"
      hint="Off by default. Turn this on only if you reach this dashboard over a tunnel or from the internet — on your home network the token link is simpler and safer.">

      <Field label="Sign-in username"
        hint="The name you'll type at the sign-in form. There is still exactly one account — this is a subject for the login, not a user list.">
        <div className="flex items-center gap-s">
          <div className="flex-1" style={{ maxWidth: 280 }}>
            <TextInput value={userDraft} onChange={setUserDraft} placeholder="you" />
          </div>
        </div>
      </Field>

      <Field label={state.credential_configured ? 'Change password' : 'Set a password'}
        hint="At least 12 characters — length matters more than symbols. Stored as an argon2id hash; it is never shown again, and never leaves this box.">
        <div className="flex flex-col gap-s" style={{ maxWidth: 280 }}>
          {/* TWO controls in ONE Field, so each needs its own name: claiming the Field's label would
              make both announce "Set a password" and leave them indistinguishable — exactly the
              multi-control case ui/forms' comment carves out for an explicit ariaLabel. */}
          <TextInput type="password" value={pwDraft} onChange={setPwDraft} placeholder="New password" ariaLabel="New password" />
          <TextInput type="password" value={pwConfirm} onChange={setPwConfirm} placeholder="Confirm password" ariaLabel="Confirm password" />
          <div className="flex items-center gap-s">
            <Button size="sm" variant={canSavePw ? 'primary' : 'secondary'} disabled={!canSavePw} onClick={savePassword}>
              {pwSaved ? <Check size={14} /> : null} {pwSaved ? 'Saved' : 'Save password'}
            </Button>
            {pwDraft.length > 0 && !pwLongEnough ? (
              <span className="text-[0.75rem]" style={{ color: 'var(--color-on-surface-low)' }}>
                {12 - pwDraft.length} more characters
              </span>
            ) : null}
            {pwDraft.length > 0 && pwLongEnough && !pwMatches ? (
              <span className="text-[0.75rem]" style={{ color: 'var(--color-on-surface-low)' }}>
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
        <Toggle on={state.login_enabled} onChange={toggleLogin} disabled={!state.credential_configured}
          label="Offer password sign-in" />
      </Row>

      <Row label="Require a 2FA code"
        hint={state.totp_enabled
          ? 'Also ask for a time-based code at sign-in.'
          : 'Enroll an authenticator first with `personalclaw auth totp setup`, then turn this on — verify a code works before requiring it.'}>
        <Toggle on={state.totp_required} onChange={toggleTotp} disabled={!state.totp_enabled}
          label="Require a 2FA code" />
      </Row>

      {state.login_enabled ? (
        <p className="text-[0.75rem] leading-relaxed" style={{ color: 'var(--color-on-surface-low)' }}>
          After {state.lockout_threshold} failed attempts, sign-in is refused for {state.lockout_window}.
          Every attempt is recorded in the audit log.
        </p>
      ) : null}
    </Section>
  )
}
