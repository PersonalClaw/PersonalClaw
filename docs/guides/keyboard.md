# Keyboard shortcuts

PersonalClaw's shortcuts, and the rules the approval key follows. The command palette lists all of
them with their keys (press ⌘K and type "shortcut"). The sidebar, terminal and focus-mode
toggles, the folded composer and an approval's Allow show their key in their tooltip.

Keys are written for macOS. Elsewhere ⌘ is Ctrl and ⌥ is Alt. Shortcuts can't be remapped.

## Moving around

| Keys | Does |
|---|---|
| ⌘K or ⌘⇧P | opens the command palette |
| ⌘B | shows or hides the sidebar |
| ⌘J or ⌘\` | shows or hides the terminal drawer |
| ⌘\\ | turns focus mode on or off: it hides the sidebar and the side pane |
| ⌘⇧. | turns reading mode on or off in a chat: it folds the composer while you read |
| ⌘⌥A | goes to the oldest chat that needs you: approvals first, then the chats waiting on your reply, in the order Home lists them |
| ⌘N | starts a new chat |
| ⌘1 to ⌘9 | opens the first nine chats in the chat list, pinned first, then the most recent |
| ↑ in an empty composer | puts your last prompt back |

In a browser tab, ⌘N and ⌘1 to ⌘9 belong to the browser, so the palette is the way there. The
desktop app binds them in its Go menu, which acts on the tab in view. A text editor's own keys, such
as ⌘Enter to send in a room or ⌘S to save, work where you type and aren't listed here.

## Approving with the keyboard

⌘Enter approves the request that has keyboard focus. Tab to the approval card (or to its Allow
button) and press it.

- **Only the card in focus.** The key does nothing while focus is anywhere else: in the composer or
  any other text field, in a dialog opened over the card, or on another card.
- **Only a request that is still waiting.** A request that was answered elsewhere, ran out of time
  or was stopped leaves the page, and a card whose answer is already on its way takes no second
  press. A hidden card answers nothing.
- **One request per decision.** Holding the key down, or pressing it again while the answer is on
  its way or once it has landed, sends nothing more, and that holds for Deny too. If the request
  fails, the card stays and you can answer it again.
- **Nothing decides by itself.** A card never takes focus when it arrives, Enter on the card itself
  decides nothing, and Escape only closes what is open. Enter or Space on a focused button presses
  that button, as a click does; ⌘Enter is the only key that approves from anywhere else in the
  card. Decline is the Deny button, with no key of its own.

### The second confirmation

Some calls ask once more before they run, because what they do is hard to take back. The card says
what the call would do and offers **Yes, run it**. Nothing is sent before you press it: pressing
Allow again, with a double-click or a held Enter, only asks again. Escape steps back without
deciding.

- a delete, or a shell command PersonalClaw could not check, since it could delete;
- a change to files outside the chat's folder;
- something sent to people other than you;
- spending money;
- a call to a tool that doesn't say what it does: an MCP server's tool, or an app's tool that
  declares nothing or declares something PersonalClaw can't read. A call that only reads asks
  nothing more. A question an MCP server puts to you, to confirm what it is about to do, asks once
  more too: nothing it declares says what your yes lets it do.

Every other approval decides on the first ⌘Enter or click. Which calls ask is worked out once, when
the approval is raised, and every place you answer from asks for the same ones: the chat, Home, the
Inbox and the phone. A messaging channel's prompt answers on one press, so a call that asks once
more is never put to it: the channel tells you it is waiting, with a link to where you answer it.

A computer-use approval decides on one press. It relies on computer use's own arming step, which
you turn on before an agent can act on your desktop, not on the second confirmation.
