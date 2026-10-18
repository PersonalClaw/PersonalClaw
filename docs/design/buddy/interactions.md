# Buddy interactions

Every input has one outcome in every presence mode. The buddy is another client of the owner's own
routes: it adds no authority, and anything that acts needs an explicit click or key press.

| Input | Hidden | Peek | Compact | Expanded | Floating | Alert shown |
|---|---|---|---|---|---|---|
| Pointer rests on the top-edge zone | peek | stays | — | — | — | — |
| Pointer rests on the character for `hoverExpand` | — | expanded | expanded | stays | a hover card with the line | stays |
| Click on the character | — | expanded | expanded | — | opens the card beside it | nothing; only its controls act |
| Drag the character | — | undocks | undocks | undocks, card closed | moves | moves, carrying the alert |
| Drop near the top centre | — | — | — | — | docks | docks, carrying the alert |
| Right-click | — | the menu | the menu | the menu | the menu | the menu |
| ⌘Enter (buddy focused) | — | — | — | — | — | approves the focused request; a broad-reach one asks to confirm first |
| Enter (buddy focused) | — | — | — | — | — | opens a focused error; never approves |
| Esc (buddy focused) | — | — | — | compact | closes the card | closes without deciding; the alert stays |
| Tab, Shift-Tab (buddy focused) | — | — | — | moves through the controls | moves through the controls | moves through the brief and controls |
| Focus shortcut | expanded, focused | expanded, focused | expanded, focused | focused | the card, focused | the alert, focused |
| Push-to-talk chord | listening | listening | listening | listening | listening | listening (the alert is held) |

Keys are written for macOS: elsewhere ⌘ is Ctrl, as in the app's keyboard model
([`web/REDESIGN.md` §6](../../../web/REDESIGN.md#6-keyboard-model)). What Enter does on a question
is still open. The chat's own question card never sends on Enter, and [#3832] proposes that Enter
chooses a focused option and sends only from a focused Send.

## The context menu

Hide for an hour · Hide until tomorrow · Dock or Float · Buddy settings… · Open PersonalClaw, and an
item that turns Buddy status off, the clear way out ([#3901]).

## Keyboard and focus

- The buddy window never takes focus by itself: it shows without activating, and takes keyboard
  focus only when the owner clicks into it or presses the focus shortcut (set in Settings, none by
  default).
- **Still open: the focus shortcut against the keyboard model.** The focus shortcut is a chord the
  owner sets, with the push-to-talk chord grammar ([`settings.md`](settings.md)), but the redesign's
  keyboard model keeps the push-to-talk chord as the one configurable chord
  ([`web/REDESIGN.md` §6](../../../web/REDESIGN.md#6-keyboard-model)). [#3822] and [#3901] settle
  which gives way. Without that chord, a keyboard-only owner has no key into the buddy window.
- ⌘Enter approves only the request that has focus inside the focused buddy window. Esc closes
  without deciding, and Deny is an explicit control with no key of its own. A broad-reach approval
  (a delete, an overwrite outside the workspace, a send to other people, or spending) asks for a
  second confirmation before it answers. No alert action fires on arrival, on a hover or on a stale
  request, and a global key press never answers anything ([#3832]).
- Focus order in the expanded card: header, the alert's brief, its controls, the in-sight rows, Open
  in PersonalClaw.
- Leaving the buddy (Esc on an empty card, or clicking elsewhere) returns focus to the app that had
  it.

## Screen readers

- The buddy window's one line is a polite live region; an alert is announced once when it arrives,
  as `Needs you: <the line>`, and never again while it waits.
- The character is decorative (`aria-hidden`); its state is always in words beside it.
- Every control has a name that includes what it acts on, as the app's approval prompt already
  does: `Allow <tool>`, and `Deny <tool>` followed by what declining does when declining ends the
  turn ([#3899]).
- Because a screen reader follows the focused window, alerts also go to an OS notification for
  owners who turn that on.

## Pointer and click-through

The window passes every click through except inside the character's shape and the open card, so the
owner can use what is underneath it. The shape is tested on every pointer move only while the buddy
is visible.

## Later work

### Play inputs

Later work: play ([#3894]).

With play, two rows of the table above change:

| Input | Hidden | Peek | Compact | Expanded | Floating | Alert shown |
|---|---|---|---|---|---|---|
| Click on the character | — | expanded | expanded | a play hit | opens the card beside it | nothing (no play) |
| Quick repeated hits | — | expanded | expanded | annoyed, then dizzy | annoyed, then dizzy | nothing |

A click on a control or on the badge is never a hit, and the context menu gains Play reactions on or
off. Settled for its return: the hit chain ends at annoyed, with no dizzy ([#4570]).

### The drop choice

Later work: drop and quick ask ([#3895]).

| Input | Hidden | Peek | Compact | Expanded | Floating | Alert shown |
|---|---|---|---|---|---|---|
| File dragged over | peek with the drop target | the drop target | the drop target | the drop target | the drop target | refused |
| File dropped | — | swallow, then the choice | same | same | same | refused |

Dropping a file shows three choices, each a click: **Ask about it** (a new chat opens with the file
attached and the cursor in the message field; nothing is sent until the owner sends it), **Add to
Knowledge** (the file is ingested, scanned like any upload, and the line says when it is done), and
**Put it aside** (nothing happens). If no choice is made before `collapse`, the file is put aside. A
file always goes through the same scan as any other upload.

When the drop returns, a dropped file is never sent without a separate explicit click, and before
anything runs each choice shows where it goes, the permission it acts under and how to undo it
([#3895]). Settled for its return: the drop is the claw's own catch, which closes around the file
and holds it up while the choice opens, never a swallow ([#4570]).

### Quick ask

Later work: drop and quick ask ([#3895]).

The expanded card has a one-line ask field below the header when no alert shows. Typing, or
dictating with push-to-talk while no composer has focus, fills it. A click on Send starts a new chat
with that message, Enter sends nothing, and Esc clears it. It never sends on its own, and before it
sends it shows where the message goes and the permission it acts under ([#3895]). In the focus order
it comes after the alert's controls.

[#3822]: https://github.com/PersonalClaw/PersonalClaw/issues/3822
[#3832]: https://github.com/PersonalClaw/PersonalClaw/issues/3832
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#3899]: https://github.com/PersonalClaw/PersonalClaw/issues/3899
[#3901]: https://github.com/PersonalClaw/PersonalClaw/issues/3901
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
