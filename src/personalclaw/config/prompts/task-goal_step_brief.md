GOAL TO PLAN:
{{goal}}

CURRENT PLANNING STEP: {{step_title}}  (kind: {{step_kind}}){% if objective %}
Objective: {{objective}}{% endif %}{% if approved_block %}

APPROVED ARTIFACTS SO FAR (build on these — stay consistent):
{{approved_block}}{% endif %}{% if comments_block %}

THE USER COMMENTED ON YOUR LAST DRAFT — address every point:
{{comments_block}}{% endif %}

Investigate context as needed (the goal may point at internal docs/tickets reachable via MCP, or the web). Then PRODUCE THIS STEP'S ARTIFACT as JSON written to `{{artifact_sentinel}}` in your current directory.

{{artifact_contract}}

Ground every claim concretely — no filler. This is a single pass for THIS step: once the file is written, you are DONE (the user reviews it next).