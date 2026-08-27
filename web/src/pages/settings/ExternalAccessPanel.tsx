import { useState } from 'react'
import { AlertTriangle, KeyRound, Plug2, ShieldOff, Trash2 } from 'lucide-react'
import { api, type ExternalAccessClient, type ExternalAccessSurface } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { Button } from '../../ui/Button'
import { PanelHeader, Section, RowGroup, Row, Toggle, NumberRow, StrListField } from './settingsUI'

// Named `CACHE_KEY`, not `KEY`. `dataLayerAdoption.test.ts` resolves an identifier
// handed to `useQuery` by matching `const <NAME> = '…'` across the WHOLE tree, so a
// module-local `const KEY` passed to the layer makes that scan adopt the six unrelated
// `const KEY` localStorage constants (`appearance`, `mode`, `nav-apps`, …) as cache
// namespaces and the ratchet goes red three directories away. A distinct name is the
// fix on both counts — it also makes the constant greppable.
const CACHE_KEY = 'settings:external-access'

/** How each surface describes itself. The backend sends the surface KEY; the prose
 *  lives here because it is UI copy, and a user reading "a2a" learns nothing. */
const SURFACE_COPY: Record<string, { label: string; hint: string }> = {
  openai: {
    label: 'OpenAI-compatible API',
    hint: 'Lets a tool that speaks the OpenAI API talk to one of your agents. The model name selects the agent.',
  },
  mcp: {
    label: 'MCP tool surface',
    hint: 'A curated, read-only MCP server your IDE can query. Cannot write, install, or change settings.',
  },
  a2a: {
    label: 'A2A gateway',
    hint: 'Agent-to-agent requests from another assistant you run.',
  },
  capture: {
    label: 'Capture proxy',
    hint: 'Records an external agent’s sessions by proxying them. Carries full prompts, so it is the most sensitive surface here.',
  },
  bridge: {
    label: 'Control bridge',
    hint: 'Semantic actions against your own dashboard. Loopback-only, always — it cannot be exposed remotely.',
  },
}

/** Settings → External Access — the shared inbound seam (EXTERNAL-ACCESS §1).
 *
 *  Everything else in PersonalClaw reaches OUT. This page is the only place anything
 *  reaches IN, so it is built to be read as a security surface rather than a feature
 *  list: the master switch is first and unmissable, every surface states why it is not
 *  serving, and each client's bindings are shown as the pins they are.
 *
 *  What this page deliberately CANNOT do, matching the backend: set a public URL, turn
 *  on remote access, or reveal a token. Those are config-file edits and a create-time
 *  reveal respectively. A control that is absent by design is noted in prose where a
 *  user would otherwise hunt for it. */
