# Working inside a chat

Most of what PersonalClaw does happens in one long conversation, and a long conversation
needs more than a send button. This guide covers the nine things the chat surface can do
beyond typing a message: taking a wrong turn back, spinning the same conversation off in two
directions, having a plan approved before anything runs, letting a queued message cut in,
finding something you said hours ago, quoting it, following a suggestion, controlling how
text appears, and putting a piece of your screen into the conversation.

Everything here works in a browser tab. Where a mechanic needs a platform capability that
your browser does not have, the control is **hidden** rather than shown and made to fail —
so if you cannot find one below, that is the reason, and each section says which capability.

---

## 1. Rewind — go back to any earlier message

**Where:** hover any of your own messages that is *not* the last one → **Rewind to here**.

Editing your last message and sending it again has always worked. Rewind is for the message
*before* that: pick a turn from an hour ago, change what you asked, and the conversation
replays from that point. Everything after the message you edited comes off the visible
transcript, and the assistant's memory of it is dropped too, so it will not quietly refer to
an answer you just undid.

It asks first, because it changes the shape of the conversation rather than adding to it.

### Nothing is thrown away

The messages that came off are kept, attached to the message you edited. A divider appears at
the rewind point telling you how many turns are held there, and you can expand it to read
them. If you decide you preferred the old direction, **restore** it — which creates a **new
session** containing "everything up to the edit, plus the old ending", and leaves the chat you
are in exactly as it is.

Restoring forks rather than swaps on purpose. A conversation you can silently switch between
two versions of is a conversation you can no longer trust to be what you last read.

Five rewinds' worth of history are kept per message; a sixth pushes out the oldest.

> The retained history is stored on the message itself. It is a 0.x state-shape change, so
> if you want a restore point before updating, run `personalclaw snapshot`.

## 2. Branch — take the same conversation two ways

**Where:** hover any message, yours or the assistant's → **Branch from here**.

Branching copies the conversation up to that message into a **new** chat and takes you
there. The chat you came from is left exactly as it was, so both directions stay live.

That is the difference from rewind: rewind *replaces* an ending, and Branch *duplicates* a
whole conversation. It is what lets you spend hard-won context more than once — keep one long
thread as the record of a project, and branch off it each time you need something produced
from what it already knows.

Branching from an *answer* is the common case ("take this analysis two ways"), so it is
offered on the assistant's messages as well as your own. The same message branches as many
times as you like, and a branch of a branch is just another branch.

The new chat's header carries a **Branched from …** link back to where it came from. That is
read from stored lineage rather than remembered from the click, so it survives a reload;
renaming the original updates it, and if the original is deleted it says so instead of
linking nowhere.

There is no "are you sure?", on purpose. Branch only ever creates — it cannot overwrite
anything in the chat you are in. Rewind, which replaces, does ask.

**Temporary** and **incognito** chats do not offer it: there is no retained transcript to
copy. There is also a ceiling on how many chats can exist at once, and at the ceiling
branching refuses with that reason rather than failing quietly.

## 3. Plan it first — nothing runs until you approve

**Where:** the **+** menu in the composer → **Plan this first**, once a chat has started.

Nothing offers this to you and nothing decides you need it — a quick task stays quick. When
you do ask for it, the chat flips into the **Plan** task mode and your next message is
answered with a plan instead of with work.

"Nothing runs" is not a promise made to the model. Plan is one of the ordinary task modes,
and it is the tool gate that refuses a mutating tool while you are in it — the same gate the
task-mode pill uses, checked before any approval you might already have granted.

When that turn finishes, a review panel opens above the composer with the plan as Markdown
(until then it says *Drafting…*, because there is nothing to review yet):

- **Edit** rewrites it by hand. The plan you approve is the one that gets carried out, so the
  last word on it is yours.
- A comment box, plus **Send comment & redraft**, has it revised with your feedback instead.
  Comments stay attached to the plan across re-drafts.
- **Approve & run it** returns the chat to the task mode it was in before and carries the
  plan on *in the same conversation* — a continuation, not a fresh start.
- **Cancel plan mode** abandons the plan and hands the chat back.

While a plan is waiting for you, the task-mode pill **refuses** to leave Plan and says why:
Approve and Cancel are the two ways out. Without that, the guarantee would be one click from
decorative.

Ask for it while a turn is running and that run is **parked** — the queue is dropped and the
turn is asked to stop at its next safe point, while the transcript is left untouched. The
panel says the run is parked, and approving resumes it. Asking again later adds another step,
which is how re-planning halfway through a task works.

## 4. Let a queued message cut in

**Where:** the stacked cards below the composer while a turn is running.

Sending a message mid-turn queues it — the current answer finishes, then yours runs. Each
queued card has three controls: **Cancel** (drop it, text comes back to the composer),
**Edit** (same, but reopened for changes), and **Interrupt now**.

**Interrupt now** stops the running turn *cooperatively* and starts that queued message next.
It is not the Stop button: Stop cancels the turn and clears the queue, while Interrupt keeps
the queue, so the message you promoted runs immediately instead of being thrown away with the
turn. Use it when the answer has clearly gone the wrong way and you already know what you
want instead.

If there is nothing queued there is nothing to promote, and Interrupt is not offered — with an
empty queue it would just be Stop under another name.

## 5. Find in the conversation

**Where:** `⌘F` (`Ctrl+F` on Windows/Linux) with a chat open.

