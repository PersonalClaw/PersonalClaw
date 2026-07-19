OVERALL DESIGN TASK:
{{task}}

CURRENT PLANNING STEP: {{step_title}}  (kind: {{step_kind}}){% if objective %}
Objective: {{objective}}{% endif %}{% if approved_block %}

APPROVED ARTIFACTS SO FAR (build on these — stay consistent):
{{approved_block}}{% endif %}{% if comments_block %}

THE USER COMMENTED ON YOUR LAST DRAFT OF THIS STEP — address every point:
{{comments_block}}{% endif %}{% if workspace_dir %}

Workspace (read it as needed): {{workspace_dir}}{% endif %}

Produce THIS STEP'S ARTIFACT as JSON written to `{{artifact_sentinel}}` in your current directory.

{{artifact_contract}}

Ground every choice in the actual product — no filler. This is a single pass for THIS step: once the file is written, you are DONE (the user reviews it next).