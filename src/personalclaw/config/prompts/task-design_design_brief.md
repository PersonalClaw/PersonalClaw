DESIGN TASK TO PLAN:
{{task}}

You are planning a DESIGN-SYSTEM build as a phased loop. You're designing the PLANNING WALKTHROUGH — the ordered set of steps we'll walk the user through, one at a time, each producing an artifact they approve before the next runs.

FIRST, understand the design task concretely (so the steps fit THIS product, not a template): who it's for, the brand/mood, the surfaces it spans, and any hard constraints (accessibility targets, existing brand colors, platform).{% if design_inputs_block %}

{{design_inputs_block}}{% endif %}{% if workspace_dir %}
  A workspace is bound at: {{workspace_dir}}
  Read any existing design notes, brand assets, or DESIGN.md there to ground the system in what already exists.{% endif %}

THEN decide which design phases this task needs and in what order. Standard phase kinds (pick the SUBSET that fits; you may add one with a snake_case slug):
{{guide}}

Narrate what you considered as you go (your reasoning must be visible).

When ready, WRITE the step list as JSON to `{{steps_sentinel}}` in your current directory, with this exact shape:
{
  "summary": "<1-2 sentences: your read of the task + why these phases>",
  "steps": [
    {"kind":"<snake_case slug>", "title":"<short human title>", "objective":"<what this phase produces, specific to THIS product>"},
    ...
  ]
}

Order matters — each phase builds on the approved ones before it. The LAST step should be `build_plan` (the executable phased breakdown). This is a single design pass: once the file is written, you are DONE.