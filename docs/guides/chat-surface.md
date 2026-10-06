# Working inside a chat

Most of what PersonalClaw does happens in one long conversation, and a long conversation
needs more than a send button. This guide covers the nine things the chat surface can do
beyond typing a message: taking a wrong turn back, spinning the same conversation off in two
directions, having a plan approved before anything runs, letting a queued message cut in,
finding something you said hours ago, quoting it, following a suggestion, controlling how
text appears, and putting a piece of your screen into the conversation. It ends with which
model answers a turn, and what gets recorded.

Everything here works in a browser tab. Where a mechanic needs a platform capability that
your browser does not have, the control is **hidden** rather than shown and made to fail —
so if you cannot find one below, that is the reason, and each section says which capability.

---

## 1. Rewind — go back to any earlier message

**Where:** hover any of your own messages that is *not* the last one → **Edit & resend** to
change what you asked, or **Rewind to here** to ask it again unchanged.

Editing your last message and sending it again only replaces that message's reply. An
*earlier* message is different: pick a turn from an hour ago, and the assistant answers it
afresh from that point. The messages after it are not re-sent — everything below the message
comes off the visible transcript, and the assistant's memory of it is dropped too, so it will
not quietly refer to an answer you just undid.

You are told before it happens, because it changes the shape of the conversation rather than
adding to it: the editor says that resending replaces everything below the message (its button
reads **Resend & replace**), and Rewind asks you to confirm.

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

### Putting files back: `/rewind-to-turn N`

Rewinding the conversation leaves your files as they are. To put back files the agent edited,
type `/rewind-to-turn N`: it previews what it would do to each file after turn N, and
`/rewind-to-turn N --confirm` does it. The conversation is not rewound.

A file comes back when it was backed up before it changed, which happens for two kinds of edit: one
the agent makes with its own file tools, and one an agent CLI such as Claude Code or Codex asks
PersonalClaw to allow, backed up the moment it is allowed. It comes back with the permissions it
had then, so a script stays runnable and a private file stays private. Nothing can back up a file
before a shell command changes it, or before an agent CLI edits it without asking, because nobody
knows beforehand which file that is. The preview lists each such file in the chat's folder,
changed, created or deleted after turn N, and the rewind leaves it as it is. It leaves out the
folder's version-control, dependency, build and cache folders (`.git`, `node_modules`, `.venv`,
`dist`, `build`, …) and long-term memory, which keeps a history of its own, and it says when a
folder of more than 20,000 files was too big to list in full. A file that a command changed before
the agent edited it comes back only as far as the agent's backup, and the preview says so.
Credential files (`.env`, keys) are never copied, so they never come back either. **Settings → Chat
→ File checkpoints** holds how much is kept.

### Retry, when a turn ended without its answer

A turn that ends in an error, is cut short, or is cut off by a restart says so where its answer
should be, with **Retry**. Retry runs the turn again from its message and replaces the attempt
on screen.

### Running a turn again asks first when it may repeat a step

**Retry**, **Regenerate** on an answer, **Rewind to here**, and **Edit & resend** with your
message unchanged each run a turn again, and the new attempt is not shown what the old one did.
When that attempt finished steps that may have changed something (a file written, a command run,
a message sent, a record created), each asks first: it lists those steps as their cards name
them, and runs the turn again only when you choose **Run it again**, because the new attempt may
make them again. A turn whose finished steps only read, or that finished none, runs again without
asking. Rewind asks only about the turn it runs again: the later turns it replaces answered other
messages. A message you edit is a new message, so it is sent without this question. A step counts
as a read only when its tool declares that it only reads, or its shell command is one PersonalClaw
reads as read-only; any other step, one whose tool declares nothing included, is asked about.

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

**Temporary** and **incognito** chats do not offer it. There is also a ceiling on how many
chats can exist at once, and at the ceiling branching refuses with that reason rather than
failing quietly.

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

While a turn runs, the composer's button says what a message you type will do. **Steer** sends
it into the running answer, which reads it at its next step. Until then a line above the
composer says it is on its way; once the answer takes it, it is your message in the chat, where
it went in, marked *Steered into the answer*, with the answer going on below it. It stays there
after a reload, and what PersonalClaw learns from your chat reads it as yours. A steer the answer
ends without taking runs next, from the queue, and still shows once. **Queue** runs it after the
answer finishes. Steer is offered only while the running turn can take a message in:
PersonalClaw's own agent can, and an agent CLI that cannot be handed a message mid-turn is
offered Queue.

A queued message waits in the stacked cards — the current answer finishes, then yours runs.
Each queued card has three controls: **Cancel** (drop it), **Edit** (take it back into the
composer to change it, with what you pasted into it, after anything you are writing there), and
**Interrupt now**.

