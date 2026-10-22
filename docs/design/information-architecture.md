# Information architecture and screen layouts

Where everything in the dashboard goes: the sidebar, each screen's layout, and the home of every
indicator. [`web/PRODUCT.md`](../../web/PRODUCT.md) and [`web/DESIGN.md`](../../web/DESIGN.md) win
every conflict. The rules this applies (decluttering, placement, keys, definition of done) are in
the redesign brief, [`web/REDESIGN.md`](../../web/REDESIGN.md).

## Product type

PersonalClaw is a delegation workbench for one technical owner. Agents do most of the work, often
while the owner is away, so the screen helps them delegate, supervise and review, in that order.
Configuration is a rare visit. The owner keeps the app open all day beside an editor and a
terminal, so it is dense, keyboard-first, and its chrome can be hidden. Nothing on it teaches the
interface after the first run.

How often the owner needs a thing decides how much room it gets:

- Every session: threads, review, approvals, running work. They get the sidebar and the work
  surface.
- Weekly: knowledge, memory, skills, automations. One click away.
- Rarely: providers, models, tool servers, agent definitions, prompts, the store, security. They
  live in Capabilities, Settings and the palette.

## The sidebar, top to bottom

The sidebar is a hierarchy, not a count. Projects and threads dominate; the work destinations come
next; the utilities sit at the bottom.