export function ExternalAccessPanel() {
  const { data, refresh } = useQuery(CACHE_KEY, () => api.externalAccess().catch(() => null), {
    persist: false,
  })
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [freshToken, setFreshToken] = useState<{ label: string; token: string } | null>(null)

  const act = async (fn: () => Promise<unknown>, tag: string) => {
    setBusy(tag)
    setError('')
    try {
      await fn()
      invalidateKeys(CACHE_KEY)
      refresh()
    } catch (e) {
      // Named, not swallowed: a kill switch that silently failed to flip is the worst
      // outcome on this page — the user believes a surface is off when it is serving.
      setError(e instanceof Error ? e.message : 'That change could not be saved.')
    } finally {
      setBusy('')
    }
  }

  const patchFlag = (path: string, value: boolean) =>
    act(() => api.patchConfig(path, value), path)

  // `NumberRow` keys its value out of a `cfg` map by the SAME string it PATCHes, so the
  // map is keyed by dotted config path rather than by the backend's short `caps` names.
  // Flattened here instead of reshaping the endpoint: `caps` is grouped the way an
  // operator reads it, and the PATCH path is the config's own spelling.
  const capsCfg: Record<string, unknown> = {
    'external_access.rate_rps': data?.caps?.rate_rps,
    'external_access.rate_burst': data?.caps?.rate_burst,
    'external_access.rate_concurrent': data?.caps?.rate_concurrent,
    'external_access.auto_disable_after_breaches': data?.caps?.auto_disable_after_breaches,
    'external_access.capture_retention_days': data?.caps?.capture_retention_days,
    // The NESTED spelling, deliberately: it is the only one `_EDITABLE_CONFIG` accepts for
    // this key, and the flat `capture_retention_days` above is the exception rather than
    // the rule. Reading a cap under one name and PATCHing it under another is how a
    // control ends up rendering a value it cannot save.
    'external_access.capture.upstream_allowlist': data?.caps?.capture_upstream_allowlist,
  }
  const patchCap = (path: string, value: never, onSaved: () => void, label?: string) =>
    act(async () => {
      await api.patchConfig(path, value)
      onSaved()
    }, label ?? path)

  const master = Boolean(data?.enabled)
  const surfaces = data?.surfaces ?? []
  const clients = data?.clients ?? []

  return (
    <div>
      <PanelHeader
        title="External Access"
        hint="Ways for something outside this machine to reach PersonalClaw. Every surface is off until you turn it on AND give it its own token, and every one is loopback-only unless you deliberately widen it in config.json. Nothing here is on by default." />

      {error && (
        <div
          className="mb-3 flex items-start gap-2 rounded-lg bg-surface-container px-4 py-3 text-[0.8125rem]"
          style={{ color: 'var(--color-danger)' }}
          role="alert">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      )}

      {data?.incident_active && (
        <div
          className="mb-3 flex items-start gap-2 rounded-lg bg-surface-container px-4 py-3 text-[0.8125rem]"
          style={{ color: 'var(--color-warning)' }}
          role="status">
          <ShieldOff size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span>
            Incident mode is on, so every inbound request is being refused regardless of the
            switches below. Clear it in Settings › Guardrails to resume.
          </span>
        </div>
      )}

      <Section
        title="Master switch"
        hint="One switch over all five surfaces. Off unmounts every one of them on the next request — no restart needed.">
        <RowGroup>
          <Row
            label="Allow inbound access"
            hint="While this is off, nothing below can serve, whatever its own switch says.">
            <Toggle
              on={master}
              onChange={(v) => patchFlag('external_access.enabled', v)}
              disabled={busy === 'external_access.enabled'}
              label="Allow inbound access" />
          </Row>
        </RowGroup>
      </Section>

      <Section
        title="Surfaces"
        hint="Each needs its own ≥32-byte token, created with `personalclaw inbound token create <surface>`. A token that is missing, too short, or equal to your dashboard token is refused — the surface will not mount and says so here.">
        {surfaces.length === 0 ? (
          <div className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low text-[0.8125rem]">
            Couldn’t read the surface configuration.
          </div>
        ) : (
          <RowGroup>
            {surfaces.map((s) => (
              <SurfaceRow
                key={s.surface}
                surface={s}
                master={master}
                busy={busy}
                onToggle={(v) => patchFlag(`external_access.${s.surface}.enabled`, v)} />
            ))}
          </RowGroup>
        )}
      </Section>

      <Section
        title="Limits"
        hint="Applied per client, not per surface, so one busy integration cannot spend another one’s allowance. A request over the rate gets a 429 and is recorded; a client that keeps going over is switched off and you are notified.">
        <RowGroup>
          <NumberRow
            label="Requests per second"
            hint="Sustained rate for each client. Fractional rates are legal but need a config.json edit — this stepper works in whole requests."
            cfg={capsCfg}
            field="external_access.rate_rps"
            min={1}
            max={1000}
            patch={patchCap} />
          <NumberRow
            label="Burst"
            hint="How many requests a client may make back-to-back before the sustained rate starts holding it back."
            cfg={capsCfg}
            field="external_access.rate_burst"
            min={1}
            max={10000}
            patch={patchCap} />
          <NumberRow
            label="Concurrent requests"
            hint="How many of a client’s requests may be in flight at once."
            cfg={capsCfg}
            field="external_access.rate_concurrent"
            min={1}
            max={256}
            patch={patchCap} />
          <NumberRow
            label="Switch a client off after"
            hint="Limit breaches within an hour before a client is disabled automatically. Set 0 to never do that — a client will then keep getting 429s indefinitely."
            cfg={capsCfg}
            field="external_access.auto_disable_after_breaches"
            min={0}
            max={10000}
            patch={patchCap} />
          <NumberRow
            label="Keep captured sessions for (days)"
            hint="Applies to the capture proxy only. It records full prompts, so this is the one limit here that is about privacy rather than load."
            cfg={capsCfg}
            field="external_access.capture_retention_days"
            min={0}
            max={3650}
            patch={patchCap} />
          {/* The one cap whose EMPTY default refuses the surface outright. It is an exclusive
              allow-list, so "nothing named yet" means "nowhere to forward" — right for egress,
              and indistinguishable from a broken proxy until the operator can see the list. */}
          <StrListField
            label="Capture upstream allow-list"
            hint="Hosts the capture proxy may forward a recorded turn to. Empty means it may forward NOWHERE, so a freshly enabled capture proxy refuses everything until you name at least one host (api.openai.com, api.anthropic.com)."
            cfg={capsCfg}
            field="external_access.capture.upstream_allowlist"
            patch={patchCap}
            placeholder="Add host…" />
        </RowGroup>
        <div className="mt-3 rounded-lg bg-surface-container px-4 py-3 text-on-surface-low text-[0.8125rem]">
          <div>
            <span className="text-on-surface">Public URL:</span>{' '}
            {data?.public_url ? (
              <code className="font-mono text-[0.75rem]">{data.public_url}</code>
            ) : (
              'not set — every surface is loopback-only'
            )}
          </div>
          {/* Shown but not editable, and the reason is stated rather than left as a missing
              control an operator would hunt for. The endpoint refuses a write to it. */}
          <div className="mt-1">
            This and each surface’s “allow remote” are the boundary for anything off this
            machine, so they are not editable here — they are deliberately a `config.json`
            edit. Tokens are not shown at all; only their hashes are stored.
          </div>
        </div>
      </Section>

      <Section
        title="Clients"
        hint="A client is one integration, with its own token and its own limits. Its bindings are pins, not defaults: a request that asks for a different agent or an un-listed tool is refused, never quietly redirected.">
        {freshToken && (
          <div
            className="mb-3 rounded-lg bg-surface-container px-4 py-3 text-[0.8125rem]"
            role="status">
            <div className="flex items-center gap-2 font-medium">
              <KeyRound size={14} aria-hidden /> Token for “{freshToken.label}”
            </div>
            <code className="mt-2 block break-all rounded bg-surface-high px-2 py-1.5 font-mono text-[0.75rem]">
              {freshToken.token}
            </code>
            <div className="mt-2 text-on-surface-low">
              Copy it now. Only a hash is stored, so this is the one and only time it can be
              shown. If you lose it, revoke the client and create another.
            </div>
            <div className="mt-2">
              <Button size="xs" variant="ghost" onClick={() => setFreshToken(null)}>
                Done
              </Button>
            </div>
          </div>
        )}
        {clients.length === 0 ? (
          <div className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low text-[0.8125rem]">
            No clients yet. Create one with <code>personalclaw inbound client create</code> — or
            keep using a plain surface token, which works but cannot be scoped or revoked on its
            own.
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {clients.map((c) => (
              <ClientRow
                key={c.client_id}
                client={c}
                busy={busy}
                onToggleDisabled={(v) =>
                  act(() => api.externalAccessSetClientDisabled(c.client_id, v), c.client_id)
                }
                onRevoke={() =>
                  act(() => api.externalAccessRevokeClient(c.client_id), c.client_id)
                } />
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Remote access"
        hint="Not editable here, on purpose.">
        <div className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low text-[0.8125rem]">
          {data?.public_url ? (
            <>
              This instance answers to <code>{data.public_url}</code>. A remote request whose
              Host header doesn’t match it exactly is refused.
            </>
          ) : (
            <>No public URL is set, so every surface is loopback-only.</>
          )}
          <div className="mt-2">
            The public URL and each surface’s <code>allow_remote</code> flag are edited in{' '}
            <code>config.json</code>, not from this page — widening a network surface should be a
            deliberate act, not one click. Prefer an SSH tunnel to loopback over opening a port.
          </div>
        </div>
      </Section>
    </div>
  )
}

function SurfaceRow({
  surface,
  master,
  busy,
  onToggle,
}: {
  surface: ExternalAccessSurface
  master: boolean
  busy: string
  onToggle: (v: boolean) => void
}) {
  const copy = SURFACE_COPY[surface.surface] ?? {
    label: surface.surface,
    hint: 'An inbound surface.',
  }
  const path = `external_access.${surface.surface}.enabled`
  // Why it cannot serve, most specific cause first. A surface that is "on" but silent is
  // the failure this line exists to prevent, so the reason is stated on the row itself
  // rather than left to the logs.
  const blocked = !surface.token_configured
    ? surface.token_problem || 'no usable token'
    : !master
      ? 'the master switch is off'
      : ''
  return (
    <Row
      label={copy.label}
      hint={
        surface.loopback_only ? `${copy.hint} Cannot be exposed remotely.` : copy.hint
      }>
      <div className="flex items-center gap-2">
        {surface.enabled && blocked && (
          <span
            className="shrink-0 rounded-pill px-2 py-0.5 text-[0.75rem]"
            style={{
              background: 'color-mix(in srgb, var(--color-warning) 14%, transparent)',
              color: 'var(--color-warning)',
            }}
            title={`On, but not serving: ${blocked}.`}>
            not serving
          </span>
        )}
        {surface.enabled && !blocked && surface.allow_remote && (
          <span
            className="shrink-0 rounded-pill px-2 py-0.5 text-[0.75rem]"
            style={{
              background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)',
              color: 'var(--color-danger)',
            }}
            title="Reachable from outside this machine.">
            remote
          </span>
        )}
        <Toggle
          on={surface.enabled}
          onChange={onToggle}
          disabled={busy === path}
          label={copy.label} />
      </div>
    </Row>
  )
}

function ClientRow({
  client,
  busy,
  onToggleDisabled,
  onRevoke,
}: {
  client: ExternalAccessClient
  busy: string
  onToggleDisabled: (v: boolean) => void
  onRevoke: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const pins = [
    client.agent ? `agent ${client.agent}` : '',
    client.tools.length ? `${client.tools.length} tool${client.tools.length === 1 ? '' : 's'}` : '',
    Object.keys(client.scope).length ? 'scoped' : '',
  ].filter(Boolean)
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg bg-surface-container px-3 py-2.5">
      <div className="min-w-40 flex-1">
        <div className="truncate text-on-surface text-[0.8125rem]">
          {client.label || 'Unnamed client'}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-2 text-on-surface-low text-[0.75rem]">
          <span className="inline-flex items-center gap-1">
            <Plug2 size={10} aria-hidden /> {client.surfaces.join(', ') || 'no surfaces'}
          </span>
          {pins.length > 0 && <span>pinned to {pins.join(' · ')}</span>}
          <span>
            {client.requests_seen} request{client.requests_seen === 1 ? '' : 's'} seen
            {client.refusals_seen > 0 && `, ${client.refusals_seen} refused`}
          </span>
        </div>
      </div>
      {client.disabled && (
        <span
          className="shrink-0 rounded-pill px-2 py-0.5 text-[0.75rem]"
          style={{
            background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)',
            color: 'var(--color-danger)',
          }}
          title="Every request from this client is refused.">
          disabled
        </span>
      )}
      <div className="flex shrink-0 items-center gap-2">
        <Toggle
          on={!client.disabled}
          onChange={(v) => onToggleDisabled(!v)}
          disabled={busy === client.client_id}
          label={`${client.label || client.client_id} enabled`} />
        {confirming ? (
          <>
            <Button
              size="xs"
              variant="ghost"
              disabled={busy === client.client_id}
              onClick={() => {
                setConfirming(false)
                onRevoke()
              }}>
              Revoke for good
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </>
        ) : (
          // Two steps, because revocation destroys the token and there is no undo — the
          // reversible control (the switch, left) is the one that reads as one click.
          <Button
            size="xs"
            variant="ghost"
            disabled={busy === client.client_id}
            onClick={() => setConfirming(true)}>
            <Trash2 size={12} aria-hidden /> Revoke
          </Button>
        )}
      </div>
    </div>
  )
}
