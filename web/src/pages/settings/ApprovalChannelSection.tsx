import { useEffect, useState } from 'react'
import { api, type ChannelRuntime } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { Field, Section } from './settingsUI'
import { Select } from '../../ui/forms'
import { LoadError } from '../../ui/ListScaffold'
import { notify } from '../../app/appSdk'

/** The in-app transport is how this dashboard talks, not a channel that can reach you elsewhere. */
const WEB_UI = 'webui'

/** A chat channel an approval can ask you on: one that knows who you are (it has your owner id). */
function knowsYou(c: ChannelRuntime): boolean {
  return c.name !== WEB_UI && !!c.owner?.id
}

/** The channels that know you, in the order the gateway tries them when nothing is chosen: by name. */
function pairedInNameOrder(channels: ChannelRuntime[]): ChannelRuntime[] {
  return channels.filter(knowsYou).sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
}

/** The rule the setting sits under: `channel_delivery.approval_providers(origin)` asks the channel a
 *  chat started on first, and only then what this setting says. */
const ORIGIN_FIRST = 'A chat that started on a chat channel is asked in that chat.'
/** What this setting governs: every approval whose chat did not start on a channel, and only while
 *  the Approval needed row delivers to Channel DM (`approval_state._asking_channels`): without that
 *  target such an approval is asked on no channel. */
const THE_REST = 'When Approval needed below delivers to Channel DM, everything else (a chat here, an unattended run, a trigger)'

/** What happens to an approval under this choice, in words. It is the control's description, so it
 *  has to be what `channel_delivery.approval_providers` / `approval_delivery` actually do: the chat's
 *  own channel first, then the chosen channel alone (and nobody else while it cannot ask), or the
 *  first connected channel that knows you, by name. */
export function approvalRouteSentence(chosen: string, channels: ChannelRuntime[]): string {
  const paired = pairedInNameOrder(channels)
  if (!chosen) {
    if (paired.length === 0) {
      return `${ORIGIN_FIRST} ${THE_REST} waits here in PersonalClaw, because no chat channel knows you yet. Pair yourself as a channel's owner on its Configure page to be asked there.`
    }
    if (paired.length === 1) {
      return `${ORIGIN_FIRST} ${THE_REST} asks on ${paired[0].display_name}, the only chat channel that knows you. When it can't reach you, the approval waits here in PersonalClaw.`
    }
    return `${ORIGIN_FIRST} ${THE_REST} asks the first connected channel that knows you, in name order: ${paired.map((c) => c.display_name).join(', ')}. Choose one to be asked only there.`
  }
  const channel = channels.find((c) => c.name === chosen)
  const name = channel?.display_name || chosen
  if (!channel) {
    return `${ORIGIN_FIRST} ${THE_REST} waits here in PersonalClaw, because ${name} is not set up here, and no other channel asks. Choose another channel, or the first connected channel that knows you.`
  }
  if (!knowsYou(channel)) {
    return `${ORIGIN_FIRST} ${THE_REST} waits here in PersonalClaw, because ${name} doesn't know who you are yet, and no other channel asks. Pair yourself as its owner on its Configure page.`
  }
  return `${ORIGIN_FIRST} ${THE_REST} asks only on ${name}. When it can't reach you, the approval waits here in PersonalClaw, and no other channel is asked.`
}

/** "Send approvals to" — which chat channel asks you to approve a tool call (`agent.approval_channel`).
 *
 *  Approvals went to the first paired channel in name order (Discord, then Email, Slack, Telegram),
 *  so someone with four channels paired had no say in which one asked. The default is still that
 *  order; choosing a channel makes it the only one that asks. It lives beside the per-kind rules
 *  because an approval with no channel origin reaches a channel through the Approval needed row's
 *  Channel DM target (and a subagent's request to start always asks there). A chat that started on
 *  a channel is asked in that chat without either. */
export function ApprovalChannelSection({ onSaved }: { onSaved: () => void }) {
  const { data, error, refresh } = useQuery('settings:approval-channel', async () => {
    const [config, channels] = await Promise.all([api.personalclawConfig(), api.channels()])
    return { chosen: String(config.agent?.approval_channel ?? ''), channels }
  })
  // Optimistic like the panel's other controls; `null` until the read lands.
  const [chosen, setChosen] = useState<string | null>(null)
  useEffect(() => { if (data) setChosen(data.chosen) }, [data])

  const hint = 'A chat that started on a chat channel is always asked in that chat. This setting decides where the rest ask you: when Approval needed below delivers to Channel DM, and when a subagent asks to start. Approve and Deny on the channel answer it, and so does PersonalClaw.'
  if (!data && error) return <LoadError what="where approvals go" error={error} onRetry={refresh} />
  if (!data || chosen === null) {
    return (
      <Section title="Approvals on chat channels" hint={hint}>
        <div data-type="body-s" className="text-on-surface-low">Checking your chat channels…</div>
      </Section>
    )
  }

  const paired = pairedInNameOrder(data.channels)
  const options = [
    { value: '', label: 'The first connected channel that knows you' },
    ...paired.map((c) => ({ value: c.name, label: c.display_name })),
  ]
  // A stored choice that can no longer ask stays visible as what it is, rather than the select
  // silently showing the default over a value that still decides where approvals go.
  if (chosen && !paired.some((c) => c.name === chosen)) {
    const channel = data.channels.find((c) => c.name === chosen)
    options.push({
      value: chosen,
      label: `${channel?.display_name || chosen} (${channel ? "doesn't know you" : 'not set up here'})`,
    })
  }

  const save = (value: string) => {
    const prev = chosen
    setChosen(value)
    api.patchConfig('agent.approval_channel', value).then(onSaved).catch((e) => {
      setChosen(prev)
      notify(`Couldn't change where approvals go: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <Section title="Approvals on chat channels" hint={hint}>
      <Field label="Send approvals to" hint={approvalRouteSentence(chosen, data.channels)}>
        <Select value={chosen} onChange={save} options={options} />
      </Field>
    </Section>
  )
}
