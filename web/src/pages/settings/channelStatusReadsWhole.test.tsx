import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProviderCard } from './ProviderCard'
import type { ChannelRuntime, SettingsProvider } from '../../lib/api'

// ── A channel's status sentence is shown whole ──────────────────────────────────────────────────
//
// The channel strip clipped the health detail to one line at 60% of the card (`truncate`), with no
// title to read the rest. The sentences a failing channel reports are long because they say what to
// do: Email Channel's refused certificate reads "…is not trusted (…), so the password was not sent.
// If the server uses your own certificate authority, set CA Certificate File…", and the card showed
// "…the TLS certificate of the IMAP server 127.0.0.1:18993 is…" (measured in a browser: 602 of
// 1,978 px shown). jsdom lays nothing out, so this pins what it can see: the whole sentence is in
// the strip, and nothing between it and the strip is styled to clip it.

const REFUSED =
  'Inbound NOT RECEIVING — the last IMAP poll failed: the TLS certificate of the IMAP server ' +
  'imap.example.test:993 is not trusted (unable to get local issuer certificate), so the password ' +
  "was not sent. If the server uses your own certificate authority, set CA Certificate File to that " +
  "authority's certificate. It tries again, waiting longer after each failure, up to 15 minutes."

const ext: SettingsProvider = {
  name: 'email-channel', displayName: 'Email Channel', enabled: true, managed: true,
  provider: { type: 'channel', capabilities: ['messaging'], hasConfigSchema: true },
  availability: { state: 'available', reason: '', checkedAt: 1 },
}

const channel: ChannelRuntime = {
  name: 'email', display_name: 'Email', connected: true, app: 'email-channel',
  capabilities: {}, health: { state: 'error', detail: REFUSED }, owner: { id: '', source: '', name: '' },
}

/** Classes that cut a line short instead of letting it wrap. */
const CLIPS = /(^|\s)(truncate|text-ellipsis|whitespace-nowrap|line-clamp-\d+|max-w-\[[^\]]*\])(\s|$)/

describe('a channel status sentence', () => {
  it('is shown whole, up to the setting it names', () => {
    render(<ProviderCard ext={ext} channel={channel} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    const sentence = screen.getByText(REFUSED)
    const strip = screen.getByRole('button', { name: 'Test: email' }).closest('div.border-t')
    expect(strip, 'the status strip').toBeTruthy()
    expect(strip!.contains(sentence)).toBe(true)
    for (let n: Element | null = sentence; n && n !== strip!.parentElement; n = n.parentElement) {
      expect(n.className, `<${n.tagName.toLowerCase()}> clips the sentence`).not.toMatch(CLIPS)
    }
  })
})
