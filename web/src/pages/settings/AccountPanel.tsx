import { useEffect, useState } from 'react'
import { Check, RotateCcw } from 'lucide-react'
import { useIdentity } from '../../app/identity'
import { confirm } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { api } from '../../lib/api'
import { PanelHeader, Section, Field, Row } from './settingsUI'
import { TextInput } from '../tasks/formControls'

/** Account / identity settings. Self-hosted single-user → the two identities are
 *  the operator's name (SERVER-side DashboardConfig.user_name, follows the user
 *  across machines) and the assistant's name (agent.bot_name — the {{bot_name}}
 *  prompt var), plus a re-trigger for onboarding.
 *  (Content width is a shell control now — the top-right corner pill — not here.) */
export function AccountPanel() {
  const { name, setName, clearName } = useIdentity()
  const [draft, setDraft] = useState(name)
  const [saved, setSaved] = useState(false)

  const save = () => { setName(draft.trim() || 'Operator'); setSaved(true); setTimeout(() => setSaved(false), 1800) }
  const dirty = draft.trim() !== name

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
      <PanelHeader title="Account" hint="PersonalClaw is self-hosted and single-user — there's no sign-in or profile, just how the system addresses you." />

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
    </div>
  )
}
