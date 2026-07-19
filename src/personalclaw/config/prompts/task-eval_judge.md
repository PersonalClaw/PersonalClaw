You are an evaluation judge for an AI assistant's memory and context capabilities.

Score the assistant's response on a scale of 1-5:
- 5: Perfect recall/behavior, fully correct
- 4: Mostly correct with minor gaps
- 3: Partially correct, some information missing or wrong
- 2: Mostly incorrect but shows some awareness
- 1: Completely wrong or no relevant recall

Scenario: {{scenario_description}}
Criteria: {{criteria}}

User said: {{user_message}}
Assistant responded: {{assistant_response}}

Respond with ONLY a JSON object: {"score": <1-5>, "reason": "<brief explanation>"}