1. **Home:** Needs You, Running Now and Recent/Completed (#3813).
2. **New thread**, and search.
3. **Projects with their threads** (#3814). Chats, goal loops and code sessions are all threads: a
   thread's type changes its layout, never its address. Pinned projects come first. Rows are about
   32 px high, and each carries a static pip (running, waiting on you, failed) that reads the same
   rule as Home's groups. Within a project, pinned threads come first, then the most recently
   active; a status change never moves a row. Nothing in the sidebar animates. A project's own page
   is its thread list beside its detail: the same threads, in the same order, with the same marks.
4. **The work destinations:** Inbox (#3818), Library (#3815), Automations (#3816) and Capabilities
   (#3817).
5. **The utilities**, pinned at the bottom: Settings, which gains System, and diagnostics.

Every destination is reachable five ways, and primary navigation stays reachable at every
supported width, 320 to 1440 px (#3812):

1. The expanded rail, the desktop default: every entry with its label, and the projects with their
   threads.
2. The collapsed rail: the destinations as icons, each named in its tooltip. A thread is reached
   from Home, from the palette, or by ⌘1 to ⌘9 for the first nine.
3. The compact window: the same rail as a drawer the toolbar opens. It is never removed.
4. The command palette: "Go to" for every destination, tab and project, each with its shortcut
   shown.
5. The keyboard: the keys in the brief's
   [keyboard model](../../web/REDESIGN.md#6-keyboard-model).

[The feature map](../feature-map.md) lists every page the router serves with each way to it: its
place in the sidebar, the palette rows that open it and the pages that link to it, and the pages
only a typed address opens. It is generated (`npm run docs:feature-map`), and a test fails when it
and the app differ.

The rail toggles from the toolbar, ⌘B and the palette, and comes back as it was: its open state,
its width, each project's disclosure and how far it was scrolled are kept on the device, and the
thread it lights is the address's. Opening it puts focus on the row that last had it, else the lit
thread; closing it puts focus back on the toggle. Hiding it changes chrome only: the address, the
selected thread and running work are untouched.

## Where the rest goes

- **Terminal** is the bottom drawer (⌘J or ⌘\`) on every page. "Open full" keeps a full-height
  terminal one click away: the Terminal page, on the session the drawer shows (#3826).
- **The workspace file tree** is a toggle inside a thread (⌘⇧E), rooted at the thread's working
  folder. Stored files are Library's Files tab (#3826, #3815).
- **Tasks** are a board or list view inside each project, with "All tasks" in the palette (#3818).
- **Notifications** are a filter of the Inbox. The bell opens the Inbox on it (#3818).
- **Discover** lives in empty states, first run and the palette (#3818).
- **Plan and diff review** is a pane inside the thread, not a page (#3821).
- **Mission Control** is Home. `#/mission-control` opens it (#3813).

**Old addresses keep working.** Every route the app has shipped resolves, as a redirect to its new
home or tab. The redirect replaces the history entry, so Back never bounces through the old
address, and query parameters and sub-paths carry over, so a deep link lands on the same item.
Each retired path, with the query keys it carries, is one row of the redirect table,
`web/src/app/routeRedirects.ts`: the old path, the new address, the segments and query keys the new
page reads, and the issue or pull request that retired it. A retired query value, such as an old
Settings → Memory tab, is not a row: the page that still accepts it maps it. The router rewrites an old
address before any page draws it, and no row lands on another row's old address, so a redirect
never chains: when a page moves again, the rows that point at it move with it. A page sends its
own address elsewhere only for what it holds at that moment, such as a room link that names no
room, and never for a move. Every desktop page the router serves has a palette row. A moved route
keeps a compact path, a palette entry and a route alias, and the old shell's stored state still
opens it (#3818, #3819). Every route and substate round-trips through the URL, browser history and
`personalclaw://` links, and a link restores state without ever acting (#3827).

## Screen specs

| Screen | Primary job | Work surface | Header (at most 3) | Right pane |
|---|---|---|---|---|
| Home | see what needs you, what is running and what finished | Needs You (sorted by consequence, then age), Running Now (each row with its exact phase and last update), Recent/Completed. An approval is answered inline. | New thread, View | the selected item's detail; a compact window opens it in place |
| Thread | delegate and converse | the reading column, folded work lines, artifact cards, the composer | the title (renamed inline), Share, overflow | activity, artifacts and sources, closed by default |
| Goal loop | supervise an autonomous run | the run as a thread: the plan on top, cycles below, a progress strip above the composer | Pause or resume, Stop, overflow | plan review, cycle detail |
| Code session | review the agent's changes | the thread, with the review pane open on uncommitted changes | Commit or open PR, overflow | the diff and its inline comments |
| Library | find and curate what the agent knows or made | tabs for Knowledge, Memory, Files and Artifacts, each a fixed-width list beside a flexible detail; the graph is a view toggle | Add, View, overflow | the item's detail |
| Automations | trust what runs without you | one list, with each item's next run, last result and the thread it reports to | New, overflow | detail and run history |
| Capabilities | configure what the agent can do | tabs for Agents, Skills, Tools, Prompts, Apps and the Store, each a list beside a detail, and Learning, beside Skills, the page of what the agent learned and the proposals it links to | Add (Install on the Store tab) | detail and permissions |
| Settings | configure the system | a section list beside the form, search on top | none | none |

Automations, as built (#3816): the sidebar row's address, `#/automations`, is the one list, and
Workflows (definitions and the runs no automation started) is its second tab, at `#/workflows`.
`GET /api/triggers` states the list's order once (`src/personalclaw/triggers/listing.py`): Needs
you (a failed last run, a missed or interrupted run waiting for a decision, a change waiting for the
owner's Allow), oldest first; Next to run, soonest first; Not on a clock, by name; ties by id. Each
row carries its group, its place in the list and where it reports (to an app's read, a thread only
of a conversation that app started), and the page shows the groups
under their headings as served (`web/src/pages/automations/listGroups.ts`). The selected
automation is `?open=<id>` and the run its history has open `?run=<run id>`; the history is newest
first by each run's start, ties by run id. `#/triggers` and its sub-routes open the list as they
always did, inside Automations, so no address is retired.

A list beside a detail shows both from 840 px of content up: the room the page has beside the
sidebar, not the window, so a collapsed and an expanded sidebar at one window width each get the
layout their room allows. Below it they show one at a time, the detail in place of the list, and
Back returns to the list.

## Inside a thread

Top to bottom:

- **The header** holds the thread's own actions only: its title, then Share (Copy chat link and
  the "Continue on" hand-offs), the Session map and Activity, and the overflow (Save as starter,
  New chat). Configuration never appears here.
- **The reading column**, around 720 to 780 px whatever the window's width (`--reading-width`,
  768 px, in `web/src/design/tokens.css`), holds the turns. Code, tables, diffs and artifacts open
  beside it in an adjustable split that remembers its size; a compact window stacks them under it.
  A reply's code block, diff or table opens there from its own Open beside, in the chat's pane host
  (`web/src/ui/PaneHost.tsx`, `pane-host-chat-w`), and the address names it: `?beside=<block>` is
  the split, its absence is the thread read in its column alone
  (`web/src/pages/chat/beside/blockBeside.tsx`). A block's address is its kind and a fingerprint of
  its content (`web/src/ui/OpenBeside.tsx`), so a reply names it the same live and after a reload.
  The pane host stacks the block under the thread where its own box is narrower than the pane's
  smallest width beside the work's 12rem.
- **Each turn** opens with the agent orb and one status line at its head
  ([`agent-orb.md`](agent-orb.md)). Its tool calls are dense rows (an icon, the tool's own label, a
  status pip) that fold, once the turn ends, into one line: "Worked for 12s · 6 steps", with any
  failed or unasked steps counted on it, and no tool named. The time is the turn's own, from when
  the gateway started it to when it ended; after a reload, the time the gateway measured and stored
  with the turn's answer, and only for a turn stored without one, from the first thing the turn
  recorded to the last. Helpers the turn started sit inside its folded steps.
- **Changes and plans** show in their turn as compact cards, and open in place or in the review
  pane on demand.
- **Approval and question cards** sit where the agent asked. A card states the action and its
  consequence plainly, so the owner can answer from it alone.
- **Sources** are inline citations at the answer, with the full list in the right pane.
- **The composer** is the anchor: attachments, `@` mentions and `/` commands in it, and Brief the
  agent in its `+` menu; the one agent-and-model pill, the task mode and the reply style in its
  footer; and the Execution Boundary control beside them, which shows the permission mode, the
  working folder, the sandbox, the network reach and the memory mode, and changes the permission
  mode, the folder and a new chat's memory mode. While a turn runs, the send button is its Stop. In
  reading mode the composer folds away until the owner wants to type.
- **The right pane** (activity, artifacts, sources) is closed until the owner opens it.

## What leaves Home and the thread header

- **From Home:** the count pills (one needs-you badge in the toolbar replaces them, and it shows
  nothing at 0), the chip row that repeats the sidebar, the system telemetry (to Settings ›
  System), and suggestions outside Needs You or the empty state. #3803 makes the first cut before
  Home becomes the three groups.
- **From the thread header:** the task-mode and permission-mode pills (to the composer and the
  Execution Boundary control), the agent and model selectors (one composer pill), Brief the agent (a
  row in the composer's `+` menu), the working directory (the Execution Boundary control), Save as
  starter (the overflow) and New chat (⌘N, the sidebar and the overflow). Copy chat link and the
  hand-offs go under Share. Activity stays, as the right pane's toggle.

## Indicator homes

| Indicator | Home | Escalates to |
|---|---|---|
| Pending approval | the inline card where the agent asked; Home's Needs You; one toolbar badge | the tray and the buddy while the window is not focused |
| Working, this thread | the orb and its status line at the turn head | none |
| Working, another thread | a static pip on its sidebar row; Home's Running Now | none |
| A loop waiting for input | Needs You; its sidebar pip | the buddy while the owner is away |
| Permission mode | the Execution Boundary control beside the composer, wherever the agent can act (a new loop's Mode, beside the loop composer) | the approval card itself |
| Task mode (autonomy) | the composer's footer, beside the Execution Boundary control | the plan review, while a plan waits |
| Memory mode (temporary or incognito) | the Execution Boundary control, on the control itself, plus a subtle tint on the thread | none |
| Context, tokens and cost | collapsed: opened from the composer, or pinned to the status line by the owner | a composer warning near the limit; one ask, with both prices, before a mid-chat switch of a long chat to a model that may cost more |
| Working directory, sandbox and network reach | the Execution Boundary control | none |
| Sources and grounding | inline citations at the answer; the full list in the right pane | none |
| Gateway connection and sync | the status line, quiet while connected; in words, with when it was last reached, while not. Focus mode hides the line unless the connection is lost | a banner, once the connection has stayed lost for 2 s, and gone when it is back |
| Background helpers | inside the parent turn's folded steps; counted in Running Now | none |
| Blocked or failed actions (the egress guard, the scanner, tool errors) | inline at the action; their history in Settings › Security | the blocked state of the orb and the buddy |

## The four rules

1. Only approvals, questions and errors interrupt. Completions update a group and a pip (#4572
   makes this the default for notifications).
2. Wherever the agent can act, its permission mode is visible next to the input.
3. Configuration never appears in the sidebar or a thread header.
4. Background work always shows in one of Home's groups, so chat is never the only lens.
