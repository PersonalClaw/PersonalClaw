import { useState } from 'react'
import { Mic2, Plus, Trash2, Lock, Unlock, ShieldCheck, Wand2 } from 'lucide-react'
import {
  api,
  type VoiceProfile,
  type VoiceProfileKind,
  type VoiceBindings,
  type VoiceResolution,
} from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { Section, Field } from './settingsUI'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { Table, THead, Th, Td } from '../../ui/Table'
import { StatusPill, type StatusPillTone } from '../../ui/StatusPill'
import { TextInput, Select } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { SquareIconButton } from '../../ui/SquareIconButton'

const QUERY_KEY = 'settings:voice-profiles'

/** The `default` binding key, which is legal alongside `<namespace>:<name>`. */
const DEFAULT_KEY = 'default'

/** `bindings.py` SURFACE_NAMESPACES. A surface is `<namespace>:<name>`; `default`
 *  is the one bare key, so it is offered as a namespace here and drops the name. */
const NAMESPACES = ['channel', 'agent', 'client'] as const

/** How each §3 level reads. `built-in` is a real, correct answer — the shipped
 *  voice speaks — so it gets neutral ink, not a warning. */
const LEVEL_COPY: Record<string, { label: string; tone: StatusPillTone }> = {
  explicit: { label: 'explicit', tone: 'primary' },
  binding: { label: 'this binding', tone: 'ok' },
  default: { label: 'default voice', tone: 'info' },
  'built-in': { label: 'built-in voice', tone: 'neutral' },
}

function surfaceKey(namespace: string, name: string): string {
  return namespace === DEFAULT_KEY ? DEFAULT_KEY : `${namespace}:${name.trim()}`
}

/** `bindings.binding_warning` returns a REASON CODE, not prose — measured live, the
 *  bind response carries `"unverified_clone_consent"`. Rendering the code verbatim is
 *  what a user would have read, so the copy lives here, where copy belongs. An
 *  unrecognised code falls through to the code itself rather than to silence: an
 *  unmapped warning is still a warning, and dropping it would hide the one thing
 *  §1.3 wants said out loud. */
const BIND_WARNING_COPY: Record<string, string> = {
  unverified_clone_consent:
    'This is a cloned voice with no consent on record. It will still speak — recording consent on the profile is what makes that provenance explicit.',
}

function bindWarningCopy(reason: string): string {
  return BIND_WARNING_COPY[reason] ?? reason
}

/** Voice profiles + per-surface bindings + the §6 one-click migration (MULTIMODAL-IO
 *  §1/§3/§6, atom MI-5).
 *
 *  Two tables, because they answer two different questions: the profile manager is
 *  "which voices exist", and the bindings table is "which one actually speaks, where".
 *  The second reads its verdict from `GET /api/voice/resolve` rather than walking the
 *  precedence chain here — a client-side copy of the chain is a second implementation
 *  that can disagree with the resolver, and the disagreement would be invisible.
 *
 *  Consent is provenance, not permission (§1.3): a profile without it synthesizes
 *  locally exactly as one with it does, and the only consequence is a warning when
 *  it is bound to an agentic surface. So "no consent recorded" is rendered as plain
 *  absence, never as an error state — the same two-weight discipline the rest of the
 *  product applies to "skipped for the right reason" vs "broken". */