**Interrupt now** stops the running turn *cooperatively* and starts that queued message next.
It is not the Stop button: Stop cancels the turn and clears the queue, while Interrupt keeps
the queue, so the message you promoted runs immediately instead of being thrown away with the
turn. Use it when the answer has clearly gone the wrong way and you already know what you
want instead.

If there is nothing queued there is nothing to promote, and Interrupt is not offered — with an
empty queue it would just be Stop under another name.

However a turn is stopped (Stop, Interrupt now, or a change of agent below), what that turn
started stops with it: a batch of tasks it handed to `subagent_run`, a background subagent it
started, and the approvals they were waiting on. Each says its chat turn was stopped. What an
earlier turn of the chat started goes on.

A process a command leaves running in the background is different: it lasts as long as its
turn, so it ends when the turn ends, stopped or not, even a server that detached itself from the
command. A loop's lasts as long as the loop, and an agent CLI's as long as the CLI's process for
that chat.

### Changing the agent while it answers

Picking another agent, agent CLI, model or reasoning effort while a turn runs — in the
composer, with **Route** on the routing chip, or through the API — applies to the message
being answered. The running turn stops, a permission card it was waiting on ends as not run,
and your message is answered again by what you picked. The chat says so where the
conversation is: "Moved to oncall-triage — it is answering your message." If the answer had
already arrived, it stands, and the change applies from your next message. If the turn had
already finished a step that may have changed something, your message is not sent to what you
picked on its own, since that could repeat the step: the chat says so, with **Retry**, which asks
first.

### Subagents the agent starts

When the agent hands work to subagents with `subagent_run`, you are asked once before anything
starts: about the one subagent, or, for a batch of tasks, one ask naming every task and what each
may change. A batch whose tasks only read starts without asking where the chat's own subagents
would, on its Trust or YOLO; one whose tasks may change things always asks, and only Allow or Deny
answers it. The chat shows one card per batch: while it waits, it says so and links to the Inbox;
once it starts, it follows the run and names a step that waits and what for. Its tasks are listed
under Subagents in the chat's Activity panel by their step names. An ask still waiting when
PersonalClaw restarts is asked again, or, if its window passed meanwhile, the chat is told the
batch never started.

