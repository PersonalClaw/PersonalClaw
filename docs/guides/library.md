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
- **Pin**: kept at the top of your library;
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

## Old addresses

The Knowledge list, the Artifacts grid and the Sources and Reports pages are part of the library now.
Their old addresses open the same things here: `#/knowledge` and `#/artifacts` open the list, with
what they named, and `#/knowledge/sources` and `#/knowledge/reports` open the set-up panel at that
group. A knowledge item's page (`#/knowledge/item/<id>`) and an artifact's (`#/artifacts/<slug>`)
keep their addresses.
