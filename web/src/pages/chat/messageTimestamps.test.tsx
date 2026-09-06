import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AssistantActions, UserActions } from './MessageActions'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'

// ── "Show timestamps" was a switch with nothing behind it ───────────────────────────────────────
//
// The preference shipped persisted through the config, exposed by two settings controls, and read by
// nothing: measured before this change, `show_timestamps` appeared in three web files (the type
// declaration and the two writers) and in no Python outside the config field and the API surface. So
// the row it promised — "Display a time on each message" — never existed.
//
// 🔑 THE DATA WAS ALREADY THERE. `chatTypes.ts` declares `ChatTurn.ts` ("source message timestamp")
// and it arrives on every rendered turn; it was used only as an identity key for edit-resend. So this
// is a display, not a plumbing job.
//
// 🪤 THE ONE REAL DESIGN DECISION WAS THAT THE ACTION ROW IS HIDDEN AT REST. `MessageActions` reveals
// the whole row on `group-hover/msg`, so dropping a stamp into it would have produced a timestamp
// visible only under the pointer — which is not what a reader who switched the setting ON asked for.
// The row was split: the stamp sits outside the reveal wrapper, the buttons stay inside it. The two
// structural tests below are what keep that split honest, because it is invisible to a render that
// only checks the text is present.
//
// 🪤 AND AN UNREADABLE STAMP MUST RENDER NOTHING. `lib/epoch.ts` already argued this for relative
// time ("`NaN` on screen is worse than a blank"), after six dashboard rows shipped reading "in NaNd".
// The absolute formatters added here inherit that parser and that contract.

