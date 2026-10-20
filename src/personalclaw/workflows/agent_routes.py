"""The workflow operations an agent's tools make with the gateway's internal credential.

The tool server an agent CLI runs (``personalclaw mcp-core``) makes each of its workflow tools'
calls on the gateway (`mcp_workflows_gateway`), and a batch `subagent_run` compiled is started
there too. ``dashboard/server.py`` opens these to the credential, beside every operation the
credential opens. They are kept apart from the routes (`workflows/handlers.py`), which import the
dashboard: the server reads these as it is imported, so it must not import the routes, or a process
that imports the routes first would import them half-made.
"""

from __future__ import annotations

#: The names the routes beside one workflow's read (`handlers.api_def_detail`) are served by: each
#: is registered first, so the router serves it, and the read is every other name.
BESIDE_ONE_WORKFLOW = ("runs", "attention", "surfacing", "manifest", "audit")

#: The owner's own routes the tool server an agent CLI runs makes its workflow tools' calls by.
#: Her browser calls each too, and each takes such a call as an agent's
#: (`approval_answer.of_request`). One workflow's read names none of the routes beside it, so the
#: credential opens that read alone.
AGENT_TOOL_ROUTES: frozenset[str] = frozenset(
    {
        "GET /api/workflows",
        "GET /api/workflows/{name:(?!(?:" + "|".join(BESIDE_ONE_WORKFLOW) + ")$)[^/]+}",
        "DELETE /api/workflows/{name}",
        "GET /api/workflows/manifest",
        "GET /api/workflows/audit",
        "POST /api/workflows/runs",
        "GET /api/workflows/runs/{run_id}",
        "GET /api/workflows/runs/{run_id}/outputs/{node_id}",
        "POST /api/workflows/runs/{run_id}/edit",
        "POST /api/workflows/runs/{run_id}/start",
        "POST /api/workflows/runs/{run_id}/pause",
        "POST /api/workflows/runs/{run_id}/resume",
        "POST /api/workflows/runs/{run_id}/cancel",
        "POST /api/workflows/runs/{run_id}/rewind",
        "POST /api/workflows/runs/{run_id}/run-from",
        "POST /api/workflows/runs/{run_id}/fork",
    }
)

#: And the ones only an agent's tools call, which no owner's route serves: the batch
#: `subagent_run` compiled (`handlers.api_batch_start`), and an agent CLI's save it hands over and
#: its dry run (`handlers.api_agent_save`), its plan, and its bounded watch of a run.
AGENT_ONLY_TOOL_ROUTES: frozenset[str] = frozenset(
    {
        "POST /api/workflows/batches",
        "POST /api/workflows/agent-saves",
        "POST /api/workflows/agent-plans",
        "GET /api/workflows/runs/{run_id}/observe",
    }
)
