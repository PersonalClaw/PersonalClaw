TASK TO PLAN:
{{task}}

You are designing the PLANNING WALKTHROUGH for this task — the ordered set of steps we'll walk the user through, one at a time, each producing an artifact the user approves before the next step runs.

FIRST, investigate the real context (so the steps fit reality, not a template):
{% if workspace_dir %}  A workspace is bound at: {{workspace_dir}}
  Read its key files (READMEs, plans/ or docs/, ROADMAP/BACKLOG, config, AGENTS.md) to learn the conventions + the SPECIFIC items this targets.{% else %}  No local workspace. Gather context from where the task points — internal docs/wikis/tickets via MCP, web/code search — whatever you have.{% endif %}

THEN decide which steps this target needs and in what order. Standard step kinds (pick the SUBSET that fits — skip any that don't apply):
{{guide}}
  (You may also invent a step kind if the target needs one — use a short snake_case slug.)

Narrate what you read/found as you go (your investigation must be visible).

When ready, WRITE the step list as JSON to `{{steps_sentinel}}` in your current directory, with this exact shape:
{
  "summary": "<1-2 sentences: what you found + why these steps>",
  "steps": [
    {"kind":"<snake_case slug>", "title":"<short human title>", "objective":"<what this step produces, referencing REAL discovered items>"},
    ...
  ]
}

Order matters — each step builds on the approved artifacts before it. The LAST step should be `decomposition` (the executable phase/task breakdown). This is a single design pass: once the file is written, you are DONE.