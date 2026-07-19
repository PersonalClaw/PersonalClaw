{% if might_not_be_plan %}First, decide: is the following text an execution plan with actionable steps the user wants to carry out?
- If NO (e.g. it is an analysis, summary, explanation, or general response), return ONLY the string 'NOT_A_PLAN'
- If YES, reformat it to match this template:

{{plan_template}}

Issues to fix: {{issues}}
Keep all original stage content. Number stages from 1. End with [OPTION: Go | Go All | Cancel]. Return ONLY the result.

Text:
{{text}}{% else %}Reformat the following plan to match this exact template:

{{plan_template}}

Issues to fix: {{issues}}

Rules:
- Keep all original stage content and tasks
- Number stages sequentially starting from 1
- End with [OPTION: Go | Go All | Cancel]
- Return ONLY the reformatted plan, nothing else

Plan to reformat:
{{text}}{% endif %}