const SRC = join(process.cwd(), 'src')
// Comments stripped before every structural match: prose quoting a token it explains otherwise
// counts as an occurrence of it.
const codeOf = (...p: string[]) =>
  readFileSync(join(SRC, ...p), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const NOOP = { onCopy: vi.fn(), onRegenerate: vi.fn(), onFork: vi.fn(), onSpeak: vi.fn() }
const TS = '2026-09-07T14:32:05.123456+00:00'

describe('the absolute formatters refuse to invent a time', () => {
  it('renders nothing for every unreadable stamp', () => {
    // The empty string is the live case, not a hypothetical: `/api/chat/sessions` sends stamp fields
    // as `''`, which is why `epochSeconds` handles it and why these three must inherit that.
    for (const bad of [undefined, null, '', 'not a date', NaN]) {
      expect(clockTime(bad as string), `clockTime(${String(bad)})`).toBe('')
      expect(fullStamp(bad as string), `fullStamp(${String(bad)})`).toBe('')
      expect(isoStamp(bad as string), `isoStamp(${String(bad)})`).toBe('')
    }
  })

  it('reads an ISO string and a numeric epoch alike', () => {
    // Both shapes reach the UI: `ChatTurn.ts` is an ISO string, while sibling stamp fields are
    // numeric seconds. A formatter that handled only one would silently blank the other.
    expect(clockTime(TS)).not.toBe('')
    expect(clockTime(1788784325)).not.toBe('')
    expect(isoStamp(TS)).toBe('2026-09-07T14:32:05.123Z')
  })

  it('gives the title the date the visible form cannot carry', () => {
    // `14:32` is ambiguous the moment a conversation is a day old, so the long form must actually
    // include the date rather than repeating the clock time.
    const full = fullStamp(TS)
    expect(full).not.toBe('')
    expect(full).toMatch(/2026/)
    expect(full.length).toBeGreaterThan(clockTime(TS).length)
  })
})

describe('a turn shows its time only when the host passes one', () => {
  it('renders a real <time> with a machine-readable value', () => {
    render(<AssistantActions text="hi" isLast ts={TS} {...NOOP} />)
    const el = screen.getByText(clockTime(TS))
    expect(el.tagName).toBe('TIME')
    expect(el.getAttribute('dateTime')).toBe(isoStamp(TS))
    expect(el.getAttribute('title')).toBe(fullStamp(TS))
  })

  it('renders no time at all when the stamp is absent', () => {
    // Absent covers BOTH "the reader has the setting off" and "this turn has no stamp yet, mid-stream".
    // One branch for both is the reason the component never learns about the preference.
    const { container } = render(<AssistantActions text="hi" isLast {...NOOP} />)
    expect(container.querySelectorAll('time')).toHaveLength(0)
  })

  it('renders nothing rather than a placeholder for an unreadable stamp', () => {
    const { container } = render(<AssistantActions text="hi" isLast ts="not a date" {...NOOP} />)
    expect(container.querySelectorAll('time')).toHaveLength(0)
    expect(container.textContent).not.toMatch(/NaN|Invalid/)
  })

  it('shows it on a USER turn too, on that turn’s own edge', () => {
    render(<UserActions text="hi" ts={TS} onEdit={vi.fn()} onFork={vi.fn()} />)
    expect(screen.getByText(clockTime(TS)).tagName).toBe('TIME')
  })
})

describe('🔑 the stamp persists while the buttons keep their hover reveal', () => {
  // The whole point of the chosen treatment. Both assertions are structural because a text query
  // passes either way: the stamp is in the DOM whether or not its ancestor is transparent at rest.

  it('the stamp is NOT inside the hover-reveal wrapper', () => {
    render(<AssistantActions text="hi" isLast ts={TS} {...NOOP} />)
    const stamp = screen.getByText(clockTime(TS))
    expect(stamp.closest('.opacity-0'), 'the time would only appear on hover').toBeNull()
  })

  it('the buttons ARE inside it, so the row still rests quiet', () => {
    // The other half: a fix that made the stamp permanent by making the whole row permanent would
    // pass the test above and change every message at rest.
    render(<AssistantActions text="hi" isLast ts={TS} {...NOOP} />)
    const copy = screen.getByRole('button', { name: 'Copy' })
    expect(copy.closest('.opacity-0'), 'the action buttons stopped being hover-revealed').not.toBeNull()
  })

  it('keeps the focus escape hatch on the wrapper that holds the buttons', () => {
    // `focus-within` is what makes the row reachable by keyboard, and it has to sit on the element
    // the buttons are in — left on the outer row it would guard nothing, since the row no longer hides.
    const code = codeOf('pages', 'chat', 'MessageActions.tsx')
    const reveal = code.match(/const REVEAL =([\s\S]*?)\n\n/)?.[1] ?? ''
    expect(reveal, 'the REVEAL class list').not.toBe('')
    expect(reveal).toMatch(/opacity-0/)
    expect(reveal).toMatch(/group-hover\/msg:opacity-100/)
    expect(reveal).toMatch(/focus-within:opacity-100/)
  })

  it('uses the type role the design system reserves for a timestamp', () => {
    // `tokens.css` documents `caption` as the home for "timestamp micro-text"; a raw font-size here
    // would be the type-scale drift that tier was created to absorb.
    render(<AssistantActions text="hi" isLast ts={TS} {...NOOP} />)
    const stamp = screen.getByText(clockTime(TS))
    expect(stamp.getAttribute('data-type')).toBe('caption')
    expect(stamp.className).toMatch(/tabular-nums/)   // a clock must not jitter as digits change
    expect(stamp.className).toMatch(/text-on-surface-low/)
  })
})

describe('the preference actually reaches the transcript', () => {
  // Everything above can pass while the setting is still inert — which is the defect being fixed.

  it('chat reads the stored preference and gates the stamp on it', () => {
    const code = codeOf('pages', 'ChatPage.tsx')
    expect(code.length, 'read ChatPage.tsx').toBeGreaterThan(1000)
    expect(code).toMatch(/useQuery\(\s*'chat:show-timestamps'/)
    expect(code).toMatch(/\.show_timestamps\b/)
    // Gating by withholding the value, so the row never sees the preference.
    expect(code).toMatch(/showTimestamps \? turn\.ts : undefined/)
    // …and both rows actually receive it. Two call sites, so one wired row is not enough.
    expect(code.match(/ts=\{stampOf\(turn\)\}/g) ?? [], 'both action rows pass the stamp').toHaveLength(2)
  })

  it('both writers bust the reader key, so the toggle changes the transcript at once', () => {
    expect(codeOf('pages', 'settings', 'ChatPanel.tsx')).toMatch(/invalidateKeys\('chat:show-timestamps'\)/)
    expect(codeOf('pages', 'settings', 'settingsWidgets.tsx')).toMatch(/'chat:show-timestamps'/)
  })

  it('the settings copy already described this, and now it is true', () => {
    const panel = codeOf('pages', 'settings', 'ChatPanel.tsx')
    const row = panel.match(/label="Show timestamps" hint="([^"]*)"/)?.[1] ?? ''
    expect(row, 'the Show timestamps row hint').not.toBe('')
    expect(row).toMatch(/time on each message/)
  })
})
