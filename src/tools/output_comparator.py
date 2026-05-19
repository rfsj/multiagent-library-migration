from __future__ import annotations

from typing import Any


def normalize_records(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dicts"):
        return value.to_dicts()
    if hasattr(value, "to_dict"):
        return value.to_dict(orient="records")
    return value
