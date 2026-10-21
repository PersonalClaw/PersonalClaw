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
   active; a status change never moves a row. Nothing in the sidebar animates.
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
page reads, and the pull request that moved it. A retired query value, such as an old Settings →
Memory tab, is not a row: the page that still accepts it maps it. The router rewrites an old
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
| Capabilities | configure what the agent can do | tabs for Agents, Skills, Tools, Prompts, Apps and the Store, each a list beside a detail | Add (Install on the Store tab) | detail and permissions |
| Settings | configure the system | a section list beside the form, search on top | none | none |

A list beside a detail shows both from the 840 px width class up. Below it they show one at a time,
the detail in place of the list, and Back returns to the list.

## Inside a thread

Top to bottom:

- **The header** holds the thread's own actions only: its title, Share and the overflow.
  Configuration never appears here.
- **The reading column**, around 720 to 780 px whatever the window's width (`--reading-width`,
  768 px, in `web/src/design/tokens.css`), holds the turns. Code, tables, diffs and artifacts open
  beside it in an adjustable split that remembers its size; a compact window stacks them under it.
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
- **The composer** is the anchor: attachments, `@` mentions and `/` commands in it; the one
  agent-and-model pill, the task mode and the reply style in its footer; and the Execution Boundary
  control beside it. While a turn runs, the send button is its Stop. In reading mode the composer
  folds away until the owner wants to type.
- **The right pane** (activity, artifacts, sources) is closed until the owner opens it.

## What leaves Home and the thread header

- **From Home:** the count pills (one needs-you badge in the toolbar replaces them, and it shows
  nothing at 0), the chip row that repeats the sidebar, the system telemetry (to Settings ›
  System), and suggestions outside Needs You or the empty state. #3803 makes the first cut before
  Home becomes the three groups.
- **From the thread header:** the task-mode and permission-mode pills (to the composer and the
  Execution Boundary control), the agent and model selectors (one composer pill), Brief the agent (a
  row in the composer's `+` menu), the working directory (the Execution Boundary control), Save as
  starter (the overflow), Activity (the right pane's toggle) and New chat (⌘N and the sidebar).

## Indicator homes

| Indicator | Home | Escalates to |
|---|---|---|
| Pending approval | the inline card where the agent asked; Home's Needs You; one toolbar badge | the tray and the buddy while the window is not focused |
| Working, this thread | the orb and its status line at the turn head | none |
| Working, another thread | a static pip on its sidebar row; Home's Running Now | none |
| A loop waiting for input | Needs You; its sidebar pip | the buddy while the owner is away |
| Permission mode and autonomy | the Execution Boundary control beside the composer, wherever the agent can act | the approval card itself |
| Memory mode (temporary or incognito) | the Execution Boundary control, plus a subtle tint on the thread | none |
| Context, tokens and cost | collapsed: opened from the composer, or pinned to the status line by the owner | a composer warning near the limit |
| Working directory and sandbox | the Execution Boundary control | none |
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
