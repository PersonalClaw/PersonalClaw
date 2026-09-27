import type { InboxProvider } from '../../lib/api'

/** The polled sources that read `inbox.watched_channels`, by the name the inbox calls them.
 *
 *  The list is shown only while one of these exists, and named by them: it is handed to every
 *  source's poll, but a source reads it only when it says so (`watches_channels`), so a list no
 *  polled source reads would be a control that changes nothing. */
export function watchedChannelReaders(providers: InboxProvider[] | null | undefined): string[] {
  return (providers ?? [])
    .filter((p) => p.polled && p.watches_channels)
    .map((p) => p.display_name || p.name)
}

/** The stored list's names, whatever the config holds. */
export function storedChannels(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : []
}

/** The sentence under "Channels to read", naming who reads them. */
export function watchedChannelsHint(readers: string[]): string {
  const who = readers.length > 1
    ? `${readers.slice(0, -1).join(', ')} and ${readers[readers.length - 1]} read`
    : `${readers[0] ?? 'Your chat app'} reads`
  return `${who} these channels into your Inbox. Add each by its id; the app must be able to read the channel. `
    + "A channel's first read starts after its newest message, so only what arrives after you add it is collected."
}