A compact bar docks under the chat header. Type, and every match in the conversation
highlights in place — the count reads `3/17`, `Enter` or `↓` moves to the next, `Shift+Enter`
or `↑` to the previous, and the matching turn scrolls into view. `Esc` closes it, and so does
a second `⌘F`, which hands the shortcut back to the browser's own find if that is what you
wanted.

Every control is reachable by keyboard: `Tab` walks the field, previous, next and close, and
`Esc` closes from any of them. Closing puts your focus back where it was, so you carry on
typing rather than restarting at the top of the page. A screen reader hears the position in
words — "Match 3 of 17", or "No matches" — rather than the digits on screen.

The search is over what is *rendered*: message text and tool-card titles. Collapsed tool
output is not searched. It never reformats your messages to highlight them — code blocks, and
a reply still streaming in, stay exactly as they were.

This searches the conversation you are in. To search *across* conversations, use **Search chats**
in the sessions list, which looks at titles and everything said in every session.

On a phone-width screen the bar spans the column instead of sitting as a pill in the corner, so
it shrinks with the page rather than hanging off the edge of a narrow one.

## 6. Quote a passage back

**Where:** select any text in the transcript.

A small **Quote** button appears over the selection. It inserts the passage into the composer
as a `>` quote, attributed to whoever said it, with your cursor after it ready to type. There
is a **Copy** beside it for when you want the text somewhere else entirely.

This is worth using whenever the conversation is long: quoting the exact paragraph you mean
is shorter than describing which paragraph you mean, and it removes the guess.

## 7. Follow-up suggestions

**Where:** under the last reply, a second or two after it finishes.

Two or three suggested next messages appear as chips. **Click** one to put it in the composer
so you can edit it first; the small **send glyph** on the chip (or a double-click) sends it as
it is. They disappear the moment you do anything else — start typing, send something, switch
session — so they never move the composer under you.

Each suggestion costs one small background model call per reply, using your fastest bound
model, and it never blocks the answer. Turn it off in **Settings → Chat → Follow-up
suggestions**; with it off, nothing is generated at all rather than generated and hidden.

They are skipped in **temporary** and **incognito** chats, which exist not to leave traces,
and they stay silent if you have no model bound.

## 8. How streaming text appears

**Where:** **Settings → Chat → Streaming text reveal**.

- **Smooth** (default) reveals whole words at a steady pace, decoupled from however the
  network happens to chunk the reply. Text arriving in one large burst still catches up within
  a few frames — the pacing never lets the display fall behind the answer.
- **Immediate** paints each chunk the instant it arrives.

If your system is set to reduce motion, or you have turned animation off in PersonalClaw, the
reveal is immediate regardless of this setting — the preference cannot re-enable motion you
asked the machine not to show you.

## 9. Put part of your screen into the conversation

**Where:** the **+** menu in the composer → **Capture screen area**.

Snip a region of your screen and it arrives as an ordinary attachment on your next message —
same chip, same removal, same text extraction as a file you dragged in.

There are two ways it can happen, and PersonalClaw picks for you:

- **On macOS**, the gateway's host uses the system snip: a crosshair, drag, done. No browser
  picker, no whole-screen share to approve.
- **Everywhere else**, the browser asks which surface to share, PersonalClaw grabs **one
  frame** and stops the capture immediately — nothing keeps recording — and then you drag a
  crop box on that frozen frame. `Esc` cancels and attaches nothing. If the macOS path fails
  (no display server, permission refused), this is the fallback.

### When the menu entry is not there

The entry is hidden when neither path is available — the browser has no screen-capture API
*and* the host is not macOS. **iOS Safari implements no screen capture at all**, so on an
iPhone or iPad talking to a non-macOS PersonalClaw there is no snip, by design: a control that
can only fail is worse than no control.

The one case worth knowing: from a phone browser pointed at a **macOS** PersonalClaw, the
entry is still there and still works — but the crosshair appears on the *Mac*, because that is
where the snip happens. Capture from the phone's own screen belongs to the mobile companion,
not here.

The captured region is saved into your uploads directory like any attachment and recorded in
the security log as the file write that it is — there is no separate "screenshot" event, and
no ongoing capture to audit, because the capture ends before the crop overlay even opens.

---

## What gets recorded

Six of these nine change something a security log should be able to show you. Each *action*
leaves exactly one entry in **Settings → Audit log** — where a mechanic has two distinct
actions, it has two rows:

| Action | Logged as |
|---|---|
| Rewind a conversation | `chat.rewind` |
| Restore a rewound ending as a new session | `chat.fork_rewound` |
| Branch a conversation into a new chat | `chat.session_fork` |
| Turn on plan mode | `chat.plan_activate` |
| Approve a plan step | `chat.plan_approve` |
| Interrupt now | `dashboard_interrupt` |
| A screen snip's attachment | `upload.file` (the same entry any upload gets) |

Generating follow-up suggestions is recorded too (`chat_followups`), because it spends a model
call you did not explicitly ask for.

A branch that is *refused* — a temporary chat, a chat an app does not own, or the ceiling on
how many chats may exist — is recorded as well, with the reason. A refusal is a decision, not
a silence.

Plan mode records the two transitions that open and close the gate: turning it on, and
approving a step. Editing or commenting on a draft records nothing, because both only change
text that is still waiting for you. Cancelling records nothing either; it returns the chat to
the task mode it was in before the gate opened.

The other three — find, quote, and the streaming reveal — record nothing, and that is correct
rather than missing: none of them leaves your browser. Find scans the conversation already
loaded in the page, quoting writes into your own composer, and the reveal setting only paces
text the reply had already sent.
