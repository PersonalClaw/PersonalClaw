You are planning work. Read the goal and produce an adaptive plan as PHASES of clarifying QUESTIONS that, once answered, give enough detail to break the work into concrete tasks. Detect the kind of work and tailor the phases (software, event, research, routine — adapt; no fixed template). 2-4 phases, 2-5 questions each.{% if prior %}

RELEVANT PRIOR CONTEXT (skip questions already answered here):
{{prior}}{% endif %}

GOAL:
{{goal}}

Respond with ONLY a JSON object, no prose:
{"phases": [{"title": "...", "description": "...", "steps": [{"title": "short label", "prompt": "the question"}]}]}