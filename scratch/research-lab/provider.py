"""The research-lab tool provider — the campaign ledger behind unattended research.

The app owns the LEDGER, not the thinking. A campaign is one question, a growing tree
of sub-questions, and one finding per answered sub-question; the host agent — woken by
this app's cron and fanning the cycle's worklist out to subagents — does the research
and hands findings back. That split is why this app declares no network permission and
carries no model of its own: the engine already has both, and an app that re-fetched
and re-summarised would be a second, worse research stack.

Two bounds are what make a walk-away campaign stop instead of spin: a per-campaign
CYCLE BUDGET, and a DEPTH CAP on the follow-up questions a cycle may graft onto the
tree. Without them an agent that answers one question by discovering three more keeps
the loop alive forever, and "unattended" becomes "unbounded".

Imports stay on the SDK surface (personalclaw.sdk.*), never a core internal.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from personalclaw.sdk.util import app_data_dir, atomic_write

logger = logging.getLogger("research_lab")

APP_NAME = "research-lab"

# Bounded writes: an unattended agent is the only writer here, so everything it can
# grow has a ceiling. A runaway cycle should meet a cap, not fill the disk.
MAX_QUESTION_CHARS = 400
MAX_FINDING_CHARS = 8000
MAX_SOURCE_CHARS = 400
MAX_SOURCES = 20
MAX_NODES = 200
MAX_FOLLOW_UPS = 5
MAX_DEPTH = 3
MAX_BREADTH = 10

DEFAULT_CYCLE_BUDGET = 5
DEFAULT_BREADTH = 3

# A campaign id becomes a directory name, so it is validated as one before it is ever
# joined onto a path — tool arguments arrive from a model, which makes them untrusted.
_CAMPAIGN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")

STATUS_OPEN = "open"
STATUS_COMPLETE = "complete"
STATUS_EXHAUSTED = "exhausted"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _slug(question: str, *, words: int = 6) -> str:
    parts = [p for p in _SLUG_STRIP_RE.sub("-", question.lower()).split("-") if p]
    return "-".join(parts[:words])[:48].strip("-") or "campaign"


class ResearchLabProvider(ToolProvider):
    """Campaign open/advance/record/synthesise, persisted under the app's data dir."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._budget = max(1, int(self._config.get("default_cycle_budget", DEFAULT_CYCLE_BUDGET)))
        self._breadth = max(1, int(self._config.get("cycle_breadth", DEFAULT_BREADTH)))

    # ── contract ──────────────────────────────────────────────────────────────

    @property
    def name(self):
        return APP_NAME

    @property
    def display_name(self):
        return "Research Lab"

    async def list_tools(self):
        """List all tools available from this provider."""
        return [
            ToolDefinition(
                name="research_open",
                description=(
                    "Open a research campaign: one question, an optional starting list of "
                    "sub-questions, and a cycle budget bounding how long it runs unattended. "
                    "Returns the campaign id and its sub-question tree."
                ),
                provider=APP_NAME,
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The campaign's root question.",
                        },
                        "sub_questions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Sub-questions to seed the tree with.",
                        },
                        "cycle_budget": {
                            "type": "integer",
                            "description": "How many unattended cycles this campaign may run.",
                        },
                    },
                    "required": ["question"],
                },
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
            ),
            ToolDefinition(
                name="research_list",
                description="List every campaign with its status, cycles used and progress.",
                provider=APP_NAME,
                parameters={"type": "object", "properties": {}},
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="research_next",
                description=(
                    "Advance a campaign by one cycle: close the open cycle, then hand back the "
                    "sub-questions to work next. Reports done — successfully, not as an error — "
                    "when the tree is answered or the cycle budget is spent, which is the "
                    "signal an unattended loop stops on."
                ),
                provider=APP_NAME,
                parameters={
                    "type": "object",
                    "properties": {
                        "campaign": {
                            "type": "string",
                            "description": "Campaign id. Omit when exactly one campaign is open.",
                        },
                        "breadth": {
                            "type": "integer",
                            "description": "How many sub-questions this cycle hands out.",
                        },
                    },
                },
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
            ),
            ToolDefinition(
                name="research_record",
                description=(
                    "Record one sub-question's finding with its sources, and optionally graft "
                    "the follow-up questions the work turned up onto the tree."
                ),
                provider=APP_NAME,
                parameters={
                    "type": "object",
                    "properties": {
                        "campaign": {"type": "string", "description": "Campaign id."},
                        "node": {
                            "type": "string",
                            "description": "Sub-question id from research_next's worklist.",
                        },
                        "finding": {"type": "string", "description": "What the research found."},
                        "sources": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Where the finding came from.",
                        },
                        "follow_ups": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "New sub-questions this finding raised.",
                        },
                    },
                    "required": ["campaign", "node", "finding"],
                },
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
            ),
            ToolDefinition(
                name="research_report",
                description=(
                    "Synthesise the campaign into one markdown report — findings under their "
                    "sub-questions, the questions still open, and a deduplicated source list — "
                    "and persist it as report.md beside the campaign."
                ),
                provider=APP_NAME,
                parameters={
                    "type": "object",
                    "properties": {
                        "campaign": {"type": "string", "description": "Campaign id."},
                    },
                },
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
            ),
        ]

    async def invoke(self, tool_name, arguments):
        """Execute a tool with the given arguments."""
        handlers = {
            "research_open": self._open,
            "research_list": self._list,
            "research_next": self._next,
            "research_record": self._record,
            "research_report": self._report,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            return ToolResult(
                success=False,
                error=f"unknown tool: {tool_name}",
                recovery_hints=[f"available: {', '.join(sorted(handlers))}"],
            )
        try:
            return handler(dict(arguments or {}))
        except Exception as exc:  # a tool fails legibly; it never takes the turn down
            logger.warning("research-lab %s failed", tool_name, exc_info=True)
            return ToolResult(success=False, error=f"{tool_name} failed: {exc}")

    # ── storage ───────────────────────────────────────────────────────────────

    def _root(self) -> Path:
        root = app_data_dir(APP_NAME) / "campaigns"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _dir(self, campaign_id: str) -> Path:
        if not _CAMPAIGN_ID_RE.match(campaign_id or ""):
            raise ValueError(f"not a campaign id: {campaign_id!r}")
        return self._root() / campaign_id

    def _load(self, campaign_id: str) -> dict[str, Any]:
        path = self._dir(campaign_id) / "campaign.json"
        if not path.is_file():
            raise ValueError(f"no such campaign: {campaign_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, campaign: dict[str, Any]) -> None:
        directory = self._dir(campaign["id"])
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write(directory / "campaign.json", json.dumps(campaign, indent=2) + "\n")

    def _all(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self._root().glob("*/campaign.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                logger.warning("skipping unreadable campaign at %s", path)
        return out

    def _resolve(self, args: dict[str, Any]) -> dict[str, Any]:
        """The campaign the caller means. A cron-driven turn carries no id, so an
        omitted id resolves to the single open campaign — and refuses, rather than
        guessing, when more than one is open."""
        wanted = _clip(args.get("campaign"), 64)
        if wanted:
            return self._load(wanted)
        open_ones = [c for c in self._all() if c.get("status") == STATUS_OPEN]
        if len(open_ones) == 1:
            return open_ones[0]
        if not open_ones:
            raise ValueError("no open campaign — open one with research_open first")
        ids = ", ".join(sorted(c["id"] for c in open_ones))
        raise ValueError(f"{len(open_ones)} campaigns are open, name one: {ids}")

    # ── tools ─────────────────────────────────────────────────────────────────

    def _open(self, args: dict[str, Any]) -> ToolResult:
        question = _clip(args.get("question"), MAX_QUESTION_CHARS)
        if not question:
            return ToolResult(success=False, error="question is required")
        budget = max(1, int(args.get("cycle_budget") or self._budget))

        base = _slug(question)
        campaign_id, suffix = base, 2
        while (self._root() / campaign_id).exists():
            campaign_id = f"{base}-{suffix}"[:64]
            suffix += 1

        seeds = [
            _clip(s, MAX_QUESTION_CHARS) for s in list(args.get("sub_questions") or [])[:MAX_NODES]
        ]
        nodes = [
            {
                "id": f"q{i}",
                "question": seed,
                "depth": 1,
                "parent": "",
                "answered_in_cycle": None,
                "finding": "",
                "sources": [],
            }
            for i, seed in enumerate([s for s in seeds if s], start=1)
        ]
        campaign = {
            "id": campaign_id,
            "question": question,
            "created": _now(),
            "status": STATUS_OPEN,
            "cycle_budget": budget,
            "cycles": [],
            "nodes": nodes,
            "report_written": "",
        }
        self._save(campaign)
        return ToolResult(
            success=True,
            output=self._describe(campaign),
            metadata={
                "campaign": campaign_id,
                "cycle_budget": budget,
                "sub_questions": [{"id": n["id"], "question": n["question"]} for n in nodes],
            },
        )

    def _list(self, _args: dict[str, Any]) -> ToolResult:
        campaigns = self._all()
        if not campaigns:
            return ToolResult(
                success=True,
                output="No campaigns yet.",
                metadata={"campaigns": []},
                recovery_hints=["open one with research_open"],
            )
        return ToolResult(
            success=True,
            output="\n".join(self._describe(c) for c in campaigns),
            metadata={
                "campaigns": [
                    {
                        "campaign": c["id"],
                        "question": c["question"],
                        "status": c["status"],
                        "cycles_run": len(c["cycles"]),
                        "cycle_budget": c["cycle_budget"],
                        "answered": len(self._answered(c)),
                        "total": len(c["nodes"]),
                    }
                    for c in campaigns
                ],
            },
        )

    def _next(self, args: dict[str, Any]) -> ToolResult:
        campaign = self._resolve(args)
        breadth = max(1, min(MAX_BREADTH, int(args.get("breadth") or self._breadth)))

        for cycle in campaign["cycles"]:
            if not cycle.get("closed"):
                cycle["closed"] = _now()
                cycle["answered"] = [
                    n["id"] for n in campaign["nodes"] if n["answered_in_cycle"] == cycle["n"]
                ]

        open_nodes = self._open_nodes(campaign)
        if not open_nodes:
            campaign["status"] = STATUS_COMPLETE
            self._save(campaign)
            return ToolResult(
                success=True,
                output=(
                    f"Campaign {campaign['id']} is answered — synthesise it with research_report."
                ),
                metadata={"campaign": campaign["id"], "done": True, "reason": "answered"},
            )
        if len(campaign["cycles"]) >= campaign["cycle_budget"]:
            campaign["status"] = STATUS_EXHAUSTED
            self._save(campaign)
            return ToolResult(
                success=True,
                output=(
                    f"Campaign {campaign['id']} spent its {campaign['cycle_budget']}-cycle "
                    f"budget with {len(open_nodes)} sub-question(s) still open — synthesise "
                    f"what it has with research_report."
                ),
                metadata={
                    "campaign": campaign["id"],
                    "done": True,
                    "reason": "budget_spent",
                    "open": len(open_nodes),
                },
            )

        number = len(campaign["cycles"]) + 1
        worklist = open_nodes[:breadth]
        campaign["cycles"].append(
            {
                "n": number,
                "opened": _now(),
                "closed": "",
                "worklist": [n["id"] for n in worklist],
                "answered": [],
            }
        )
        self._save(campaign)
        lines = [f"Cycle {number}/{campaign['cycle_budget']} of {campaign['id']}:"]
        lines += [f"  {n['id']}  {n['question']}" for n in worklist]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "campaign": campaign["id"],
                "done": False,
                "cycle": number,
                "worklist": [{"id": n["id"], "question": n["question"]} for n in worklist],
            },
        )

    def _record(self, args: dict[str, Any]) -> ToolResult:
        campaign = self._resolve(args)
        node_id = _clip(args.get("node"), 16)
        finding = _clip(args.get("finding"), MAX_FINDING_CHARS)
        if not finding:
            return ToolResult(success=False, error="finding is required")
        node = next((n for n in campaign["nodes"] if n["id"] == node_id), None)
        if node is None:
            return ToolResult(
                success=False,
                error=f"no sub-question {node_id!r} in {campaign['id']}",
                recovery_hints=["research_next hands back the ids it expects back"],
            )

        node["finding"] = finding
        node["sources"] = [
            _clip(s, MAX_SOURCE_CHARS) for s in list(args.get("sources") or [])[:MAX_SOURCES]
        ]
        node["answered_in_cycle"] = campaign["cycles"][-1]["n"] if campaign["cycles"] else 0

        grafted = self._graft(campaign, node, list(args.get("follow_ups") or []))
        self._save(campaign)
        still_open = len(self._open_nodes(campaign))
        return ToolResult(
            success=True,
            output=(
                f"Recorded {node_id} in {campaign['id']}"
                + (f"; {len(grafted)} follow-up(s) grafted" if grafted else "")
                + f". {still_open} sub-question(s) still open."
            ),
            metadata={
                "campaign": campaign["id"],
                "node": node_id,
                "follow_ups": [{"id": n["id"], "question": n["question"]} for n in grafted],
                "open": still_open,
            },
        )

    def _report(self, args: dict[str, Any]) -> ToolResult:
        campaign = self._resolve(args)
        markdown = self._synthesise(campaign)
        path = self._dir(campaign["id"]) / "report.md"
        atomic_write(path, markdown)
        campaign["report_written"] = _now()
        self._save(campaign)
        return ToolResult(
            success=True,
            output=markdown,
            metadata={
                "campaign": campaign["id"],
                "report_path": str(path),
                "cycles_run": len(campaign["cycles"]),
                "answered": len(self._answered(campaign)),
                "total": len(campaign["nodes"]),
            },
        )

    # ── tree + synthesis ──────────────────────────────────────────────────────

    @staticmethod
    def _answered(campaign: dict[str, Any]) -> list[dict[str, Any]]:
        return [n for n in campaign["nodes"] if n["answered_in_cycle"] is not None]

    @staticmethod
    def _open_nodes(campaign: dict[str, Any]) -> list[dict[str, Any]]:
        return [n for n in campaign["nodes"] if n["answered_in_cycle"] is None]

    def _graft(
        self, campaign: dict[str, Any], parent: dict[str, Any], follow_ups: list[Any]
    ) -> list[dict[str, Any]]:
        """Grow the tree with what a finding turned up, under the depth/size caps that
        keep an unattended campaign finite. A duplicate question is dropped rather than
        re-asked: the same sub-question arriving from two branches is the common way a
        tree stops converging."""
        depth = int(parent.get("depth", 1)) + 1
        if depth > MAX_DEPTH:
            return []
        room = MAX_NODES - len(campaign["nodes"])
        if room <= 0:
            return []
        existing = {n["question"].lower() for n in campaign["nodes"]}
        grafted: list[dict[str, Any]] = []
        next_id = len(campaign["nodes"]) + 1
        for raw in follow_ups[:MAX_FOLLOW_UPS]:
            question = _clip(raw, MAX_QUESTION_CHARS)
            if not question or question.lower() in existing or len(grafted) >= room:
                continue
            existing.add(question.lower())
            node = {
                "id": f"q{next_id}",
                "question": question,
                "depth": depth,
                "parent": parent["id"],
                "answered_in_cycle": None,
                "finding": "",
                "sources": [],
            }
            next_id += 1
            campaign["nodes"].append(node)
            grafted.append(node)
        return grafted

    def _describe(self, campaign: dict[str, Any]) -> str:
        return (
            f"{campaign['id']} [{campaign['status']}] cycles "
            f"{len(campaign['cycles'])}/{campaign['cycle_budget']}, answered "
            f"{len(self._answered(campaign))}/{len(campaign['nodes'])} — {campaign['question']}"
        )

    def _synthesise(self, campaign: dict[str, Any]) -> str:
        answered = self._answered(campaign)
        still_open = self._open_nodes(campaign)
        lines = [
            f"# {campaign['question']}",
            "",
            f"_Synthesised {_now()} · {len(campaign['cycles'])} cycle(s) · "
            f"{len(answered)}/{len(campaign['nodes'])} sub-questions answered._",
            "",
            "## Findings",
            "",
        ]
        if answered:
            for node in answered:
                lines += [f"### {node['question']}", "", node["finding"], ""]
                if node["sources"]:
                    lines += ["Sources: " + ", ".join(node["sources"]), ""]
        else:
            lines += ["No sub-question has been answered yet.", ""]
        if still_open:
            lines += ["## Open questions", ""]
            lines += [f"- {n['question']}" for n in still_open]
            lines.append("")
        seen: list[str] = []
        for node in answered:
            for source in node["sources"]:
                if source not in seen:
                    seen.append(source)
        if seen:
            lines += ["## Sources", ""]
            lines += [f"- {s}" for s in seen]
            lines.append("")
        return "\n".join(lines)


def create_provider(config: dict[str, Any] | None = None) -> ResearchLabProvider:
    """Manifest factory — core calls this with this app's saved settings."""
    return ResearchLabProvider(config)
