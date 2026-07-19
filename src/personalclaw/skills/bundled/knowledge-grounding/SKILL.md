---
name: knowledge-grounding
description: Search the knowledge pool before answering, ground claims in retrieved sources and cite them, recognize ingest-worthy content and capture it with knowledge_create — don't fabricate when the answer is on record.
always: false
triggers: knowledge, search knowledge, what do we know, do we have, sources, cite, ingest, save to knowledge, bookmark, reference, look it up, according to
---

# Knowledge Grounding

PersonalClaw has a **knowledge pool** — ingested documents, notes, bookmarks, and
references the user has accumulated. When a question might be answered by what's
already on record, **search it first and ground your answer in what you find**
rather than answering from general assumptions or making something up.

Tools: `knowledge_search` (semantic/keyword search), `knowledge_get` (fetch a
specific item by id), `knowledge_create` (add new content to the pool).

## Search before answering

For any question that could plausibly be covered by the user's own material —
their projects, decisions, docs, references, prior research — **`knowledge_search`
first.** If relevant items come back, base your answer on them:

1. `knowledge_search` with the user's question (or its key terms).
2. `knowledge_get` the most relevant hits to read the actual content.
3. Answer **from the retrieved material**, not from a guess.

This is the difference between a confident-but-wrong answer and a grounded one.
If the user has the answer on record, use it.

## Cite your sources

When your answer rests on knowledge items, **say so** — name the source
(title/path/id) you drew from, so the user can trust and trace it. *"Per your
`Q2-architecture` note, …"* beats an unattributed assertion. If you synthesized
across several items, cite each. Citations also make it obvious when an answer is
grounded vs. when you're reasoning beyond the record.

## Don't fabricate when knowledge exists

If the pool plausibly contains the answer, **searching is mandatory before you
answer from memory.** Never invent a fact, a number, a decision, or a citation
when the real one is retrievable. And distinguish clearly:

- **Found it** → answer from the source, cite it.
- **Searched, found nothing** → say the knowledge pool doesn't cover it, then
  answer from general reasoning **labelled as such** (not presented as if it came
  from the user's records).

Don't dress up a guess as a recalled fact.

## Recognize ingest-worthy content

Some content is worth keeping. When the user shares — or you produce together —
something durable and reusable, capture it with `knowledge_create`:

- **`text`** — a note, a decision record, a distilled summary, research findings,
  a snippet of reference material worth keeping.
- **`bookmark`** — a URL/reference the user will want to find again.

Good candidates: "save this for later", a link the user clearly wants to keep, a
conclusion reached after real research, reference material that answers a
recurring question. Capture it so the next session can `knowledge_search` and
find it.

Don't ingest: transient chatter, secrets, throwaway scratch, or content the user
didn't signal is worth keeping. (For named, versioned generated UI/docs the user
iterates on, use the `artifacts` skill instead; for durable user *preferences and
corrections*, use `memory-discipline`. Knowledge is the searchable reference
pool.)
