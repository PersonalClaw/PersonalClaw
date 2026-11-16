# Your library

**Library** in the sidebar is one place for everything you keep: the notes, files and links you saved,
the pages and documents your chats made, the reports your schedules write and what your intents find.
Its three tabs are **Library**, **Memory** and **Files**. This page is about the first.

---

## One list, with a preview beside it

The list holds each thing once, newest activity first: what you pinned at the top, then what you were
reading, then today, this week and earlier. Selecting a row opens it in the preview beside the list,
and the row stays selected in the address (`#/library?item=<ref>`), so a reload, Back and a copied
link open it again. On a phone the preview opens full screen, and Back returns to the row.

The header holds, in order:

- **Search**, which finds words and meaning across everything;
- **Filter**: Kind (notes, documents, links, images and media, pages and apps, reports), Made by (you,
  chats, automations, sources), Project, State, Collection, Tag and Sort by. The count line under the
  title says what the filter left and offers **Clear**;
- **List** or **Grid**;
- the **…**: Select items, Feeds, intents and reports, Read missed items and Process everything again…;
- **Add**: Upload files, Add a link, Write a note, Record audio, and the three ways to set up something
  that adds on its own. Dropping files anywhere on the page adds them too. With the Project filter
  set, what you add goes into that project.

Everything the list shows is in the address (`#/library?q=&kind=&made=&project=&collection=&tag=
&state=&sort=&layout=grid`), so a filtered list can be bookmarked or shared.

## What you can do with a thing

Every item has the same few actions, and a kind that cannot do one leaves it out:

- **Use in chat ▾**: a new chat with it attached (nothing is sent until you send), Ask about this (a
  new chat that reads it and changes nothing), Attach to the chat you were last in or another one, and
  Add to the project you were last in or any project;
- **Open**: its own page. A note, file or link opens its reader, an artifact its page, a report its
  latest write-up. Their back control returns to the library with the item selected, or to the project
  you opened it from;
- **Pin**: kept at the top of your library. A starred note is pinned here, and its Unpin clears the
  star too;
- **More**: Copy text, Copy link, Download, Add to project…, Mark as read or unread, Archive or Restore,
  and Delete… for what can be deleted.

**Select items** in the … lets you act on many at once: Pin, Add to collection, Archive, Delete…, and
in its … Add to project…, Mark as read and Mark as unread. A bulk act answers how many it did, and why
it skipped the rest.

## What adds on its own: feeds, intents and reports

**Feeds, intents and reports**, in the … and at `#/library?setup=all`, opens in the pane beside the
list. Pausing one keeps everything it added.

- **Intents** are things the agent looks out for in everything you add. Each checks every new item
  once with your model, and keeps what matches as findings, which sit in the list as one row per
  intent. **Run on saved items** checks what you already have, in the background: you can keep
  working, and Stop keeps what it found. An intent can write a report from its findings on a
  schedule.
- **Reports** answer a question on a schedule from what is new in your library. Each keeps one
  write-up, rewritten on every run. **Run now** writes one at once.
- **Feeds, folders and apps** bring new things in: a blog's feed, a page's listing, a folder on this
  computer. **Check now** looks at once; **Remove…** stops it and keeps what it added.

## Collections, tags and disagreements

A **collection** is a set you make by hand, or a search that fills itself. Filter to one to Rename or
Delete it from the count line; deleting it keeps its items. **Manage tags**, from the Filter's Tag
section, opens the tag tree in the pane: rename, nest, merge and delete, with the reports that read a
tag named before you change it.

When two items say different things, both are marked, and **Compare them** shows the two claims side
by side. You choose which one the agent goes by, or say both can be right; both are always kept.

## The Power user view

The **Power user view** switch in the header adds detail and changes nothing else. With it on, a tab
list runs down the left of the page: **Graph** (the default), **Items**, **Tags**, **Conflicts** and
**Decisions**, with **Library settings** pinned at its foot. Each tab says one fact under its name, and
a tab with nothing to say says nothing.

- **Graph** draws the people, places and ideas your items mention, and how they connect. Pick a name to
  see how many items mention it, what it is linked to and where; **Show all in Items** opens Items
  narrowed to them.
- **Items** is the list, with more on its count line (how many can be found by meaning, how many wait
  to be read) and in each preview's About (what was read from it, whether it is searchable by meaning,
  possible duplicates and a link to its own graph).
- **Tags**, **Conflicts** and **Decisions** are the tag tree, every disagreement with its Compare
  choices, and the decision journal.
- **Library settings** says what is in your library, how it is found and what needs a look, each with
  its one action: Re-index, Open graph, Show them, Review, Change, Process everything again…. Its
  Being read row counts the items still being read or that couldn't be read, the ones Show them
  lists; the names the graph has not taken yet from your items have a row of their own, with Set up
  a model while there is none to take them.

Feeds, intents and reports show more too: a feed's kind, how often it is really checked and how its
last check went; what an intent asks of your model; what a report reads and what it may cite.

Turning it off hides no control. Re-index search by meaning and Process everything again… are in the
page's "…", the State filter shows what is still being read or couldn't be read, and each item keeps its
own graph on its page.

The setting is kept on the gateway, so it is the same in every browser, the desktop app and the phone.
A link to one of its tabs (`#/library?view=graph`, and `tags`, `conflicts`, `decisions`, `items` or
`settings`) shows that tab whatever the setting, as a link restores what you were looking at: "On for
this visit. Your setting is off." says so, with **Keep it on**. A link never changes the setting; the
switch and Keep it on are the only things that do. ⌘K has **Turn on Power user view** (or off) and a row
for each tab.

## Old addresses

The Knowledge list, the Artifacts grid and the Sources and Reports pages are part of the library now.
Their old addresses open the same things here: `#/knowledge` and `#/artifacts` open the list, with
what they named, `#/knowledge?view=graph` (and its tags, conflicts and decisions) opens that tab of the
Power user view, and `#/knowledge/sources` and `#/knowledge/reports` open the set-up panel at that
group. A knowledge item's page (`#/knowledge/item/<id>`) and an artifact's (`#/artifacts/<slug>`)
keep their addresses.
