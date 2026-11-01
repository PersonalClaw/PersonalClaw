# 🦞 PersonalClaw — Visual Showcase

A guided tour of PersonalClaw: a self-hosted personal AI agent that unifies agentic chat,
autonomous goal loops, long-term memory, a knowledge base, skills, and automation behind
one gateway and one dashboard you own.

> _Every screen below is a **real capture** from a running instance seeded with illustrative
> scenario data — shown in both **light** and **dark** themes. No mockups. The set is
> reproducible per release via the capture pipeline ([docs/screenshots/CAPTURE.md](docs/screenshots/CAPTURE.md)).
> This set was captured with **no model bound**, so the screens that need one (chat replies,
> suggestions, knowledge extraction) show what a fresh install shows before you add a provider.
> Architecture diagrams accompany the screens whose *flow* is as important as their look._

---

## 🏠 The Dashboard

Your day at a glance: a prompt bar that starts a new thread, then what needs you, what is
running and what finished lately, with starter suggestions (they need a model, and say so).
The sidebar holds your threads by project, and Attention and Overview are two views of the
same home — all live over one WebSocket.

| Light | Dark |
|---|---|
| ![Dashboard — light](docs/screenshots/light/01-dashboard.png) | ![Dashboard — dark](docs/screenshots/dark/01-dashboard.png) |

---

## 🗣️ Agentic Chat

Conversational threads where the agent acts with tool access: it grounds answers in your
knowledge, and tool calls surface as decision-ready approval prompts. The composer chooses
the agent and how it works. On a fresh install with no provider yet, chat offers a one-time
138 MiB local model so it is never a dead end, or a link to connect a provider you already
have.

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
entities and relations, generates summaries, and embeds every item for semantic search, all
reachable by the agent in chat. Here, **five seeded notes and bookmarks**: extraction and
semantic search start once a model is bound, so the counters still read 0 entities and 0
relations. Knowledge, Memory, Files and Artifacts sit together under Library.

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
        Unattended work bounded by guardrails:
        budgets · breakers · approval gates
    end note
```

---

## ⏰ Automations & 🔁 Workflows

Cron / interval / webhook **triggers** fire background work — a native action, a saved
prompt, a workflow, or an agent — with human-readable schedules and next-fire times; here,
the built-in heartbeat, self-remediation and digests. **Workflows** are reusable SOPs
(ordered playbooks); with no runs yet, the page offers its templates to start from.

| Triggers — light | Triggers — dark |
|---|---|
| ![Triggers — light](docs/screenshots/light/06-triggers.png) | ![Triggers — dark](docs/screenshots/dark/06-triggers.png) |

| Workflows — light | Workflows — dark |
|---|---|
| ![Workflows — light](docs/screenshots/light/07-workflows.png) | ![Workflows — dark](docs/screenshots/dark/07-workflows.png) |

---

## 🧩 Skills & 🔌 App Platform

Reusable **SKILL.md** procedures from a marketplace, and an **everything-is-an-app**
platform: 37 built-in apps ship in-package, and every vendor — models, search, speech,
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
all filesystem-defined over the Agent Client Protocol (ACP). A built-in's definition is
fixed and its model swappable; agents you create are fully editable.

| Light | Dark |
|---|---|
| ![Agents — light](docs/screenshots/light/13-agents.png) | ![Agents — dark](docs/screenshots/dark/13-agents.png) |

---

## ⚙️ Settings — provider-agnostic by design

One hub for the whole system: security rules, secrets, the audit chain, apps, providers,
routing and search, each with its live figures. **Models** are assigned per use case (chat
and its routing sub-categories, embedding, speech-to-text, text-to-speech, and more) from
whatever providers you've added. This capture has none bound yet; the one model available
is the bundled offline one. Nothing ties you to one vendor.

| Settings — light | Settings — dark |
|---|---|
| ![Settings — light](docs/screenshots/light/11-settings.png) | ![Settings — dark](docs/screenshots/dark/11-settings.png) |

| Models — light | Models — dark |
|---|---|
| ![Models — light](docs/screenshots/light/12-settings-models.png) | ![Models — dark](docs/screenshots/dark/12-settings-models.png) |

---

## 🚀 Onboarding

A short first-run flow in five steps: your name, bringing your setup over, essential apps,
a first try, and you're set — or skip it and come back from Settings. Warm coral identity on
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
PERSONALCLAW_HOME=/tmp/pc-showcase personalclaw gateway --seed demo-home --seed-replace \
  --seed-local-model --port auto --no-open
node docs/screenshots/capture.mjs --home /tmp/pc-showcase   # in another shell: light/ + dark/
```

The pipeline follows the same seed-data → headless-capture pattern used across the
project's sibling repos, so the showcase stays honest (real UI, illustrative data) and
current (re-run per release).
