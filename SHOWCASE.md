# 🦞 PersonalClaw — Visual Showcase

A guided tour of PersonalClaw: a self-hosted personal AI agent that unifies agentic chat,
autonomous goal loops, long-term memory, a knowledge base, skills, and automation behind
one gateway and one dashboard you own.

> _Every screen below is a **real capture** from a running instance seeded with illustrative
> scenario data — shown in both **light** and **dark** themes. No mockups. The set is
> reproducible per release via the capture pipeline ([docs/screenshots/CAPTURE.md](docs/screenshots/CAPTURE.md)).
> Architecture diagrams accompany the screens whose *flow* is as important as their look._

---

## 🏠 The Dashboard

Your day at a glance: quick stats, a prompt bar that starts a chat or a task, "jump back in"
sessions, live tasks, contextual suggestions the system draws from your memory and knowledge,
and a real-time system strip — all live over one WebSocket.

| Light | Dark |
|---|---|
| ![Dashboard — light](docs/screenshots/light/01-dashboard.png) | ![Dashboard — dark](docs/screenshots/dark/01-dashboard.png) |

---

## 🗣️ Agentic Chat

Conversational sessions where the agent acts with full tool access. It **grounds answers in
your knowledge** (note the "worked through 3 steps · knowledge_search" and the cited source
below), and tool calls surface as decision-ready approval prompts. Sessions fork, undo,
branch into variants, and organize into folders/tags/kanban.

| Light | Dark |
|---|---|
| ![Chat — light](docs/screenshots/light/02-chat.png) | ![Chat — dark](docs/screenshots/dark/02-chat.png) |

```mermaid
sequenceDiagram
    participant U as You
    participant C as Chat runner
    participant Ctx as Context engine
    participant M as Model
    participant T as Tool (approval-gated)
    U->>C: message
    C->>Ctx: assemble (memory · knowledge · skills)
    Ctx->>M: prompt
    M-->>C: wants a tool
    C->>U: approval brief (what · why · blast radius)
    U-->>C: approve / deny / remember
    C->>T: run (sandboxed, egress-guarded)
    T-->>C: result
    C->>M: continue
    M-->>U: answer (streamed)
    C->>Ctx: after-turn learning (propose, never auto-write)
```

---

## 📚 Knowledge Base

Ingest notes, gists, bookmarks, documents, and media. A node-graph pipeline extracts
entities and relations, generates summaries, and embeds every item for semantic search —
here, **7 items → 50 entities → 32 relations**, all reachable by the agent in chat.

| Light | Dark |
|---|---|
| ![Knowledge — light](docs/screenshots/light/03-knowledge.png) | ![Knowledge — dark](docs/screenshots/dark/03-knowledge.png) |

## 🧠 Memory — a distinct store

A deliberate boundary: **Memory** is the harness's own learning about you (facts, episodes,
lessons); **Knowledge** is your personal library. They never bleed into each other. The
Memory Studio visualizes facts, documents, and their graph.

| Light | Dark |
|---|---|
| ![Memory — light](docs/screenshots/light/08-memory.png) | ![Memory — dark](docs/screenshots/dark/08-memory.png) |

```mermaid
flowchart LR
    subgraph MEM["🧠 Memory — memory.db (harness mechanics)"]
        F["Facts · facets"]
        E["Episodic"]
        P["Procedural · lessons"]
    end
    subgraph KN["📚 Knowledge — knowledge.db (your items)"]
        D["Docs · PDFs · web pages · media"]
        EN["Entities + relations (graph)"]
        S["Semantic + keyword search"]
    end
    CHAT["Chat / Loop context"] --> MEM
    CHAT --> KN
    CORR["Your corrections"] -.->|after-turn, gated| MEM
    ING["Ingestion"] -->|extract → chunk → embed| KN
```

---

## ✅ Tasks

A full task system — list, cards, **kanban**, and dependency-graph views — with priorities,
labels, statuses, and one-click completion. The agent can file and update tasks through its
tools, so what you plan and what it does stay in one place.

| Light | Dark |
|---|---|
| ![Tasks — light](docs/screenshots/light/04-tasks.png) | ![Tasks — dark](docs/screenshots/dark/04-tasks.png) |

---

## 🎯 Goal Loops

Give the agent a target; it classifies, plans, and loops **one cycle at a time** under a
deterministic supervisor — observable, pausable, resumable. Five kinds — General, Goal,
Code (SDLC), Research, and Design — each with depth and attended/unattended modes.

| Light | Dark |
|---|---|
| ![Goal loops — light](docs/screenshots/light/05-loops.png) | ![Goal loops — dark](docs/screenshots/dark/05-loops.png) |

