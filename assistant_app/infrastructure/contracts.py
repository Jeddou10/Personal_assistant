"""Callable contracts for injected service dependencies."""

from collections.abc import Callable
from typing import Any, Protocol

from assistant_app.domain.models import JsonObject
from assistant_app.domain.ports import StorePort


class Request(Protocol):
    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: Any = None,
        timeout: float = 30,
        local: bool = False,
        form: bool = False,
    ) -> JsonObject: ...


TokenProvider = Callable[[StorePort], str]
