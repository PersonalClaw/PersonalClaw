"""Filesystem store for the Project / TaskList hierarchy.

A **Project** is a first-class work unit. Each project owns a directory under
``PERSONALCLAW_HOME/projects/<id>/`` holding:

- ``project.json`` — the project entity (metadata).
- ``context/`` — consolidated context for continuation across features/sessions.
- ``worktrees/`` — per-workspace git worktrees when the project binds a shared
  codebase (so several projects can operate on one workspace without colliding).

Task lists stay one-JSON-per-file under ``PERSONALCLAW_HOME/tasks/task_lists/``
(tasks themselves under ``.../tasks/`` via the native task provider).

The two protected default projects — ``Personal`` (catch-all for work created
without a chosen project) and ``Repeatable`` (home for resettable lists) — are
seeded on first access and cannot be deleted. Task-list creation routes to a
project by precedence: ``repeatable`` → the Repeatable project; an explicit
``project_id``; a ``project_name`` (find-or-create); else the Personal project.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from personalclaw.config.loader import config_dir
from personalclaw.tasks.models import DEFAULT_PROJECTS, Project, TaskList


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class HierarchyStore:
    """Filesystem-backed CRUD for projects and task lists."""

    def _base(self) -> Path:
        return config_dir() / "tasks"

    def _projects_dir(self) -> Path:
        # Projects live at the config root (not under tasks/) — they're a top-level
        # entity owning context + worktrees, not a sub-concern of the task system.
        d = config_dir() / "projects"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _lists_dir(self) -> Path:
        d = self._base() / "task_lists"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Projects ──

    def _project_dir(self, project_id: str) -> Path:
        return self._projects_dir() / project_id

    def _project_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def context_dir(self, project_id: str) -> Path:
        """The project's context directory (created on demand). Where a project's
        cross-feature context consolidates; also the working area when no external
        workspace is bound."""
        d = self._project_dir(project_id) / "context"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def worktrees_dir(self, project_id: str) -> Path:
        """The project's worktrees directory (created on demand). Holds per-workspace
        git worktrees when the project binds a shared codebase."""
        d = self._project_dir(project_id) / "worktrees"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _read_project(self, path: Path) -> Project | None:
        try:
            return Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def _write_project(self, project: Project) -> None:
        # Materialize the project's own directory + context dir on write, so the
        # context space exists from the moment a project does (features write into
        # it immediately). project.json lives inside projects/<id>/.
        self._project_dir(project.id).mkdir(parents=True, exist_ok=True)
        self.context_dir(project.id)
        self._project_path(project.id).write_text(
            json.dumps(project.to_dict(), indent=2), encoding="utf-8"
        )

    def migrate_layout(self) -> None:
        """One-time, idempotent migration to the projects/<id>/ layout.

        Three things, all safe to re-run:
        1. DELETE the orphaned legacy ``config/projects/*.json`` flat files — dead
           data from the pre-cutover Projects feature (vision/phases/steps), whose
           reading code was removed in the Goal-Loop cutover. The new layout uses
           ``config/projects/<id>/project.json`` (a subdir per project), so these
           flat files at the projects root are unambiguously legacy.
        2. MOVE each live project from the old ``config/tasks/projects/<id>.json``
           into ``config/projects/<id>/project.json`` + create its context dir.
        3. RENAME a legacy ``Chore`` default project to ``Personal`` (the new
           catch-all name) so its existing task lists carry over unbroken.
        """
        proot = self._projects_dir()
        # (1) delete legacy flat *.json at the projects root (new layout is subdirs).
        for f in proot.glob("*.json"):
            if f.is_file():
                f.unlink(missing_ok=True)
        # (2) migrate the old tasks/projects/<id>.json store into the new layout.
        old_dir = self._base() / "projects"
        if old_dir.is_dir():
            for f in sorted(old_dir.glob("*.json")):
                proj = self._read_project(f)
                if proj is None:
                    continue
                # A legacy id may be a slug ("chore"/"repeatable") or "p-xxxx"; keep it.
                if not self._project_path(proj.id).exists():
                    self._write_project(proj)
            import shutil
            shutil.rmtree(old_dir, ignore_errors=True)
        # (3) fold a legacy Chore project into the new Personal name.
        for p in self._all_projects_raw():
            if p.name == "Chore":
                # Only rename if a Personal doesn't already exist (else just drop the
                # rename — find_or_create_project will route to the existing Personal).
                if not any(q.name == "Personal" for q in self._all_projects_raw()):
                    p.name = "Personal"
                    p.is_default = True
                    self._write_project(p)

    def ensure_defaults(self) -> None:
        """Seed the Personal + Repeatable projects if absent."""
        self.migrate_layout()
        existing = {p.name for p in self._all_projects_raw()}
        for name in DEFAULT_PROJECTS:
            if name not in existing:
                now = _now_iso()
                self._write_project(
                    Project(
                        id=f"p-{uuid.uuid4().hex[:8]}",
                        name=name,
                        is_default=True,
                        created_at=now,
                        updated_at=now,
                    )
                )

    def _all_projects_raw(self) -> list[Project]:
        out: list[Project] = []
        # One directory per project: projects/<id>/project.json.
        for d in sorted(self._projects_dir().iterdir()):
            if not d.is_dir():
                continue
            p = self._read_project(d / "project.json")
            if p:
                out.append(p)
        return out

    def list_projects(self) -> list[Project]:
        self.ensure_defaults()
        return sorted(self._all_projects_raw(), key=lambda p: (not p.is_default_project(), p.name.lower()))

    def get_project(self, project_id: str) -> Project | None:
        return self._read_project(self._project_path(project_id))

    def get_project_by_name(self, name: str) -> Project | None:
        for p in self._all_projects_raw():
            if p.name == name:
                return p
        return None

    def find_or_create_project(self, name: str) -> Project:
        name = name.strip()
        if not name:
            return self.find_or_create_project("Personal")
        existing = self.get_project_by_name(name)
        if existing:
            return existing
        now = _now_iso()
        project = Project(
            id=f"p-{uuid.uuid4().hex[:8]}",
            name=name,
            is_default=name in DEFAULT_PROJECTS,
            created_at=now,
            updated_at=now,
        )
        self._write_project(project)
        return project

    def create_project(
        self,
        name: str,
        agent_instructions_template: str = "",
        *,
        workspace_dir: str = "",
        name_locked: bool = False,
        brief: str = "",
    ) -> Project:
        name = name.strip()
        if not name:
            raise ValueError("project name is required")
        if self.get_project_by_name(name):
            raise ValueError(f"a project named '{name}' already exists")
        now = _now_iso()
        project = Project(
            id=f"p-{uuid.uuid4().hex[:8]}",
            name=name,
            is_default=name in DEFAULT_PROJECTS,
            workspace_dir=str(workspace_dir or "").strip(),
            name_locked=bool(name_locked),
            agent_instructions_template=agent_instructions_template,
            brief=str(brief or "").strip(),
            created_at=now,
            updated_at=now,
        )
        self._write_project(project)
        return project

    def update_project(self, project_id: str, **fields) -> Project | None:
        project = self.get_project(project_id)
        if not project:
            return None
        if "name" in fields:
            new_name = str(fields["name"]).strip()
            if not new_name:
                raise ValueError("project name cannot be empty")
            other = self.get_project_by_name(new_name)
            if other and other.id != project_id:
                raise ValueError(f"a project named '{new_name}' already exists")
            project.name = new_name
        if "agent_instructions_template" in fields:
            project.agent_instructions_template = fields["agent_instructions_template"]
        if "brief" in fields:
            project.brief = str(fields["brief"] or "").strip()
        if "workspace_dir" in fields:
            project.workspace_dir = str(fields["workspace_dir"] or "").strip()
        if "status" in fields:
            status = str(fields["status"] or "").strip()
            if status not in ("active", "archived"):
                raise ValueError("status must be 'active' or 'archived'")
            project.status = status
        if "name_locked" in fields:
            project.name_locked = bool(fields["name_locked"])
        project.updated_at = _now_iso()
        self._write_project(project)
        return project

    def delete_project(self, project_id: str) -> bool:
        import shutil

        project = self.get_project(project_id)
        if not project:
            return False
        if project.is_default_project():
            raise ValueError(f"the default project '{project.name}' cannot be deleted")
        # Cascade: drop the project's task lists (tasks are re-homed by the caller
        # / left orphaned-by-list — the task provider owns task deletion).
        for tl in self.list_task_lists(project_id=project_id):
            self._list_path(tl.id).unlink(missing_ok=True)
        # Remove the whole project dir (project.json + context/ + worktrees/).
        shutil.rmtree(self._project_dir(project_id), ignore_errors=True)
        return True

    # ── Task lists ──

    def _list_path(self, list_id: str) -> Path:
        return self._lists_dir() / f"{list_id}.json"

    def _read_list(self, path: Path) -> TaskList | None:
        try:
            return TaskList.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def _write_list(self, tl: TaskList) -> None:
        self._list_path(tl.id).write_text(json.dumps(tl.to_dict(), indent=2), encoding="utf-8")

    def _all_lists_raw(self) -> list[TaskList]:
        out: list[TaskList] = []
        for f in sorted(self._lists_dir().glob("*.json")):
            tl = self._read_list(f)
            if tl:
                out.append(tl)
        return out

    def list_task_lists(self, project_id: str | None = None) -> list[TaskList]:
        lists = self._all_lists_raw()
        if project_id:
            lists = [tl for tl in lists if tl.project_id == project_id]
        return sorted(lists, key=lambda tl: tl.name.lower())

    def get_task_list(self, list_id: str) -> TaskList | None:
        return self._read_list(self._list_path(list_id))

    def create_task_list(
        self,
        name: str,
        *,
        project_id: str = "",
        project_name: str = "",
        repeatable: bool = False,
        agent_instructions_template: str = "",
    ) -> TaskList:
        """Create a task list, routing to a project by precedence:
        repeatable → Repeatable; explicit project_id → must exist;
        project_name → find-or-create; else → Personal."""
        name = name.strip()
        if not name:
            raise ValueError("task list name is required")
        self.ensure_defaults()
        if repeatable:
            project = self.find_or_create_project("Repeatable")
        elif project_id:
            project = self.get_project(project_id)
            if not project:
                raise ValueError(f"no project with id '{project_id}'")
        elif project_name:
            project = self.find_or_create_project(project_name)
        else:
            project = self.find_or_create_project("Personal")
        now = _now_iso()
        tl = TaskList(
            id=f"tl-{uuid.uuid4().hex[:8]}",
            name=name,
            project_id=project.id,
            agent_instructions_template=agent_instructions_template,
            created_at=now,
            updated_at=now,
        )
        self._write_list(tl)
        return tl

    def update_task_list(self, list_id: str, **fields) -> TaskList | None:
        tl = self.get_task_list(list_id)
        if not tl:
            return None
        if "name" in fields:
            new_name = str(fields["name"]).strip()
            if not new_name:
                raise ValueError("task list name cannot be empty")
            tl.name = new_name
        if "project_id" in fields and fields["project_id"]:
            target = self.get_project(fields["project_id"])
            if not target:
                raise ValueError(f"no project with id '{fields['project_id']}'")
            tl.project_id = target.id
        if "agent_instructions_template" in fields:
            tl.agent_instructions_template = fields["agent_instructions_template"]
        tl.updated_at = _now_iso()
        self._write_list(tl)
        return tl

    def delete_task_list(self, list_id: str) -> bool:
        if not self._list_path(list_id).exists():
            return False
        self._list_path(list_id).unlink(missing_ok=True)
        return True
