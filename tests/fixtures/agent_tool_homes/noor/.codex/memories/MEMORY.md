# Memory

- feedsmith: Noor prefers asyncio.TaskGroup over gather for fan-out (adopted after the 22 Jul review,
  together with the per-host-first semaphore order, #395).
- feedsmith changelog: Added / Changed / Fixed, PR number on every line, Tomás reviews the wording.
- carrier-webhooks: load tests live in loadtest/ and run against localhost:8080 only. Never point them
  at staging without asking.
- eta-engine: shipment_etas is about 410M rows. Every query needs a created_at bound.
- Home machine: the local profile uses qwen3-coder:30b through Ollama; it is fine for shell questions,
  not for reviews.
