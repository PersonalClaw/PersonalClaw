"""A provider added or removed while a registry's walk awaits another ends no walk.

The diarization, image, video, knowledge-source and task registries each keep their providers in
one module dict, which ``providers/registry.py`` changes at run time as a provider instance is
added or removed (image and video also on a provider config save). Each walk below awaits one
provider at a time. Over the live ``_providers.values()``, an entry added or removed during that
await ended the walk with ``RuntimeError: dictionary changed size during iteration``, so the
Settings page or the search that asked got an error instead of an answer. Each walks a copy now.

Each test holds the first provider's awaited call on an event, adds or removes a provider while
it waits, then lets it answer: the walk returns what the registry held when it started.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest

from personalclaw.diarization import registry as diarization
from personalclaw.image_gen import registry as image_gen
from personalclaw.knowledge_providers import registry as knowledge
from personalclaw.knowledge_providers.base import KnowledgeItem
from personalclaw.tasks import registry as tasks
from personalclaw.tasks.models import Task
from personalclaw.video_gen import registry as video_gen

T = TypeVar("T")


class _Provider:
    """A provider of any of the five kinds, as far as their walks read one: its name, its info,
    and the call each walk awaits. A held provider's calls wait until it is let go."""

    def __init__(self, name: str, *, held: bool = False, holds_task: str = "") -> None:
        self.name = self.display_name = name
        self.called = asyncio.Event()
        self.let_go = asyncio.Event()
        if not held:
            self.let_go.set()
        self._holds_task = holds_task

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}

    async def _answer(self) -> None:
        self.called.set()
        await self.let_go.wait()

    async def is_available(self) -> bool:
        await self._answer()
        return True

    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
        await self._answer()
        return [KnowledgeItem(id=f"{self.name}:{query}", title=query)]

    async def get_task(self, task_id: str) -> Task | None:
        await self._answer()
        return Task(id=task_id, title="a task") if task_id == self._holds_task else None


async def _changed_under(walk: Awaitable[T], held: _Provider, change: Callable[[], None]) -> T:
    """What *walk* returns when *change* is made while *held*'s call waits."""

    async def change_while_held() -> None:
        await held.called.wait()
        change()
        held.let_go.set()

    answer, _ = await asyncio.gather(walk, change_while_held())
    return answer


@pytest.mark.asyncio
async def test_a_diarization_provider_added_while_one_is_probed(monkeypatch) -> None:
    held = _Provider("held", held=True)
    monkeypatch.setattr(diarization, "_providers", {"held": held, "other": _Provider("other")})

    infos = await _changed_under(
        diarization.list_all_providers_info(),
        held,
        lambda: diarization.register_provider(_Provider("added")),  # type: ignore[arg-type]
    )

    assert [info["name"] for info in infos] == ["held", "other"], infos


@pytest.mark.asyncio
async def test_an_image_provider_removed_while_it_is_probed(monkeypatch) -> None:
    held = _Provider("held", held=True)
    monkeypatch.setattr(image_gen, "_providers", {"held": held, "other": _Provider("other")})
    monkeypatch.setattr(image_gen, "_ensure_registered", lambda: None)

    infos = await _changed_under(
        image_gen.list_all_providers_info(), held, lambda: image_gen.unregister_provider("held")
    )

    assert [info["name"] for info in infos] == ["held", "other"], infos


@pytest.mark.asyncio
async def test_a_video_provider_added_while_one_is_probed(monkeypatch) -> None:
    held = _Provider("held", held=True)
    monkeypatch.setattr(video_gen, "_providers", {"held": held, "other": _Provider("other")})

    infos = await _changed_under(
        video_gen.list_all_providers_info(),
        held,
        lambda: video_gen.register_provider(_Provider("added")),  # type: ignore[arg-type]
    )

    assert [info["name"] for info in infos] == ["held", "other"], infos


@pytest.mark.asyncio
async def test_a_knowledge_source_removed_while_one_is_searched(monkeypatch) -> None:
    held = _Provider("held", held=True)
    monkeypatch.setattr(knowledge, "_providers", {"held": held, "other": _Provider("other")})

    items = await _changed_under(
        knowledge.search_all("notes"), held, lambda: knowledge.unregister_provider("other")
    )

    assert [item.id for item in items] == ["held:notes", "other:notes"], items


@pytest.mark.asyncio
async def test_a_task_provider_added_while_one_is_asked_for_the_task(monkeypatch) -> None:
    held = _Provider("native", held=True)
    holder = _Provider("other", holds_task="t-1")
    monkeypatch.setattr(tasks, "_providers", {"native": held, "other": holder})

    routed = await _changed_under(
        tasks._routed("t-1", None),
        held,
        lambda: tasks.register_provider(_Provider("added")),  # type: ignore[arg-type]
    )

    assert routed is holder, routed
