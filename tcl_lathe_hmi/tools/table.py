from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path


MAX_TOOL_NUMBER = 12
TURRET_STATIONS = 8
REFERENCE_TOOL_NUMBER = 1

WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")


@dataclass(frozen=True)
class ToolRecord:
    tool_number: int
    x_offset_mm: float = 0.0
    z_offset_mm: float = 0.0
    description: str = ""

    @property
    def display_name(self) -> str:
        return f"T{self.tool_number}"


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

    def update_description(self, tool_number: int, description: str) -> ToolRecord:
        tool = self.ensure_tool(tool_number)
        updated = replace(tool, description=description.strip())
        self.upsert(updated)
        return updated

    def to_json(self) -> list[dict[str, object]]:
        return [
            {
                "tool_number": tool.tool_number,
                "x_offset_mm": tool.x_offset_mm,
                "z_offset_mm": tool.z_offset_mm,
                "description": tool.description,
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
            table.upsert(
                ToolRecord(
                    tool_number=int(item["tool_number"]),
                    x_offset_mm=float(item.get("x_offset_mm", 0.0)),
                    z_offset_mm=float(item.get("z_offset_mm", 0.0)),
                    description=str(item.get("description", "")).strip(),
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
        ToolRecord(tool_number=1, description="turning rough/finish"),
        ToolRecord(tool_number=2, description="centre drill"),
        ToolRecord(tool_number=3, description="6mm drill"),
        ToolRecord(tool_number=4, description="boring bar"),
        ToolRecord(tool_number=5, description="parting tool"),
        ToolRecord(tool_number=6, description="external thread"),
        ToolRecord(tool_number=7, description="internal thread"),
        ToolRecord(tool_number=8, description="spare turret station"),
        ToolRecord(tool_number=9, description="manual 8mm drill"),
        ToolRecord(tool_number=10, description="manual 10mm drill"),
        ToolRecord(tool_number=11, description="manual tap"),
        ToolRecord(tool_number=12, description="manual special tool"),
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
            setup.table.upsert(record)
            if station is not None and 1 <= station <= TURRET_STATIONS:
                setup.turret.assign(record.tool_number, station)
    return setup


def parse_legacy_linuxcnc_tool_line(
    line: str,
    *,
    line_number: int = 0,
) -> tuple[ToolRecord | None, int | None]:
    content, description = _split_comment(line)
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
            description=description.strip(),
        ),
        int(words["P"]) if "P" in words else None,
    )


def _split_comment(line: str) -> tuple[str, str]:
    if ";" not in line:
        return line, ""
    content, comment = line.split(";", 1)
    return content, comment


def _validate_tool_number(tool_number: int) -> None:
    if not 1 <= tool_number <= MAX_TOOL_NUMBER:
        raise ValueError(f"tool number must be in range 1..{MAX_TOOL_NUMBER}")


def _validate_station(station: int) -> None:
    if not 1 <= station <= TURRET_STATIONS:
        raise ValueError(f"station must be in range 1..{TURRET_STATIONS}")