A subagent that has not started when PersonalClaw restarts, because it waits for a free slot (Max
subagents in Settings → Agent defaults) or for your Allow, is kept. After the restart it waits for
its slot again, in the order the agent asked for it, or asks you again with the same card: your
approvals are read as they are then, as for a new subagent, so the restart starts nothing you had
not allowed. One that can no longer run is ended, and told why where it reports, as any
subagent's ending is: the agent it was to run on is gone, or the app it was for no longer runs agent
work (an app's subagent also starts no wider than the app's tier is now). One whose chat was
deleted, or whose asking turn ended with the restart (a subagent's own subagent, a workflow
step's), is ended too, with nobody but you to tell. The notice after the restart names each one,
beside the subagents the restart stopped while they ran, and one you cancelled stays cancelled. The
restart's confirm counts only the subagents a restart cuts off: one waiting for a slot loses nothing
to it.

When a subagent finishes, its report comes back to the chat: whole, or, when it is long, shown to
the agent in part with a way to read the rest. The whole report is kept in the chat, where the
agent's `subagent_status` reads it again later, a restart included, until the chat is deleted or
goes a week unused. A report is the subagent's words, not yours, so the agent reads it as text from
outside: one the injection screen refuses never reaches the agent, which is told it was withheld
and why, as your note about the run is.

Each chat's agent sees only that chat's subagents. `subagent_list` lists the ones the chat started,
a batch's tasks and the subagents a subagent starts included, and `subagent_status` reads only
theirs, before and after a restart. Another chat's subagent, a Temporary or an Incognito chat's
included, reads as not found, as an id that never existed does, and so does a subagent of a turn
someone else started in a shared channel thread, to every chat but that thread. You see every
chat's subagents on the Background agents page.

What a chat's work waits on in your Inbox is that chat's too: the approval its agent or one of its
subagents asks for, a batch's ask (which names each task), a question its agent put to you, the
note a call of its left when nobody could answer, and what one of its own runs waits on. When an
agent reads your Inbox (`inbox_list`, for a briefing), it reads its own chat's items and what is
about no chat: your mail and channel messages, proposals, notices, and what a run of yours waits
on. Another chat's items, a Temporary or an Incognito chat's included, are not in what it reads or
in its count. A scheduled briefing and the Morning triage digest are no chat's work, so they read
none of what your chats' work waits on. You see every item on the Inbox page.

A batch saves no workflow. It runs as one workflow run, which holds its tasks, and is in none of
your workflow definitions: no agent lists it, reads it, starts it again or deletes it by name. Its
run is the chat's own, and so is every workflow run a Temporary or Incognito chat starts: only that
chat's agent reads it (`workflow_status`, `workflow_output`, `workflow_observe`), works on it or is
told it is running, and to any other chat's agent it reads as not found, as an id that never
existed does. You see every chat's on the **Runs** tab of the Workflows page, each marked as its
chat's: *Temporary chat's batch*, *Incognito chat's run*, *A chat's batch*.

### What a private chat leaves behind

In a **Temporary** or **Incognito** chat the agent sets up no work that lasts after the chat: it
creates, starts or steers no loop or project, creates or changes no automation or scheduled task,
and registers no callback. Each of those is kept after the chat and works on by itself, on a model
of its own, so what you said there would leave the chat. The agent tells you so, and you can set it
up from an ordinary chat, or on the Loops or Triggers page. A workflow the agent starts from such a
chat keeps the chat's mode and runs on its model.

Nor does it save anything that other chats read later. It keeps no skill you teach it (there is
no skill to review at the chat's end), files no proposal for review, creates or changes no task,
task list or project on the Tasks page, posts nothing to the Inbox, and changes no loop's task or
plan. The agent says so instead; you can do any of it from an ordinary chat, or on the Skills,
Tasks or Loops page. It still reads your tasks, skills and loops as before. A workflow it starts
puts none of its steps on the Tasks page, and a run one of its steps starts keeps what that run
keeps.

A **Temporary** chat's workflow runs end with it: once the chat has ended, a run still working is
stopped, and the run, any run it started, and what they produced are deleted. So does a batch of
its still waiting for your Allow: its ask leaves your Inbox. An **Incognito** chat's runs are kept,
as its transcript is, until you delete the chat: then they go the same way.

### Deleting a chat

**Delete chat** removes the conversation for good, and with it what memory drew from that chat
alone: its summary, its episodes, and the facts and lessons that came only from it. Other chats
stop recalling them, and the daily history and the day's digest stop repeating them. If the chat
had replaced something memory held from earlier, the earlier version comes back.

What also came from elsewhere stays: a lesson you taught in another chat too, a fact you edited in
**Settings → Memory**, and what PersonalClaw drew from many chats together. So do the files you
attached to an ordinary chat (Files lists them), and the Knowledge entries, artifacts and skills you
kept. You can remove any of those where it is listed: a lesson or a fact in **Settings → Memory**.

Each deletion leaves one entry in **Settings → Audit log** (`chat.deleted`), naming the chat and
never what it held. If the chat's history cannot be removed from disk, the page says so and the
chat stays in the list; its entry then says `failure`.

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
in the sessions list, which looks at titles and everything said in every session, or press `⌘K`
(`Ctrl+K`) anywhere. Past two characters the palette also searches inside your chats, memory,
knowledge and tasks, grouped under those headings below the pages and actions. A chat opens with
this find bar already holding what you typed. A source that could not be searched says so in the
palette instead of showing nothing.

Your agent can search your earlier chats too, with its `chat_search` tool: ask it what you
discussed or decided before, and a handoff, a standup or a weekly review looks there as well as in
your notes. It gets each matching chat's title, when it started and was last active, the turns that
say it and where to open it. It never searches the chat you are in, an **Incognito** chat or a
**Temporary** chat, and in a Temporary chat it searches nothing at all.

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
so you can edit it first; the small **send glyph** on the chip sends it as it is. They disappear
the moment you do anything else — start typing, send something, switch session — so they never
move the composer under you.

A chip is sent as your own words, so it never says anything for you that the conversation
doesn't. When the reply asks you for something (a time, a detail, a choice), the chips leave the
answer to you and suggest what you might ask or do instead. A chip the model wrote with a time,
number, name, path or file that neither your message nor the reply gives is not shown. The
suggestions on a new chat follow the same rule, checked against what they were written from, and
so does **Optimize**: a rewrite that adds a detail you left open is not used, and the composer
says what it added.

Each suggestion costs one small background model call per reply, using your fastest bound
model, and it never blocks the answer. Turn it off in **Settings → Chat → Follow-up
suggestions**; with it off, nothing is generated at all rather than generated and hidden.

They are skipped in **temporary** and **incognito** chats. They are written by a model, never by
an agent CLI, even when your chats run on one. With no model chosen for them (nothing in
**Settings → Models**, and no provider with a default model), nothing is asked, and the place
under the reply says *"Follow-ups need a model: choose one in Settings → Models."*, which takes
you there. The suggestions on a new chat and on the dashboard say the same, and so does an
untitled chat's header, under its name, for its title and tags.

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
same chip and same removal as a file you dragged in, and it reaches the model the way any
attached image does (below). A screenshot already on your clipboard needs no menu: paste it
into the composer (⌘V / Ctrl+V) and it attaches the same way. A paste that carries text as
well — cells copied from a spreadsheet bring a picture of themselves along — pastes the text.

A long text paste (four lines or more, or a few hundred characters) becomes a card above the
composer and a **[Paste #N]** marker where it landed, so your message stays readable while you
write it. However the message then goes (sent, steered into a running answer, queued, brought
back with ↑ or out of the queue, edited, rewound) the agent reads what you pasted in the marker's
place, and your message shows it as a chip you can open, after a reload too. **Copy** on your
message copies it with the pasted text.

A file still uploading shows its progress above the composer, and until it is in, Send is off
and says which file it is waiting for; Enter says the same. The message then goes with the
file. Cancel the upload to send without it, while it is still sending: once the whole file is
sent its row stops offering Cancel, and it is attached a moment later.

There are two ways it can happen, and PersonalClaw picks for you:

- **On macOS**, the gateway's host uses the system snip: a crosshair, drag, done. No browser
  picker, no whole-screen share to approve.
- **Everywhere else**, the browser asks which surface to share, PersonalClaw grabs **one
  frame** and stops the capture immediately — nothing keeps recording — and then you drag a
  crop box on that frozen frame. `Esc` cancels and attaches nothing. If the macOS path fails
  (no display server, permission refused), this is the fallback.

### How an attached file reaches the model

A document, a text file, or a code or config file (a script, `.json`, `.yaml`, `.toml`, …)
reaches the model as its text, marked as the file's content and never as instructions to it.
A binary file named as text or code is not read, and the model is told only its name and
size. The text is scanned first: text that fails the content safety scan is not given to the
model, and the model and the file's preview say so. Keys the file holds are masked, as the
Files view masks them. The model gets at most 200,000 characters of one file, and of a text
larger than 512 KB only its first 256 KB. Click the file's chip on your sent message to see
the text the model was given.

### How an attached image reaches the model

When the model answering the chat takes images, it is shown the image itself. Whether it does
is read from what the platform records: the provider's declaration that its connection carries
images (Anthropic, Bedrock, OpenAI-compatible and Ollama connections do) *and* the model's own
image-understanding capability as Settings → Models lists it. An image larger than 1568 px on
its long edge is scaled down first.

When it does not — a text-only model, or an agent CLI such as Claude Code, which runs its own
connection — the image goes as text: what OCR and your image-understanding model read from it.
That model is the one chosen for **Image · Modality** in Settings → Models; with none chosen,
your chat model reads images itself when it takes them. The chip says **as text** before you send, with the
reason ("gemma3:1b can't take images.") and what the model will get instead. If nothing is set
up to read an image, it gets only the image's size and format, and the chip says **No image
model is set up** with a link to Settings → Models. After sending, the chip on your message
keeps saying **sent as text**, and its preview shows exactly the text that was sent.

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

## Which model answers a turn

A turn runs on the model you pick in the composer's model pill. Left on **Auto**, it runs on a
chain from **Settings → Models**, chosen by the folder the chat works in:

- **Code & tools**, for a chat working in a folder of its own: the working directory you set for
  it (the chat header's **Working directory**), its project's folder, or its agent's. While Code &
  tools has no model bound, these turns use your Chat chain.
- **Chat**, for every other chat: one in the workspace every chat starts in.

The folder decides before the turn starts, never what you ask in it. So with Code & tools on one
provider (your work account, say) and Chat on another (a plan for your own chats), a chat working
in your repository sends its turns to the first and an everyday chat to the second. Set the
working directory before the work starts: a chat with no folder of its own runs on Chat even when
you ask it to run a command in a repository. Changing it moves the chat's next turn. A side
question asked beside the chat runs on the same chain as the chat. The chat's subagents use
Orchestration, and its title, tags and follow-ups use Background, as Settings → Models says for
each.

Each turn names the model that answered it. Its details chip says **on <model>**, and opened,
**Answered by <model>, from your Code & tools chain** (or your Chat chain, or **picked for this
chat**). On Auto the pill says the same, **Auto · <model>**, and its Auto row names the chain the
next turn takes. A chat on an agent CLI runs on that CLI's own model.

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

A Retry is recorded as `chat.retry_failed_turn`, a Regenerate as `chat.regenerate`, and a
message you resend as `chat.edit_resend` (a rewind is `chat.rewind`, above). When one of them asks
first, the question is recorded (`needs_confirm`, naming the steps it may repeat), and so is your
**Run it again** (`allowed`, with the steps you confirmed).

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
