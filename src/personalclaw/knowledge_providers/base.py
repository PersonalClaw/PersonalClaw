"""Abstract base for Knowledge providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KnowledgeSource:
    id: str
    name: str
    source_type: str = ""
    item_count: int = 0
    provider: str = ""


@dataclass
class KnowledgeItem:
    id: str
    title: str
    content: str = ""
    source_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class KnowledgeProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        ...

    @abstractmethod
    async def list_sources(self) -> list[KnowledgeSource]:
        ...

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
        ...

    @abstractmethod
    async def get_item(self, item_id: str) -> KnowledgeItem | None:
        ...

    async def ingest(self, source_id: str, content: str, title: str = "", metadata: dict[str, Any] | None = None) -> KnowledgeItem | None:
        return None

    async def delete_item(self, item_id: str) -> bool:
        return False

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}
