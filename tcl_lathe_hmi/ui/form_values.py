from __future__ import annotations


def parse_number(value: str, default: float) -> float:
    try:
        return float(value)
    except ValueError:
        return default


def optional_int(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    return int(float(text))
