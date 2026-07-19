import importlib.metadata
import logging
from typing import Type, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


def discover_providers(group: str, base_class: Type[T] = object) -> dict[str, Type[T]]:
    """Load entry points in *group*; return {name: class} for valid providers."""
    providers: dict[str, Type[T]] = {}
    eps = importlib.metadata.entry_points(group=group)
    for ep in eps:
        try:
            mod = ep.load()
            cls = getattr(mod, "Provider", None)
            if cls is None:
                cls_name = "".join(w.capitalize() for w in ep.name.split("_")) + "Provider"
                cls = getattr(mod, cls_name, None)
            if cls is not None and (base_class is object or issubclass(cls, base_class)):
                providers[ep.name] = cls
        except Exception:
            logger.warning("Failed to load %s provider %r", group, ep.name, exc_info=True)
    return providers
