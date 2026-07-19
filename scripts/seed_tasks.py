#!/usr/bin/env python3
"""Seed a rich, realistic Tasks dataset for dev / visual QA.

Wipes the existing tasks + non-default projects/task-lists, then builds a dataset
that exercises EVERY task feature in combination so the UI (list/cards/board/DAG,
detail panel, filters, sort, search, reset) can be evaluated against real data:

- Real Project → TaskList → Task hierarchy (several projects incl. a Repeatable one)
- All five priorities and all five statuses, in varied combinations
- Exit criteria (none / partial / fully met → drives the complete-gate + progress)
- Ordered action plans (some steps completed)
- All three note channels (general / research / execution)
- A typed dependency DAG with a real critical path + a bottleneck (one task many
  others depend on) + auto-blocked dependents
- Due dates spanning overdue → today → soon → far future (+ some with none)
- Assignees, labels/tags, agent-instruction templates, comments
- A Repeatable-project list whose tasks are all done (so reset is exercisable)

Run against the live gateway (default http://127.0.0.1:10000, no-auth dev mode):
    .venv/bin/python scripts/seed_tasks.py [BASE_URL]
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:10000"


def _req(method: str, path: str, body: dict | None = None) -> dict:
    data = None
    headers = {"X-Session-Key": "seed-script"}
    if body is not None:
        import json as _json
        data = _json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            import json as _json
            txt = r.read().decode()
            return _json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        import json as _json
        detail = e.read().decode()
        try:
            detail = _json.loads(detail).get("error", detail)
        except Exception:
            pass
        raise SystemExit(f"{method} {path} → {e.code}: {detail}")


def get(p): return _req("GET", p)
def post(p, b): return _req("POST", p, b)
def put(p, b): return _req("PUT", p, b)
def delete(p): return _req("DELETE", p)


def _iso_days(offset_days: int) -> str:
    """An ISO date `offset_days` from today (negative = overdue)."""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + offset_days * 86400))


def wipe() -> None:
    """Remove existing tasks + custom projects/lists so the seed is deterministic."""
    for t in get("/api/tasks?limit=10000").get("tasks", []):
        if t.get("provider") == "native":
            delete(f"/api/tasks/{t['id']}")
    for tl in get("/api/task-lists").get("task_lists", []):
        delete(f"/api/task-lists/{tl['id']}")
    for p in get("/api/projects").get("projects", []):
        if not p.get("is_default"):
            delete(f"/api/projects/{p['id']}")
    print("wiped existing tasks + custom projects/lists")


def project(name: str, instructions: str = "") -> str:
    return post("/api/projects", {"name": name, "agent_instructions_template": instructions})["id"]


def task_list(name: str, project_id: str = "", repeatable: bool = False) -> str:
    body = {"name": name}
    if repeatable:
        body["repeatable"] = True
    elif project_id:
        body["project_id"] = project_id
    return post("/api/task-lists", body)["id"]


def task(**fields) -> str:
    return post("/api/tasks", fields)["id"]


def comment(task_id: str, body: str, author: str = "you") -> None:
    post(f"/api/tasks/{task_id}/comments", {"body": body, "author": author})


def main() -> None:
    print(f"Seeding rich task dataset against {BASE}")
    wipe()

    # ── Project: Beacon Launch — a feature build with a full dependency DAG ──
    beacon = project(
        "Beacon Launch",
        "Ship the Beacon feature. Prefer small, reviewable changes; keep the API contract stable.",
    )
    build = task_list("Build", project_id=beacon)
    qa = task_list("QA & Release", project_id=beacon)

    # Root design tasks (done) → a bottleneck (backend) several tasks depend on.
    spec = task(
        title="Write the feature spec", task_list_id=build, status="done", priority="high",
        labels=["planning"], assignee="ravi", due=_iso_days(-14),
        exit_criteria=[{"description": "scope agreed", "met": True}, {"description": "API sketch reviewed", "met": True}],
        notes=[{"content": "Kicked off from the Q3 planning doc."}],
        research_notes=[{"content": "Surveyed 3 similar features in the codebase for prior art."}],
    )
    contract = task(
        title="Design the API contract", task_list_id=build, status="done", priority="high",
        labels=["planning", "api"], assignee="ravi", depends_on=[spec], due=_iso_days(-10),
        exit_criteria=[{"description": "endpoints documented", "met": True}],
        action_plan=[{"content": "draft OpenAPI", "completed": True}, {"content": "review with frontend", "completed": True}],
    )
    backend = task(  # the BOTTLENECK — many tasks depend on this
        title="Build backend endpoints", task_list_id=build, status="in_progress", priority="critical",
        labels=["backend", "api"], assignee="mei", depends_on=[contract, spec], due=_iso_days(-2),
        agent_instructions_template="Implement each endpoint behind the documented contract; add unit tests per handler.",
        exit_criteria=[
            {"description": "GET /things implemented", "met": True},
            {"description": "POST /things implemented", "met": True},
            {"description": "auth middleware wired", "met": False},
        ],
        action_plan=[
            {"content": "scaffold the router", "completed": True},
            {"content": "implement handlers", "completed": True},
            {"content": "wire auth + rate limits", "completed": False},
        ],
        research_notes=[{"content": "Reused the existing pagination helper from the search module."}],
        execution_notes=[{"content": "Handlers live in api/things.py; auth still TODO."}],
    )
    migrate = task(
        title="Migrate the database schema", task_list_id=build, status="in_progress", priority="critical",
        labels=["backend", "db"], assignee="mei", depends_on=[contract], due=_iso_days(0),
        exit_criteria=[{"description": "migration written", "met": True}, {"description": "rollback tested", "met": False}],
    )
    frontend = task(
        title="Build the frontend UI", task_list_id=build, status="open", priority="medium",
        labels=["frontend"], assignee="dana", depends_on=[backend], due=_iso_days(5),
        action_plan=[{"content": "list view", "completed": False}, {"content": "detail drawer", "completed": False}],
    )
    seed_data = task(
        title="Seed test data", task_list_id=qa, status="open", priority="medium",
        labels=["qa"], depends_on=[migrate], due=_iso_days(3),
    )
    e2e = task(
        title="Write end-to-end tests", task_list_id=qa, status="open", priority="high",
        labels=["qa"], assignee="dana", depends_on=[frontend, seed_data], due=_iso_days(7),
        exit_criteria=[{"description": "happy path", "met": False}, {"description": "error states", "met": False}],
    )
    docs = task(
        title="Write API docs", task_list_id=qa, status="open", priority="low",
        labels=["docs"], depends_on=[backend], due=_iso_days(8),
    )
    ship = task(  # critical-path tail
        title="Ship the release", task_list_id=qa, status="open", priority="critical",
        labels=["release"], assignee="ravi", depends_on=[e2e, docs], due=_iso_days(10),
        agent_instructions_template="Cut the release only when all blockers are done and the changelog is updated.",
    )
    comment(backend, "Auth middleware is the last piece — pairing with security tomorrow.", "mei")
    comment(backend, "Sounds good, I'll have the token format ready.", "ravi")
    comment(ship, "Holding the release train for the e2e suite.")

    # ── Project: Security — a mix incl. a MANUAL block + a cancelled task ──
    sec = project("Security")
    sec_list = task_list("Hardening", project_id=sec)
    task(
        title="Rotate API keys", task_list_id=sec_list, status="blocked", priority="critical",
        labels=["security"], assignee="mei", due=_iso_days(-1),
        notes=[{"content": "Blocked on the vendor — waiting for their new key-issuance window (external, no prerequisite)."}],
    )  # manual block (no unfinished prereq) — exercises manual-vs-auto block
    task(
        title="Pen-test the login flow", task_list_id=sec_list, status="open", priority="high",
        labels=["security", "auth"], due=_iso_days(14),
        exit_criteria=[{"description": "OWASP top-10 checked", "met": False}],
    )
    task(
        title="Deprecate the legacy webhook", task_list_id=sec_list, status="cancelled", priority="medium",
        labels=["cleanup"], notes=[{"content": "Cancelled — the webhook has external consumers we can't migrate yet."}],
    )

    # ── Repeatable project list — all tasks done, so the UI reset is exercisable ──
    weekly = task_list("Weekly ops", repeatable=True)
    for t in ("Review on-call alerts", "Triage the bug backlog", "Update the status dashboard"):
        task(title=t, task_list_id=weekly, status="done", priority="low", labels=["ops"],
             exit_criteria=[{"description": "completed this week", "met": True}])

    # ── Chore (default) — loose ad-hoc tasks, varied due dates for the sort view ──
    chore = get("/api/projects")["projects"]
    chore_id = next(p["id"] for p in chore if p["name"] == "Chore")
    chore_list = task_list("Inbox", project_id=chore_id)
    task(title="Reply to the design review thread", task_list_id=chore_list, status="open",
         priority="medium", due=_iso_days(-3), labels=["admin"])           # overdue
    task(title="Book the team offsite", task_list_id=chore_list, status="open",
         priority="low", due=_iso_days(1), labels=["admin"])               # due tomorrow
    task(title="Renew the TLS certificate", task_list_id=chore_list, status="open",
         priority="high", due=_iso_days(2), labels=["infra"])              # due soon
    task(title="Draft the Q4 roadmap", task_list_id=chore_list, status="in_progress",
         priority="medium", due=_iso_days(30), labels=["planning"],
         action_plan=[{"content": "gather input", "completed": True}, {"content": "draft themes", "completed": False}])
    task(title="Archive last quarter's projects", task_list_id=chore_list, status="done",
         priority="trivial", labels=["cleanup"])                           # trivial priority rung
    task(title="Tidy the shared drive", task_list_id=chore_list, status="open", priority="trivial")

    tasks = get("/api/tasks?limit=10000")
    projects = get("/api/projects")["projects"]
    lists = get("/api/task-lists")["task_lists"]
    print(f"\nSeeded: {tasks['total']} tasks across {len(projects)} projects / {len(lists)} task lists")
    print("Coverage: all 5 statuses + 5 priorities, exit criteria (partial/full),")
    print("ordered action plans, 3 note channels, a dependency DAG with a bottleneck +")
    print("critical path + auto-blocked dependents, a manual block, due dates")
    print("(overdue→far), assignees, labels, agent instructions, comments, and a")
    print("done Repeatable list ready to reset.")


if __name__ == "__main__":
    main()
