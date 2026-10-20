import type { ChannelRuntime } from '../../lib/api'

/** The dashboard's own transport. It is listed with the chat channels, and it is not somewhere a
 *  result can be sent. Mirrors `channel_transports.WEBUI_TRANSPORT`. */
export const DASHBOARD_TRANSPORT = 'webui'

/** A schedule's `channel`, split: `<name>` means your direct messages on that chat channel, and
 *  `<name>:<target>` a chat on it. It is the delivery route without its `channel:` prefix, which is
 *  what the API takes and what a schedule row carries. Only the first colon splits: the target is
 *  the channel's own id and may hold one. */
export function splitChannel(value: string | null | undefined): { name: string; target: string } {
  const v = (value ?? '').trim()
  const i = v.indexOf(':')
  return i < 0 ? { name: v, target: '' } : { name: v.slice(0, i), target: v.slice(i + 1) }
}

export function joinChannel(name: string, target: string): string {
  const n = name.trim()
  const t = target.trim()
  if (!n) return ''
  return t ? `${n}:${t}` : n
}

/** The channels a result can go to: every registered channel but the dashboard itself. */
export function chatChannels(channels: ChannelRuntime[] | undefined): ChannelRuntime[] {
  return (channels ?? []).filter((c) => c.name !== DASHBOARD_TRANSPORT)
}

/** How a schedule's channel reads on its row: "NAME, your DMs" or "NAME · target", with the
 *  channel's own display name. A name that is no channel set up here reads as itself: it is not
 *  "your DMs" anywhere, and the row's `channel_problem` says why. */
export function channelLabel(value: string | null | undefined, channels: ChannelRuntime[] | undefined): string {
  const { name, target } = splitChannel(value)
  if (!name) return ''
  const shown = chatChannels(channels).find((c) => c.name === name)?.display_name
  if (!shown) return target ? `${name} · ${target}` : name
  return target ? `${shown} · ${target}` : `${shown}, your DMs`
}
