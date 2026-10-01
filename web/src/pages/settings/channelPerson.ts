import type { ChannelOwnerRef } from '../../lib/api'

// How a person a chat channel knows is named, on every page that shows one: Settings › Sender
// trust, a channel's Owner on its Configure page, and the channel's status row. They read the same
// trust-list entry, so they name it through this one formatter and cannot drift apart.

/** The words for a channel's id for someone: "Telegram id 7000000001". */
export function channelIdWords(channel: string, id: string): string {
  return `${channel} id ${id}`
}

/** A person on a channel: the name its trust list knows them by, with the channel's id for them as
 *  the quieter detail under it (`detail`). With no name, the id leads, said as the channel's id,
 *  and there is no detail: the id would only repeat. */
export function channelPerson(channel: string, id: string, name: string): { name: string; detail: string } {
  return name ? { name, detail: channelIdWords(channel, id) } : { name: channelIdWords(channel, id), detail: '' }
}

/** Who a channel reaches its owner as. A shared id with no name here is given bare, because it may
 *  be another platform's id, not this channel's; the sentence around it says which id it is. */
export function ownerWho(channel: string, owner: ChannelOwnerRef): string {
  if (owner.source === 'shared' && !owner.name) return owner.id
  return channelPerson(channel, owner.id, owner.name).name
}