```mermaid
stateDiagram-v2
    [*] --> Classify
    Classify --> Plan
    Plan --> Cycle
    Cycle --> Evaluate
    Evaluate --> Cycle: not done (chip away)
    Evaluate --> Done: goal met
    Cycle --> Paused: you pause / nudge
    Paused --> Cycle: resume
    Done --> [*]
    note right of Cycle
        Bounded by guardrails:
        budgets · breakers · approval gates
    end note
```

---

## ⏰ Triggers & 🔁 Workflows

Cron / interval / webhook **triggers** fire background work — a native action, a saved
prompt, a workflow, or an agent — with human-readable schedules and next-fire times.
**Workflows** are reusable SOPs (ordered playbooks) surfaced by scope + semantic match.

| Triggers — light | Triggers — dark |
|---|---|
| ![Triggers — light](docs/screenshots/light/06-triggers.png) | ![Triggers — dark](docs/screenshots/dark/06-triggers.png) |

| Workflows — light | Workflows — dark |
|---|---|
| ![Workflows — light](docs/screenshots/light/07-workflows.png) | ![Workflows — dark](docs/screenshots/dark/07-workflows.png) |

---

## 🧩 Skills & 🔌 App Platform

Reusable **SKILL.md** procedures from a marketplace, and an **everything-is-an-app**
platform: 27 built-in apps ship in-package, and every vendor — models, search, speech,
channels, agent runtimes — installs as a removable app through a quarantine → scan →
consent lifecycle. A "dangerous" scan verdict is terminal and non-overridable.

| Skills — light | Skills — dark |
|---|---|
| ![Skills — light](docs/screenshots/light/10-skills.png) | ![Skills — dark](docs/screenshots/dark/10-skills.png) |

| App platform — light | App platform — dark |
|---|---|
| ![Apps — light](docs/screenshots/light/09-apps.png) | ![Apps — dark](docs/screenshots/dark/09-apps.png) |

```mermaid
flowchart LR
    SRC["App source\n(git URL / local dir)"] --> Q["Quarantine"]
    Q --> SCAN{"Supply-chain scan"}
    SCAN -->|clean| INST["Install"]
    SCAN -->|warning| CONSENT["Consent required (409)"]
    SCAN -->|dangerous| REFUSE["Refused — non-overridable"]
    CONSENT -->|you confirm| INST
    INST --> REG["Register providers · MCP · crons"]
    REG --> BE["Backend subprocess\n(scoped token · watchdog)"]
```

---

## 🤖 Agents

Multiple built-in agents cooperate — a default conversational agent plus specialized
workers for goal loops, the code SDLC engine, planning, and background consolidation —
all filesystem-defined over the Agent Client Protocol (ACP) and fully editable.

| Light | Dark |
|---|---|
| ![Agents — light](docs/screenshots/light/13-agents.png) | ![Agents — dark](docs/screenshots/dark/13-agents.png) |

---

## ⚙️ Settings — provider-agnostic by design

One hub for the whole system. **Models** are assigned per use case (chat, embedding, STT,
TTS, image, audio, video) from whatever providers you've added — here, `claude-sonnet-5`
for chat and `text-embedding-3-small` for embeddings, chosen from **108** discovered models.
Nothing ties you to one vendor.

| Settings — light | Settings — dark |
|---|---|
| ![Settings — light](docs/screenshots/light/11-settings.png) | ![Settings — dark](docs/screenshots/dark/11-settings.png) |

| Models — light | Models — dark |
|---|---|
| ![Models — light](docs/screenshots/light/12-settings-models.png) | ![Models — dark](docs/screenshots/dark/12-settings-models.png) |

---

## 🚀 Onboarding

A short first-run flow: your name, a chat model, and you're in. Warm coral identity on
calm surfaces — "companion, not console."

| Light | Dark |
|---|---|
| ![Onboarding — light](docs/screenshots/light/01-onboarding.png) | ![Onboarding — dark](docs/screenshots/dark/01-onboarding.png) |

---

## Reproduce this showcase

Every screen above is reproducible in both themes from a seeded instance. Configure a model
provider, seed a little scenario data, then run the capture pipeline:

```bash
# see docs/screenshots/CAPTURE.md for the full walkthrough
PERSONALCLAW_AUTH_MODE=none personalclaw gateway --port 10000 --no-open
PCLAW_URL=http://localhost:10000 node docs/screenshots/capture.mjs   # light/ + dark/, every route
```

The pipeline follows the same seed-data → headless-capture pattern used across the
project's sibling repos, so the showcase stays honest (real UI, illustrative data) and
current (re-run per release).
