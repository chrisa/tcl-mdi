from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path


MAX_TOOL_NUMBER = 12
TURRET_STATIONS = 8
REFERENCE_TOOL_NUMBER = 1

TOOL_TYPE_UNSPECIFIED = "unspecified"
TOOL_TYPE_TURNING_LH = "turning_lh"
TOOL_TYPE_TURNING_RH = "turning_rh"
TOOL_TYPE_TURNING_NEUTRAL = "turning_neutral"
TOOL_TYPE_EXTERNAL_THREAD = "external_thread"
TOOL_TYPE_CENTRE_DRILL = "centre_drill"
TOOL_TYPE_DRILL = "drill"
TOOL_TYPE_BORING_BAR = "boring_bar"
TOOL_TYPE_INTERNAL_THREAD = "internal_thread"
TOOL_TYPE_PARTING_REAR = "parting_rear"
TOOL_TYPE_PARTING_FRONT = "parting_front"

TOOL_TYPES = (
    TOOL_TYPE_UNSPECIFIED,
    TOOL_TYPE_TURNING_LH,
    TOOL_TYPE_TURNING_RH,
    TOOL_TYPE_TURNING_NEUTRAL,
    TOOL_TYPE_EXTERNAL_THREAD,
    TOOL_TYPE_CENTRE_DRILL,
    TOOL_TYPE_DRILL,
    TOOL_TYPE_BORING_BAR,
    TOOL_TYPE_INTERNAL_THREAD,
    TOOL_TYPE_PARTING_REAR,
    TOOL_TYPE_PARTING_FRONT,
)

TOOL_TYPE_LABELS = {
    TOOL_TYPE_UNSPECIFIED: "Unspecified",
    TOOL_TYPE_TURNING_LH: "55 LH Copy",
    TOOL_TYPE_TURNING_RH: "55 RH Copy",
    TOOL_TYPE_TURNING_NEUTRAL: "Neutral Copy",
    TOOL_TYPE_EXTERNAL_THREAD: "External Thread",
    TOOL_TYPE_CENTRE_DRILL: "Centre Drill",
    TOOL_TYPE_DRILL: "Drill",
    TOOL_TYPE_BORING_BAR: "Boring Bar",
    TOOL_TYPE_INTERNAL_THREAD: "Internal Thread",
    TOOL_TYPE_PARTING_REAR: "Parting Rear",
    TOOL_TYPE_PARTING_FRONT: "Parting Front",
}

TURNING_TOOL_TYPES = (
    TOOL_TYPE_TURNING_LH,
    TOOL_TYPE_TURNING_RH,
    TOOL_TYPE_TURNING_NEUTRAL,
)

WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")


@dataclass(frozen=True)
class ToolRecord:
    tool_number: int
    x_offset_mm: float = 0.0
    z_offset_mm: float = 0.0
    tool_type: str = TOOL_TYPE_UNSPECIFIED
    nominal_size_mm: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_type", normalize_tool_type(self.tool_type))
        if self.nominal_size_mm is not None and self.nominal_size_mm <= 0.0:
            object.__setattr__(self, "nominal_size_mm", None)

    @property
    def display_name(self) -> str:
        return f"T{self.tool_number}"

    @property
    def type_label(self) -> str:
        return tool_type_label(self.tool_type)


