"""Recursive JSON and opaque version value types used by domain models."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from typing import cast

from portable_memory_engine.domain.errors import DomainValidationError

type JsonScalar = bool | int | float | str | None
type JsonValue = JsonScalar | tuple[JsonValue, ...] | FrozenJsonObject
type VersionToken = str | int


class FrozenJsonObject(Mapping[str, JsonValue]):
    """A deeply immutable, hashable JSON object with deterministic key order."""

    __slots__ = ("_items",)

    def __init__(self, value: Mapping[str, object] | None = None) -> None:
        if value is None:
            source: Mapping[str, object] = {}
        elif isinstance(value, Mapping):
            source = value
        else:
            raise DomainValidationError("JSON objects must be mappings")
        frozen_items: list[tuple[str, JsonValue]] = []
        normalized_keys: set[str] = set()
        for key, item in source.items():
            normalized_key = _normalize_json_key(key)
            if normalized_key in normalized_keys:
                raise DomainValidationError("JSON object keys must be unique after normalization")
            normalized_keys.add(normalized_key)
            frozen_items.append((normalized_key, freeze_json(item)))
        frozen_items.sort(key=lambda pair: pair[0])
        self._items = tuple(frozen_items)

    def __getitem__(self, key: str) -> JsonValue:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(self._items)

    def __repr__(self) -> str:
        return f"FrozenJsonObject(keys={len(self._items)})"


def _normalize_json_key(value: object) -> str:
    if not isinstance(value, str):
        raise DomainValidationError("JSON object keys must be strings")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized:
        raise DomainValidationError("JSON object keys must not be empty")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise DomainValidationError("JSON object keys must not contain control characters")
    return normalized


def freeze_json(value: object) -> JsonValue:
    """Validate and deeply freeze one JSON-compatible value."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DomainValidationError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        return FrozenJsonObject(cast("Mapping[str, object]", value))
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in cast("Sequence[object]", value))
    raise DomainValidationError("metadata values must be JSON-compatible")
