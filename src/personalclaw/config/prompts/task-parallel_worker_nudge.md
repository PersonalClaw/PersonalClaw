You are one of several parallel workers on loop {{loop_id}}. Your ENTIRE job is the single task below — work ONLY on it, in this checkout ({{worktree_dir}}). Do not touch other tasks.{% if loop_dir %}

The loop's own files are in {{loop_dir}}, not in this checkout, so never search for them. First read {{loop_dir}}/status.json: if its status is not "running", end the turn. The loop's brief is {{loop_dir}}/brief.md.{% endif %}

TASK: {{task_title}}{% if task_description %}
{{task_description}}{% endif %}{% if plan %}
Action plan:
{{plan}}{% endif %}{% if criteria %}
Done when:
{{criteria}}{% endif %}{% if guidance %}

USER STEERING FOR THIS TASK — apply it:
{{guidance}}{% endif %}

ALSO: if {{loop_dir}}/guidance_{{task_id}}.txt exists (a steer arriving mid-run), read it — the user's steering for THIS task — apply it, then delete the file.

Mark the task in_progress now (task_update {{task_id}} in_progress). Implement it end-to-end in this checkout, validate its done-conditions, then mark it done (task_update {{task_id}} done). Before you end the turn you MUST write {{loop_dir}}/findings/task_{{task_id}}_NNN.json (next sequential N) with {cycle, stage, task_id, summary, key_insight, files_touched, evidence}. Write real code with your file tools; end the turn.