class ToolTable:
    def __init__(self, tools: list[ToolRecord] | None = None):
        self._tools: dict[int, ToolRecord] = {
            tool_number: ToolRecord(tool_number=tool_number)
            for tool_number in range(1, MAX_TOOL_NUMBER + 1)
        }
        for tool in tools or []:
            self.upsert(tool)

    @property
    def tools(self) -> list[ToolRecord]:
        return [self._tools[key] for key in sorted(self._tools)]

    def get(self, tool_number: int) -> ToolRecord | None:
        return self._tools.get(tool_number)

    def ensure_tool(self, tool_number: int) -> ToolRecord:
        tool = self.get(tool_number)
        if tool is None:
            raise ValueError(f"tool number must be in range 1..{MAX_TOOL_NUMBER}")
        return tool

    def upsert(self, tool: ToolRecord) -> None:
        _validate_tool_number(tool.tool_number)
        self._tools[tool.tool_number] = tool

    def update_offsets(
        self,
        tool_number: int,
        *,
        x_offset_mm: float | None = None,
        z_offset_mm: float | None = None,
    ) -> ToolRecord:
        tool = self.ensure_tool(tool_number)
        updated = replace(
            tool,
            x_offset_mm=tool.x_offset_mm if x_offset_mm is None else x_offset_mm,
            z_offset_mm=tool.z_offset_mm if z_offset_mm is None else z_offset_mm,
        )
        self.upsert(updated)
        return updated

    def find_by_type(
        self,
        tool_type: str,
        *,
        nominal_size_mm: float | None = None,
    ) -> ToolRecord | None:
        normalized = normalize_tool_type(tool_type)
        candidates = [tool for tool in self.tools if tool.tool_type == normalized]
        if nominal_size_mm is None:
            return candidates[0] if candidates else None

        for tool in candidates:
            if (
                tool.nominal_size_mm is not None
                and abs(tool.nominal_size_mm - nominal_size_mm) < 1e-6
            ):
                return tool
        return None

    def first_by_types(
        self,
        tool_types: Iterable[str],
        *,
        nominal_size_mm: float | None = None,
    ) -> ToolRecord | None:
        normalized_types = tuple(normalize_tool_type(tool_type) for tool_type in tool_types)
        if nominal_size_mm is not None:
            for tool_type in normalized_types:
                tool = self.find_by_type(tool_type, nominal_size_mm=nominal_size_mm)
                if tool is not None:
                    return tool

        for tool_type in normalized_types:
            tool = self.find_by_type(tool_type)
            if tool is not None:
                return tool
        return None

    def to_json(self) -> list[dict[str, object]]:
        return [
            {
                "tool_number": tool.tool_number,
                "x_offset_mm": tool.x_offset_mm,
                "z_offset_mm": tool.z_offset_mm,
                "tool_type": tool.tool_type,
                "nominal_size_mm": tool.nominal_size_mm,
            }
            for tool in self.tools
        ]

    @classmethod
    def from_json(cls, data: object) -> "ToolTable":
        if not isinstance(data, list):
            raise ValueError("tools must be a list")
        table = cls(sample_tool_records())
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("tool entries must be objects")
            tool_number = int(item["tool_number"])
            fallback = table.get(tool_number)
            fallback_tool_type = (
                fallback.tool_type if fallback is not None else TOOL_TYPE_UNSPECIFIED
            )
            table.upsert(
                ToolRecord(
                    tool_number=tool_number,
                    x_offset_mm=float(item.get("x_offset_mm", 0.0)),
                    z_offset_mm=float(item.get("z_offset_mm", 0.0)),
                    tool_type=_optional_tool_type(item.get("tool_type"), fallback_tool_type),
                    nominal_size_mm=_optional_float(
                        item.get(
                            "nominal_size_mm",
                            fallback.nominal_size_mm if fallback is not None else None,
                        )
                    ),
                )
            )
        return table


@dataclass(frozen=True)
class TurretStation:
    station: int
    tool_number: int | None = None


class Turret:
    def __init__(self, stations: dict[int, int | None] | None = None):
        self._stations: dict[int, int | None] = {
            station: None for station in range(1, TURRET_STATIONS + 1)
        }
        for station, tool_number in (stations or {}).items():
            self.assign(tool_number, int(station))

    @property
    def stations(self) -> list[TurretStation]:
        return [
            TurretStation(station=station, tool_number=self._stations[station])
            for station in sorted(self._stations)
        ]

    def tool_for_station(self, station: int) -> int | None:
        _validate_station(station)
        return self._stations[station]

    def station_for_tool(self, tool_number: int) -> int | None:
        _validate_tool_number(tool_number)
        for station, assigned_tool in self._stations.items():
            if assigned_tool == tool_number:
                return station
        return None

    def assign(self, tool_number: int | None, station: int | None) -> None:
        if tool_number is None:
            if station is None:
                return
            _validate_station(station)
            self._stations[station] = None
            return

        _validate_tool_number(tool_number)
        if station is not None:
            _validate_station(station)

        for current_station, assigned_tool in list(self._stations.items()):
            if assigned_tool == tool_number:
                self._stations[current_station] = None

        if station is not None:
            self._stations[station] = tool_number

    def to_json(self) -> dict[str, int | None]:
        return {str(station): tool_number for station, tool_number in sorted(self._stations.items())}

    @classmethod
    def from_json(cls, data: object) -> "Turret":
        if isinstance(data, dict) and isinstance(data.get("stations"), dict):
            data = data["stations"]
        if not isinstance(data, dict):
            raise ValueError("turret stations must be an object")
        stations: dict[int, int | None] = {}
        for key, value in data.items():
            station = int(key)
            stations[station] = None if value is None else int(value)
        return cls(stations)


