# Redesign brief

The rules every UI change in the redesign
([#3800](https://github.com/PersonalClaw/PersonalClaw/issues/3800)) is checked against: what to
remove, where each element goes, which keys do what, and what "done" means for a UI PR. Each
redesign item links the section it implements.

Every convention here says what it is and why it helps, in PersonalClaw's own terms. None names or
imitates another product, and none brings in another product's assets, code or text.

## 1. Sources of truth

- [`web/PRODUCT.md`](PRODUCT.md) and [`web/DESIGN.md`](DESIGN.md) win every conflict, including one
  with this brief. To change the look, edit `web/DESIGN.md` first, then the code.
- [The product tenets](../docs/vision.md#product-tenets) decide a trade-off between two goods. The
  earlier tenet wins, and the PR that chose names the tenet by its number.
- [`docs/decisions/`](../docs/decisions/README.md) holds the settled product decisions, one file
  each.
- [`docs/design/patterns.md`](../docs/design/patterns.md) is the gallery of shared primitives.
- [`docs/design/motion.md`](../docs/design/motion.md) owns every spring and duration.
- [`docs/design/consistency-audit.json`](../docs/design/consistency-audit.json), written by
  `npm run audit:consistency`, ranks which files to fix first. Cite the inventory and the command,
  never a count copied from an issue, since the counts move.
- [`docs/design/information-architecture.md`](../docs/design/information-architecture.md) owns
  navigation, screen layouts and where each indicator lives.
- [`docs/design/agent-orb.md`](../docs/design/agent-orb.md) owns the agent orb.
- [`docs/design/baseline.md`](../docs/design/baseline.md) is every screen's measured "before": its
  snapshots, figures, density analysis, critique and checklist answers. A redesign PR quotes its
  screen's section from it.
- [`docs/design/buddy/`](../docs/design/buddy/README.md) owns the desktop buddy.

## 2. Review skills, and on what

Each skill below is a dependency of the redesign. Each is pinned to a commit, and its licence was
read at that commit, through the GitHub API, on 2026-10-18. Raising a pin is a change to this
table: the new commit, and the licence read at it.

| Skill | Source and commit | Licence at that commit | Install | Used on |
|---|---|---|---|---|
| Impeccable: one skill with 24 commands, 59 deterministic detector rules and a design hook. It reads `web/PRODUCT.md` and `web/DESIGN.md` as product truth. | `github.com/pbakaus/impeccable`, release 4.6.0, commit `8a0e198da34ebc5b936a9d5975fb7a3b1cf353b7` (2026-10-15) | Apache-2.0 (`LICENSE`) | `npx impeccable@4.6.0 install`, and `npx impeccable@4.6.0 update` to refresh. Already wired here: `web/.impeccable/`, and the type ramp mirrored in `web/DESIGN.md`'s front matter. | `critique` before and `polish` after each surface; `distill` and `quieter` on clutter; `layout` for spacing rhythm; `npx impeccable@4.6.0 detect web/src` for §8 |
| web-design-guidelines: reviews files against the Web Interface Guidelines (104 rules at the rules commit: accessibility, focus, forms, animation, performance and more) and reports each finding as `file:line` | `github.com/vercel-labs/agent-skills`, `skills/web-design-guidelines`, commit `063bee94c3f4df8453406c830b0a7df0f2860278` (2026-08-28). The rules are not in the skill: it fetches `command.md` from `github.com/vercel-labs/web-interface-guidelines` each time it runs. They are pinned on their own: commit `434b7f91364665f2f733b310ec54809bf8f37937` (2026-10-05). | The skill: MIT, stated in its repository's README; the repository has no licence file at that commit. The rules: MIT (`LICENSE`). | `npx skills@1.7.2 add vercel-labs/agent-skills#063bee94c3f4df8453406c830b0a7df0f2860278 --skill web-design-guidelines -g`. A review reads the rules from `https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/434b7f91364665f2f733b310ec54809bf8f37937/command.md`, not from the copy the skill would fetch. | every changed file, before a PR; the gate on every primitive-consolidation PR |
| `emil-design-eng` and `review-animations`: rules for easing, duration, origin and how often a motion repeats, and a strict review of motion code. The set also has `improve-animations`, `animation-vocabulary` and `apple-design`. | `github.com/emilkowalski/skills`, commit `2d228630ba1446365f614a108cb8252f1feda202` (2026-10-11) | MIT (`LICENSE`) | `npx skills@1.7.2 add emilkowalski/skills#2d228630ba1446365f614a108cb8252f1feda202 --skill emil-design-eng --skill review-animations -g` | any motion change; auditing the springs in `docs/design/motion.md`; stripping motion from high-frequency actions such as sidebar clicks and message sends |
| ui-density: a value-density analysis in four dimensions (visual, information, design, temporal) with a fixed output | Smithery lists it at `smithery.ai/skills/rohunvora/ui-density` with no version or commit, so the listing cannot be pinned. It names its source, `github.com/rohunvora/my-claude-skills`, `.claude/skills/ui-density`, which is pinned instead: commit `4a5b5523176423b2ed81f1ccdda2b5c3fdf10625` (2025-12-25). | None. The repository has no licence file at that commit, and the skill names none. It is installed per machine and never copied into this repository. | `npx skills@1.7.2 add rohunvora/my-claude-skills#4a5b5523176423b2ed81f1ccdda2b5c3fdf10625 --skill ui-density -g` | a density analysis of each screen before it is redesigned, attached to the PR |
| `baseline-ui`: an opinionated Tailwind baseline that quotes each violation, says why it matters and proposes a fix. The set also has `fixing-accessibility` and `fixing-motion-performance`. | `github.com/ibelick/ui-skills` (ui-skills.com), commit `747a47fdb6b405e5cbf268f7c410123930455157` (2026-10-17) | MIT (`LICENSE`) | `npx skills@1.7.2 add ibelick/ui-skills#747a47fdb6b405e5cbf268f7c410123930455157 --skill baseline-ui -g` | the consistency inventory's top 15 files, file by file |
| frontend-design: guidance for a distinct visual direction on new UI | `github.com/anthropics/skills`, `skills/frontend-design`, commit `dbd4588f9e1033efb41dad4bef2f7947c8993d44` (2026-10-09) | Apache-2.0 (`skills/frontend-design/LICENSE.txt`); the repository has no licence file at its root | `npx skills@1.7.2 add anthropics/skills#dbd4588f9e1033efb41dad4bef2f7947c8993d44 --skill frontend-design -g` | net-new surfaces only, such as the buddy's settings sheet |

The installer is `skills` 1.7.2 on npm: `github.com/vercel-labs/skills`, tag `v1.7.2`, commit
`671e8c320810d36fed80fac5f1a2c1bf7e82d812`, MIT. It takes a full commit after `#`, and `-g` puts a
skill in the user's own agent folders rather than in the repository.

**How the skills are held.** Each skill is installed per machine at the commit above. Nothing is
vendored into this repository in this change. A skill is instructions an agent follows, so it is
read at its pinned commit before its first use. The `skills` installer reports each install (the
repository and the skill, for a public repository), and Impeccable reports which design direction
won a round; `DO_NOT_TRACK=1` turns both off.

**Ours win.** Where a skill's advice disagrees with `web/PRODUCT.md`, `web/DESIGN.md` or this brief,
the finding is dropped. `baseline-ui`'s stack rules (Tailwind defaults, its class helper, its choice
of headless libraries) don't apply here: `web/src/ui/` owns the primitives. frontend-design picks no
new direction for a surface that `web/DESIGN.md` already covers.

## 3. Decluttering rules

1. Every element answers "what does the owner do with this right now?" If nothing, delete it or
   move it behind disclosure.
2. One navigation path per destination: no chip row that repeats the sidebar.
3. Hide counters that read 0. Badge only what waits on the owner.
4. At most 3 always-visible controls in a page header. The rest go in an overflow menu or the
   command palette.
5. One agent and model selector per surface, as a single pill in the composer. It carries the
   reasoning effort only for a model that declares effort levels.
6. System telemetry (CPU, memory, disk, network) lives in Settings › System, not on Home.
7. Coral only on the primary action, focus, selection and live agent activity, under ~10% of a
   screen. Everything else sits on neutral surfaces.
8. The animated backdrop is off by default and lives only in the Claw Arcade identity. Elsewhere:
   no ambient glow, particles or sheen, and the composer's focus is a plain 2px coral ring
   (`web/DESIGN.md` §7).
9. Raw `button`, `input`, `select` and `textarea` are replaced with `web/src/ui/` primitives. The
   shared `Field` (`web/src/ui/forms.tsx`) carries its error wiring before any form is restyled.
10. No new top-level sidebar entry without removing or merging one. A new place nests under the
    destination it belongs to.
11. One animated thinking indicator per turn: the agent orb, driven by the phase contract
    ([#3833](https://github.com/PersonalClaw/PersonalClaw/issues/3833)), not by status text. Waiting
    for approval is a still frame.
12. Nothing moves on a high-frequency action. Sidebar clicks, sends, count changes and tab switches
    are instant. Springs only where space changes, on the presets in `docs/design/motion.md`.
13. Work folds until asked. A turn's steps, a diff and a plan each show as one line or one card
    until the owner opens them.

## 4. Space and placement

**Regions and their jobs.** Anything that fits none of them belongs in the command palette, not in
new chrome.

| Region | Its job |
|---|---|
| Toolbar | back, breadcrumb, the palette, one needs-you badge. In the desktop app the window's own controls sit inside it, so there is no second title bar. |
| Sidebar | Home, then the projects and their threads, which dominate; then Inbox, Library, Automations and Capabilities; Settings and diagnostics pinned at the bottom |
| Page header | the title, at most 3 actions, an overflow menu |
| Work surface | the one thing the page is for. The only region that grows. |
| Composer | the next turn, and everything that decides what it may do (§5) |
| Right pane | the selection's detail. Closed by default. |
| Bottom drawer | the terminal |
| Status line | ambient state only: the connection, sync, and the context the owner pinned |

**Rules**

- The work surface gets at least ~75% of the window with the sidebar open, and ~90% in focus mode.
- Widths follow intent. Reading stays around 720 to 780 px. Code, tables, diffs, artifacts and
  review use an adjustable split that remembers its size. A compact window stacks them without
  clipping either.
- Place by scope: global things in global chrome, page things in the page header, object things on
  the object, item things on their row (and in its context menu and the palette).
- Use frequency decides space. Every session (threads, review, approvals, running work) gets the
  sidebar and the work surface. Weekly (knowledge, memory, skills, automations) is one click away.
  Rarely (providers, models, tool servers, agent definitions, prompts, the store, security) lives in
  Capabilities, Settings and the palette.
- Never hide primary navigation behind a menu on desktop. Everything that leaves the chrome stays
  one keystroke away in the palette, with its shortcut shown.
- Status stays at the periphery (the status line, sidebar pips) until it needs a decision, then
  moves to the centre.
- Focus mode (⌘\): one key hides the sidebar and the right pane. Selecting something that needs a
  pane opens only that pane, one at a time while the mode lasts. The same key, the palette or the
  sidebar's own control leaves it, and the sidebar and the panes it closed come back. It lasts
  across reloads, as the collapsed sidebar does. On a phone the sidebar is already a drawer, so
  focus mode only closes the pane. Nothing is hover-only: what a hover shows is also reachable by
  keyboard and by touch.
- Every route and substate round-trips through the URL and browser history.

**The per-screen checklist.** Every redesign PR answers it for its screen:

1. What is the one thing the owner came here to do, and is it the largest element?
2. Does every visible element pass "needed right now"?
3. Can anything move to the status line, the palette or an overflow menu?
4. Is each element in its scope's home (global chrome, page header, the object, its row)?
5. With all chrome hidden, can the owner still act by keyboard?
6. Does every hidden feature keep a visible path back (palette, overflow, Settings)?
7. Before and after: screenshots, and the work surface's share of the window.

## 5. Conventions to match

1. **One calm conversation column.** A thread reads in one centred column, around 720 to 780 px
   whatever the window's width. A line stays readable at any width, and the eye returns to the same
   place after every turn.
2. **Quiet chrome, neutral surfaces, one accent.** The sidebar, toolbar, headers and status line are
   neutral tones with no decoration. Coral means the agent is alive: it marks the agent at work, and
   besides that only the primary action, focus and selection. When colour means "alive" or "act
   here", one glance finds what is running and what needs the owner. Coral, the orb, the buddy and
   the status words read one set of brand and state tokens, so a scheme recolours them together.
3. **Dense tool rows that fold into one line.** Each tool call is one row: an icon, the tool's own
   label and a status pip (running, done, failed, needs you). When the turn ends its rows fold into
   one line, "Worked for 12s · 6 steps", which opens on a click or a key. "N failed" and "N ran
   without asking you" stay on the folded line. The answer is the first thing the owner reads,
   every step is one click from being checked, and nothing that went wrong folds out of sight.
4. **Diffs and plans open where they are, on demand.** A change or a plan shows in its turn as a
   compact card (what it touches, how much), and opens in place or in the review pane beside the
   thread. A comment on a line or a step goes to the agent as guidance for its next step. Review
   happens next to the work without a page change, and long content never pushes the conversation
   off the screen.
5. **Approval cards say what will happen.** An approval card states the action (the command, the
   file, the host) and its consequence (what it changes, how far it reaches), then its answers. It
   sits where the agent asked, and Home lists it too. The owner can decide from the card alone,
   without opening the run. A redesign changes the card's layout, never its words: security and
   consent copy changes only in a change that is about it ([`AGENTS.md`](../AGENTS.md)). The keys
   are in §6.
6. **The composer is the anchor.** Everything that decides the next turn sits in it or beside it:
   attachments and mentions, the one agent-and-model pill, the task mode, the reply style, and the
   Execution Boundary control, which shows the permission mode, the sandbox, the workspace, the
   network or host reach and the memory mode before anything runs. While a turn runs, the send
   button is its Stop. The owner sees what the next turn may do where they type it, and the thread
   header keeps only the thread's own actions.
7. **Slash commands and mentions.** `/` lists the composer's commands. `@` attaches a file or a
   knowledge item by name. The hands stay on the keyboard, and context is attached by name instead
   of by browsing for it.
8. **Keyboard first.** Every action has a key or a palette row, and the key is shown wherever the
   action appears. An owner who lives in the app all day moves faster by key, and a key shown beside
   its command teaches itself.
9. **Threads in one dense sidebar.** Projects list their threads in rows about 32 px high, each with
   a static status pip. Far more threads fit, and running, waiting and failed read at a glance with
   nothing moving.
10. **Tools inside the work.** The terminal is a drawer at the bottom of any page, and a thread's
    workspace files are a toggle beside its conversation. Stored files live in Library. The owner
    opens them without leaving the work they belong to.
11. **A reading mode.** While the owner reads long output, the composer folds away, and comes back
    on a key or when they start typing. The whole height goes to the text being read. It folds to
    one line over the end of the transcript, which says what it opens and shows the start of a
    draft, and it also comes back on that line or on focus, with everything it held.

## 6. Keyboard model

Keys are written for macOS. Elsewhere ⌘ is Ctrl and ⌥ is Alt.

| Keys | Does |
|---|---|
| ⌘Enter | approves the request that has keyboard focus |
| Esc | closes or cancels the current detail; it never decides an approval |
| ⌘⌥A | jumps to the next thread that needs the owner, in Home's Needs You order (consequence, then age) |
| ⇧Esc | clears the unread markers |
| ⌘K, ⌘⇧P | the command palette |
| ⌘B | the sidebar |
| ⌘J, ⌘\` | the bottom drawer |
| ⌘⇧E | the workspace file tree, inside a thread |
| ⌘1 to ⌘9 | the first nine threads |
| ⌘N | a new thread |
| ↑ in an empty composer | restores the last prompt |
| ⌘\ | focus mode: hides the sidebar and the right pane |
| ⌘⇧. | reading mode: folds a thread's composer |

Focus mode's and reading mode's keys were chosen against the keys browsers and the desktop app
already take: ⌘⇧F, the first proposal, is a browser's full screen on a Mac, and ⌘⇧\ a browser's tab
overview.

**Rules**

- Nothing is approved without an explicit key press or click.
- Decline is an explicit control, with no shortcut of its own.
- A delete, an overwrite outside the workspace, a send to other people and spending each ask a
  second confirmation before they run. So does a call to a tool that says nothing about what it does
  (an MCP server's, or an app's that declares nothing), unless the call is an established read.
  Every other approval decides on the first ⌘Enter or click. Which confirmation a call asks is worked
  out once, where the approval is recorded, and every surface reads it from the approval
  (`confirms`); a messaging channel, whose prompt answers on one press, is never asked such a call
  and links to where it is asked instead. A computer-use approval decides on one press: it relies on
  computer use's own arming step, not on the second confirmation.
- No approval key acts from a text field, on a stale request, on a hidden surface or on a card
  without focus. A held or repeated key sends one request.
- Arrival never moves focus onto an approval action.
- Shortcuts are shown beside their commands in the palette, menus and tooltips, not only on a help
  page.
- One keymap holds every binding (its keys per platform, its label, its scope). The global handler,
  the palette and the tooltips all read it.
- Where a browser keeps a key for itself (⌘N, ⌘1 to ⌘9), the desktop app binds it as a menu
  accelerator, and in a browser tab the palette is the path.
- Approval wording doesn't change with the keys. Shortcuts can't be remapped; the push-to-talk chord
  stays the one configurable chord.

**As built**

- The keymap is `web/src/app/keymap.ts`: one row per command (`id`, `label`, `keywords`, `scope`,
  `chords`, `disabledReason`). `global` rows run through its one handler, from anywhere; `focused`
  rows (⌘Enter on an approval, ↑ in the composer) are handled by the control that has focus, and the
  palette lists them dimmed with their reason and never runs them.
- To add a key: add a row, then give it a runner with `useKeyBinding(id, run)` where the thing it
  moves lives (no runner where it has nothing to act on: its key is left to the browser, and the
  palette lists it dimmed). Focus mode (⌘\\) and reading mode (⌘⇧.) are rows like the rest. The
  palette and the tooltips of the shell's toggles, the folded composer and Allow read the row's keys
  with `bindingLabel(id)`. A desktop menu sends a row's id, never a key press, to the tab in view
  (`desktop/goMenu.js`), and only an id in `DESKTOP_COMMANDS`.
- No other file reads `metaKey`/`ctrlKey` except the ones `everyChordIsOneRegistryRow.test.tsx`
  lists, and that list only shrinks; a handler that only needs to know a chord is held asks
  `heldWithAModifier`.
- An approval's answer is one request: the prompt stays decided once its request landed (the caller
  resolves `true`), a second press of Allow while the confirmation is asked only asks again, and
  every choice, Deny included, goes through the same guard.
- ⌘1 to ⌘9 open the chats in the chat list's order (pinned first, then the most recent) until
  threads replace the list. ⇧Esc and ⌘⇧E are not bound yet: they wait for the record of what has
  been seen and for the file tree.

## 7. Desktop buddy

- Off until the owner turns it on.
- Hidden unless something runs or waits on the owner, and nothing is scheduled while it is hidden:
  no timers, no animation frames.
- Only approvals, questions and errors interrupt. They come one at a time, in the order every
  surface keeps (consequence, then age), and each stays until answered.
- Nothing is answered without an explicit click or key, by the rules of §6.
- Four visual families (quiet, active, waiting, blocked) carry urgency. The text names the exact
  phase.
- Listening always wins.
- Reduced motion and the expressiveness and bounciness dials are honoured.
- Sound is off by default.
- No other product's character, name, icon or sounds.

Everything else is in [`docs/design/buddy/`](../docs/design/buddy/README.md).

## 8. Definition of done for a UI PR

1. The web gate passes from the repository root:
   `npm run typecheck:web && npm run test:web && npm run build`, then `npm run smoke:render`.
2. `npm run e2e:a11y --workspace=web` and `npm run e2e:visual --workspace=web` pass. A snapshot is
   updated only for an intended change, and the PR names it. The visual baselines are macOS-only,
   so that leg runs locally until #3943.
3. The one named UI quality check passes (#3811, #3941). It covers every route and substate URL in
   light and dark, at 320, 390, 412, 768, 1024 and 1440 px and at 200% text zoom, operated by
   keyboard alone, with its focus order, 44 px compact targets, rendered contrast, reduced motion
   and accessibility-tree snapshot.
4. `npm run audit:consistency` reports fewer raw controls than before, never more.
5. The design detector reports no new findings over `web/src`
   (`npx impeccable@4.6.0 detect web/src`).
6. The surface renders each of its states: empty, large, long content, offline, a concurrent or
   expired approval, interrupted, failed and recovered.
7. One surface per PR. The PR answers §4's checklist, quotes its screen's before figures from the
   [baseline report](../docs/design/baseline.md) (#3804), and shows before and after screenshots,
   light and dark.
8. Every route and substate it touches round-trips through the URL and browser history.
9. A motion or backdrop change attaches a browser Performance trace and Electron
   `app.getAppMetrics()` CPU, before and after, with the window idle. With the default identity, the
   window focused and no turn running, the app schedules no animation-frame callbacks.
10. A visual change never changes what the agent says or may do. The instructions a turn sends
    change only in a PR that is about them.
11. Where the look changed, `web/DESIGN.md` changed first.
