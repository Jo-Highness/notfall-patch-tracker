"""Routing-Tabelle rekursiv auflisten.

Ab FastAPI 0.16x werden eingebundene Router nicht mehr flachgeklopft, sondern als
``_IncludedRouter`` gehalten. Der Walker behandelt beide Formen; die Tests
pruefen zusaetzlich, dass bekannte Pfade gefunden werden (sonst waere ein
„keine /admin-Route gefunden" wertlos).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from starlette.routing import BaseRoute, Mount, Route


def iter_routes(routes: Iterable[BaseRoute], prefix: str = "") -> Iterator[tuple[str, set[str]]]:
    for r in routes:
        original: Any = getattr(r, "original_router", None)
        if original is not None:
            ctx = getattr(r, "include_context", None)
            yield from iter_routes(original.routes, prefix + getattr(ctx, "prefix", ""))
        elif isinstance(r, Mount):
            yield prefix + r.path, set()
            yield from iter_routes(r.routes, prefix + r.path)
        elif isinstance(r, Route) or hasattr(r, "path"):
            methods = getattr(r, "methods", None) or set()
            yield prefix + str(getattr(r, "path", "")), set(methods)