export function VoiceProfilesSection() {
  const [creating, setCreating] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [draftKind, setDraftKind] = useState<VoiceProfileKind>('design')
  const [draftProvider, setDraftProvider] = useState('')
  const [draftModel, setDraftModel] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  /** The advisory `warning` a bind can return (§1.3). Kept separate from `err`:
   *  the bind SUCCEEDED, so showing it as a failure would misreport the outcome. */
  const [bindWarning, setBindWarning] = useState('')

  const { data, error: loadErr, refresh } = useQuery(
    QUERY_KEY, () => api.voiceProfiles(), { persist: true },
  )

  if (!data && loadErr) return <LoadError what="voice profiles" error={loadErr} onRetry={refresh} />
  if (!data) return <ListSkeleton rows={3} what="voice profiles" />

  const { profiles, bindings } = data

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label); setErr(''); setBindWarning('')
    try {
      await fn()
      invalidateKeys(QUERY_KEY)
      invalidateKeys('settings:voice-effective', true)
      refresh()
    } catch (e) {
      setErr(String((e as Error)?.message || e))
    } finally {
      setBusy('')
    }
  }

  async function create() {
    await run('create', async () => {
      await api.voiceProfileCreate({
        name: draftName.trim(),
        kind: draftKind,
        provider: draftProvider.trim(),
        model: draftModel.trim(),
      })
      setDraftName(''); setDraftProvider(''); setDraftModel(''); setCreating(false)
    })
  }

  async function bind(namespace: string, name: string, profileId: string) {
    await run('bind', async () => {
      const res = await api.voiceBindingSet(surfaceKey(namespace, name), profileId)
      if (res.warning) setBindWarning(bindWarningCopy(res.warning))
    })
  }

  const hasProfiles = profiles.length > 0

  return (
    <Section
      title="Voices" icon={Mic2} iconTone="muted"
      hint="A voice profile is what renders speech — which engine, how it is conditioned, and whose consent is on record. Bind one per surface, or set a default for everything."
      right={hasProfiles ? (
        <Button variant="ghost" size="sm" onClick={() => setCreating((v) => !v)} ariaExpanded={creating}>
          <Plus size={14} aria-hidden="true" /> New voice
        </Button>
      ) : undefined}
    >
      {err && <p role="alert" className="mb-s text-danger" data-type="body-s">{err}</p>}

      {!hasProfiles ? (
        <div className="py-s">
          <p className="text-on-surface-low" data-type="body-s">
            No voice profiles yet — the built-in voice speaks everywhere. Turn your current
            text-to-speech selection into a profile in one step, then bind it per channel or agent.
          </p>
          <div className="mt-s flex items-center gap-s">
            <Button onClick={() => run('migrate', () => api.voiceMigrate())} loading={busy === 'migrate'}>
              <Wand2 size={14} aria-hidden="true" /> Migrate current voice
            </Button>
            <Button variant="ghost" onClick={() => setCreating(true)}>Create one manually</Button>
          </div>
        </div>
      ) : (
        <Table caption="Voice profiles">
          <THead>
            <tr><Th>Voice</Th><Th>Kind</Th><Th>Engine</Th><Th>State</Th><Th align="right">Actions</Th></tr>
          </THead>
          <tbody>
            {profiles.map((p) => <ProfileRow key={p.id} profile={p} busy={busy} run={run} />)}
          </tbody>
        </Table>
      )}

      {creating && (
        <div className="mt-s border-t border-outline-variant pt-s">
          <Field label="Name" hint="What you will call this voice.">
            <TextInput value={draftName} onChange={setDraftName} placeholder="Reading voice" ariaLabel="Voice name" />
          </Field>
          {/* Both kinds are offered, and a clone starts without its clip on purpose:
              the upload store fills a slot ON an existing profile, so the record has
              to exist before a clip can arrive. The row states the gap ("no reference
              clip") rather than the form pretending the kind is unavailable. */}
          <Field label="Kind" hint="A design voice is parameters and instructions. A clone voice conditions on a reference clip, which you add to the profile after creating it.">
            <Select value={draftKind} onChange={(v) => setDraftKind(v as VoiceProfileKind)}
              ariaLabel="Voice kind"
              options={[
                { value: 'design', label: 'Design — parameters and instructions' },
                { value: 'clone', label: 'Clone — conditioned on a reference clip' },
              ]} />
          </Field>
          <Field label="Engine" hint="Leave blank to use whichever text-to-speech model is bound in Models.">
            <div className="flex items-center gap-s">
              <TextInput value={draftProvider} onChange={setDraftProvider} placeholder="provider" ariaLabel="Voice provider" />
              <TextInput value={draftModel} onChange={setDraftModel} placeholder="model" ariaLabel="Voice model" />
            </div>
          </Field>
          <div className="mt-s flex items-center gap-s">
            <Button onClick={create} disabled={!draftName.trim()} loading={busy === 'create'}
              disabledReason={!draftName.trim() ? 'Name the voice first.' : undefined}>
              Create voice
            </Button>
            <Button variant="ghost" onClick={() => { setCreating(false); setErr('') }}>Cancel</Button>
          </div>
        </div>
      )}

      <h3 className="mt-6 mb-1 text-on-surface" data-type="label-s">Where each voice speaks</h3>
      <p className="mb-s text-on-surface-low" data-type="body-s">
        An explicit request wins, then this surface&apos;s binding, then the default, then the built-in
        voice. The effective column is the resolver&apos;s own answer, not a guess.
      </p>
      {bindWarning && (
        <p role="status" className="mb-s flex items-start gap-1.5 text-warn" data-type="body-s">
          <ShieldCheck size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{bindWarning}</span>
        </p>
      )}
      <BindingsTable bindings={bindings} profiles={profiles} busy={busy} run={run} />
      {hasProfiles && <BindForm profiles={profiles} busy={busy} onBind={bind} />}
    </Section>
  )
}