@dataclass
class ToolSetup:
    table: ToolTable
    turret: Turret

    def to_json(self) -> dict[str, object]:
        return {
            "version": 1,
            "tools": self.table.to_json(),
            "turret": {"stations": self.turret.to_json()},
        }

    @classmethod
    def from_json(cls, data: object) -> "ToolSetup":
        if not isinstance(data, dict):
            raise ValueError("tool setup must be an object")
        return cls(
            table=ToolTable.from_json(data.get("tools", [])),
            turret=Turret.from_json(data.get("turret", {})),
        )

    @classmethod
    def load(cls, path: str | Path) -> "ToolSetup":
        return cls.from_json(json.loads(Path(path).expanduser().read_text()))

    def save(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        target.write_text(json.dumps(self.to_json(), indent=2, sort_keys=True) + "\n")


def sample_tool_records() -> list[ToolRecord]:
    return [
        ToolRecord(tool_number=1, tool_type=TOOL_TYPE_TURNING_LH),
        ToolRecord(tool_number=2, tool_type=TOOL_TYPE_TURNING_RH),
        ToolRecord(tool_number=3, tool_type=TOOL_TYPE_TURNING_NEUTRAL),
        ToolRecord(tool_number=4, tool_type=TOOL_TYPE_EXTERNAL_THREAD),
        ToolRecord(tool_number=5, tool_type=TOOL_TYPE_CENTRE_DRILL),
        ToolRecord(
            tool_number=6,
            tool_type=TOOL_TYPE_DRILL,
            nominal_size_mm=5.0,
        ),
        ToolRecord(
            tool_number=7,
            tool_type=TOOL_TYPE_DRILL,
            nominal_size_mm=7.0,
        ),
        ToolRecord(
            tool_number=8,
            tool_type=TOOL_TYPE_DRILL,
            nominal_size_mm=10.0,
        ),
        ToolRecord(tool_number=9, tool_type=TOOL_TYPE_BORING_BAR, nominal_size_mm=14.0),
        ToolRecord(tool_number=10, tool_type=TOOL_TYPE_INTERNAL_THREAD),
        ToolRecord(tool_number=11, tool_type=TOOL_TYPE_PARTING_REAR),
        ToolRecord(tool_number=12, tool_type=TOOL_TYPE_PARTING_FRONT),
    ]


def sample_tool_table() -> ToolTable:
    return ToolTable(sample_tool_records())


def sample_turret() -> Turret:
    return Turret({station: station for station in range(1, TURRET_STATIONS + 1)})


def sample_tool_setup() -> ToolSetup:
    return ToolSetup(table=sample_tool_table(), turret=sample_turret())


def setup_from_legacy_linuxcnc(text: str) -> ToolSetup:
    setup = sample_tool_setup()
    for line_number, line in enumerate(text.splitlines(), start=1):
        record, station = parse_legacy_linuxcnc_tool_line(line, line_number=line_number)
        if record is None:
            continue
        if 1 <= record.tool_number <= MAX_TOOL_NUMBER:
            fallback = setup.table.get(record.tool_number)
            if fallback is not None:
                record = replace(
                    record,
                    tool_type=(
                        fallback.tool_type
                        if record.tool_type == TOOL_TYPE_UNSPECIFIED
                        else record.tool_type
                    ),
                    nominal_size_mm=(
                        fallback.nominal_size_mm
                        if record.nominal_size_mm is None
                        else record.nominal_size_mm
                    ),
                )
            setup.table.upsert(record)
            if station is not None and 1 <= station <= TURRET_STATIONS:
                setup.turret.assign(record.tool_number, station)
    return setup


def parse_legacy_linuxcnc_tool_line(
    line: str,
    *,
    line_number: int = 0,
) -> tuple[ToolRecord | None, int | None]:
    content, _comment = _split_comment(line)
    content = content.strip()
    if not content:
        return None, None

    words: dict[str, float] = {}
    pos = 0
    while pos < len(content):
        while pos < len(content) and content[pos].isspace():
            pos += 1
        if pos >= len(content):
            break
        match = WORD_RE.match(content, pos)
        if match is None:
            label = f"line {line_number}: " if line_number else ""
            raise ValueError(f"{label}could not parse tool table entry: {line!r}")
        words[match.group(1).upper()] = float(match.group(2))
        pos = match.end()

    if "T" not in words:
        label = f"line {line_number}: " if line_number else ""
        raise ValueError(f"{label}tool table entry missing T word")

    return (
        ToolRecord(
            tool_number=int(words["T"]),
            x_offset_mm=float(words.get("X", 0.0)),
            z_offset_mm=float(words.get("Z", 0.0)),
        ),
        int(words["P"]) if "P" in words else None,
    )


def _split_comment(line: str) -> tuple[str, str]:
    if ";" not in line:
        return line, ""
    content, comment = line.split(";", 1)
    return content, comment


def normalize_tool_type(tool_type: str) -> str:
    normalized = tool_type.strip().lower().replace(" ", "_")
    return normalized or TOOL_TYPE_UNSPECIFIED


def tool_type_label(tool_type: str) -> str:
    return TOOL_TYPE_LABELS.get(normalize_tool_type(tool_type), tool_type.strip() or "Unspecified")


def tool_type_from_label(label: str) -> str:
    stripped = label.strip()
    for tool_type, tool_label in TOOL_TYPE_LABELS.items():
        if stripped == tool_label:
            return tool_type
    return normalize_tool_type(stripped)


def _optional_tool_type(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    return str(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def _validate_tool_number(tool_number: int) -> None:
    if not 1 <= tool_number <= MAX_TOOL_NUMBER:
        raise ValueError(f"tool number must be in range 1..{MAX_TOOL_NUMBER}")


def _validate_station(station: int) -> None:
    if not 1 <= station <= TURRET_STATIONS:
        raise ValueError(f"station must be in range 1..{TURRET_STATIONS}")