/** The bindings table and its effective column.
 *
 *  🔴 The resolutions are their OWN query, not a decoration folded into the profile
 *  read. Two reasons, and the second is the load-bearing one:
 *
 *  1. A resolver read that fails must not blank the profile list beside it.
 *  2. It must not be SWALLOWED either. Which voice wins is the whole question this
 *     table answers, so substituting `null` per row would quietly turn "we could not
 *     find out" into a rendered verdict. A separate query lets the failure surface
 *     exactly where it applies — this table says it could not read the resolver, and
 *     the rest of the section stays usable.
 */
function BindingsTable({ bindings, profiles, busy, run }: {
  bindings: VoiceBindings
  profiles: VoiceProfile[]
  busy: string
  run: (label: string, fn: () => Promise<unknown>) => Promise<void>
}) {
  const byId = new Map(profiles.map((p) => [p.id, p]))
  const surfaces = Object.keys(bindings).sort()
  // The bare `''` surface is the baseline: what speaks when nothing is bound.
  const wanted = ['', ...surfaces]
  const { data: effective, error, refresh } = useQuery(
    `settings:voice-effective:${wanted.join('|')}`,
    async () => {
      const rows = await Promise.all(wanted.map((s) => api.voiceResolve(s)))
      const out: Record<string, VoiceResolution> = {}
      wanted.forEach((s, i) => { out[s] = rows[i] })
      return out
    },
  )

  return (
    <>
      {error && (
        <p role="alert" className="mb-s text-danger" data-type="body-s">
          Could not read which voice wins for each surface. <TextLink onClick={refresh}>Retry</TextLink>
        </p>
      )}
      <Table caption="Voice bindings">
        <THead>
          <tr><Th>Surface</Th><Th>Voice</Th><Th>Effective</Th><Th align="right">Actions</Th></tr>
        </THead>
        <tbody>
          {surfaces.length === 0 && (
            <tr>
              <Td>Everywhere</Td>
              <Td><span className="text-on-surface-low">Not bound</span></Td>
              <Td><EffectivePill resolution={effective?.['']} pending={!effective && !error} /></Td>
              <Td align="right"><span className="text-on-surface-low" data-type="caption">—</span></Td>
            </tr>
          )}
          {surfaces.map((surface) => (
            <tr key={surface}>
              <Td>{surface === DEFAULT_KEY ? 'Everywhere (default)' : surface}</Td>
              <Td>{byId.get(bindings[surface])?.name || bindings[surface]}</Td>
              <Td><EffectivePill resolution={effective?.[surface]} pending={!effective && !error} /></Td>
              <Td align="right">
                <SquareIconButton label={`Unbind ${surface}`} loading={busy === `unbind:${surface}`}
                  onClick={() => run(`unbind:${surface}`, () => api.voiceBindingClear(surface))}>
                  <Trash2 size={14} aria-hidden="true" />
                </SquareIconButton>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  )
}

/** The resolver's verdict for one surface. An absent resolution is either still in
 *  flight or failed — both are stated, because falling through to "built-in" would
 *  manufacture an answer this surface has not given. */
function EffectivePill({ resolution, pending }: { resolution?: VoiceResolution; pending: boolean }) {
  if (!resolution) {
    return <span className="text-on-surface-low" data-type="caption">{pending ? 'Reading…' : 'Not read'}</span>
  }
  const copy = LEVEL_COPY[resolution.level] ?? LEVEL_COPY['built-in']
  return (
    <span className="flex items-center gap-1.5">
      <StatusPill tone={copy.tone}>{copy.label}</StatusPill>
      {resolution.locked && <StatusPill tone="primary">seed pinned</StatusPill>}
    </span>
  )
}

function ProfileRow({ profile, busy, run }: {
  profile: VoiceProfile
  busy: string
  run: (label: string, fn: () => Promise<unknown>) => Promise<void>
}) {
  const p = profile
  const engine = [p.provider, p.model].filter(Boolean).join(' · ')
  return (
    <tr>
      <Td>
        <span className="text-on-surface" data-type="label-s">{p.name || p.id}</span>
        <span className="ml-2 font-mono text-on-surface-low" data-type="caption">{p.id}</span>
      </Td>
      <Td><StatusPill tone={p.kind === 'clone' ? 'info' : 'neutral'}>{p.kind}</StatusPill></Td>
      <Td>{engine || <span className="text-on-surface-low">Bound model</span>}</Td>
      <Td>
        <span className="flex flex-wrap items-center gap-1.5">
          {p.locked && <StatusPill tone="primary">locked</StatusPill>}
          {/* Provenance, not permission: recorded consent is worth showing, and its
              absence is plain absence — no warn tone, because nothing is wrong. */}
          {p.verified_own_voice
            ? <StatusPill tone="ok">own voice verified</StatusPill>
            : <span className="text-on-surface-low" data-type="caption">No consent recorded</span>}
          {p.kind === 'clone' && !p.artifacts.ref_audio && <StatusPill tone="warn">no reference clip</StatusPill>}
        </span>
      </Td>
      <Td align="right">
        <span className="flex items-center justify-end gap-1">
          {p.locked ? (
            <SquareIconButton label={`Unlock ${p.name || p.id}`} loading={busy === `unlock:${p.id}`}
              onClick={() => run(`unlock:${p.id}`, () => api.voiceProfileUnlock(p.id))}>
              <Unlock size={14} aria-hidden="true" />
            </SquareIconButton>
          ) : (
            <SquareIconButton
              label={`Lock ${p.name || p.id} to its latest generation`}
              // Nothing to pin yet: locking pins a PAST generation, so with an empty
              // history the control is gated with the reason rather than absent.
              // The compound gate splits: the real gate is `disabled`, in-flight is `loading`.
              disabled={p.history_count === 0}
              loading={busy === `lock:${p.id}`}
              disabledReason={p.history_count === 0 ? 'Speak with this voice once — locking pins a generation you already heard.' : undefined}
              onClick={() => run(`lock:${p.id}`, () => api.voiceProfileLock(p.id, p.history_count - 1))}>
              <Lock size={14} aria-hidden="true" />
            </SquareIconButton>
          )}
          <SquareIconButton label={`Delete ${p.name || p.id}`} loading={busy === `del:${p.id}`}
            onClick={() => run(`del:${p.id}`, () => api.voiceProfileDelete(p.id))}>
            <Trash2 size={14} aria-hidden="true" />
          </SquareIconButton>
        </span>
      </Td>
    </tr>
  )
}

function BindForm({ profiles, busy, onBind }: {
  profiles: VoiceProfile[]
  busy: string
  onBind: (namespace: string, name: string, profileId: string) => Promise<void>
}) {
  const [namespace, setNamespace] = useState<string>(DEFAULT_KEY)
  const [name, setName] = useState('')
  const [profileId, setProfileId] = useState(profiles[0]?.id ?? '')
  const needsName = namespace !== DEFAULT_KEY
  const ready = !!profileId && (!needsName || !!name.trim())
  return (
    // `Select` and `TextInput` are `w-full` by design — they are built for a `Field`,
    // which owns the column. In this toolbar row the caller owns the width, so each
    // control is boxed; unboxed, every one of them takes a full line and the row
    // reads as three stacked bars instead of one bind control.
    <div className="mt-s flex flex-wrap items-end gap-s">
      <div className="w-52">
        <Select value={namespace} onChange={setNamespace} ariaLabel="Surface kind"
          options={[
            { value: DEFAULT_KEY, label: 'Everywhere (default)' },
            ...NAMESPACES.map((n) => ({ value: n, label: n })),
          ]} />
      </div>
      {needsName && (
        <div className="w-40">
          <TextInput value={name} onChange={setName} placeholder={`${namespace} name`} ariaLabel="Surface name" />
        </div>
      )}
      <div className="w-52">
        <Select value={profileId} onChange={setProfileId} ariaLabel="Voice to bind"
          options={profiles.map((p) => ({ value: p.id, label: p.name || p.id }))} />
      </div>
      <Button variant="secondary" onClick={() => onBind(namespace, name, profileId)}
        disabled={!ready} loading={busy === 'bind'}
        disabledReason={!ready ? 'Pick a voice, and name the surface.' : undefined}>
        Bind
      </Button>
    </div>
  )
}
