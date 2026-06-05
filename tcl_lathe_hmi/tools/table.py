from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path


WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")


@dataclass(frozen=True)
class ToolRecord:
    tool_number: int
    station: int | None = None
    x_offset_mm: float = 0.0
    z_offset_mm: float = 0.0
    diameter_mm: float = 0.0
    front_angle_deg: float | None = None
    back_angle_deg: float | None = None
    orientation: int | None = None
    comment: str = ""

    @property
    def display_name(self) -> str:
        suffix = f" P{self.station}" if self.station is not None else ""
        return f"T{self.tool_number}{suffix}"

    def to_linuxcnc_line(self) -> str:
        parts = [
            f"T{self.tool_number}",
            f"X{self.x_offset_mm:.6g}",
            "Y0.0",
            f"Z{self.z_offset_mm:.6g}",
            "A0.0",
            "B0.0",
            "C0.0",
            "U0.0",
            "V0.0",
            "W0.0",
            f"D{self.diameter_mm:.6g}",
        ]
        if self.station is not None:
            parts.insert(1, f"P{self.station}")
        if self.front_angle_deg is not None:
            parts.append(f"I{self.front_angle_deg:.6g}")
        if self.back_angle_deg is not None:
            parts.append(f"J{self.back_angle_deg:.6g}")
        if self.orientation is not None:
            parts.append(f"Q{self.orientation}")
        line = " ".join(parts)
        if self.comment:
            line += f" ;{self.comment}"
        return line


class ToolTable:
    def __init__(self, tools: list[ToolRecord] | None = None):
        self._tools: dict[int, ToolRecord] = {}
        for tool in tools or []:
            self.upsert(tool)

    @property
    def tools(self) -> list[ToolRecord]:
        return [self._tools[key] for key in sorted(self._tools)]

    def get(self, tool_number: int) -> ToolRecord | None:
        return self._tools.get(tool_number)

    def upsert(self, tool: ToolRecord) -> None:
        if tool.tool_number < 0:
            raise ValueError("tool_number must be non-negative")
        if tool.station is not None and tool.station < 0:
            raise ValueError("station must be non-negative")
        self._tools[tool.tool_number] = tool

    def remove(self, tool_number: int) -> None:
        self._tools.pop(tool_number, None)

    def ensure_tool(self, tool_number: int, station: int | None = None) -> ToolRecord:
        existing = self.get(tool_number)
        if existing is not None:
            if station is not None and existing.station is None:
                existing = replace(existing, station=station)
                self.upsert(existing)
            return existing
        tool = ToolRecord(tool_number=tool_number, station=station)
        self.upsert(tool)
        return tool

    def import_linuxcnc(self, text: str) -> None:
        self._tools.clear()
        for line_number, line in enumerate(text.splitlines(), start=1):
            record = parse_linuxcnc_tool_line(line, line_number=line_number)
            if record is not None:
                self.upsert(record)

    def export_linuxcnc(self) -> str:
        lines = [tool.to_linuxcnc_line() for tool in self.tools]
        return "\n".join(lines) + ("\n" if lines else "")

    @classmethod
    def from_linuxcnc(cls, text: str) -> "ToolTable":
        table = cls()
        table.import_linuxcnc(text)
        return table

    @classmethod
    def load(cls, path: str | Path) -> "ToolTable":
        return cls.from_linuxcnc(Path(path).expanduser().read_text())

    def save(self, path: str | Path) -> None:
        Path(path).expanduser().write_text(self.export_linuxcnc())


def parse_linuxcnc_tool_line(line: str, *, line_number: int = 0) -> ToolRecord | None:
    content, comment = _split_comment(line)
    content = content.strip()
    if not content:
        return None

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

    return ToolRecord(
        tool_number=int(words["T"]),
        station=int(words["P"]) if "P" in words else None,
        x_offset_mm=float(words.get("X", 0.0)),
        z_offset_mm=float(words.get("Z", 0.0)),
        diameter_mm=float(words.get("D", 0.0)),
        front_angle_deg=float(words["I"]) if "I" in words else None,
        back_angle_deg=float(words["J"]) if "J" in words else None,
        orientation=int(words["Q"]) if "Q" in words else None,
        comment=comment.strip(),
    )


def _split_comment(line: str) -> tuple[str, str]:
    if ";" not in line:
        return line, ""
    content, comment = line.split(";", 1)
    return content, comment


def sample_tool_table() -> ToolTable:
    return ToolTable(
        [
            ToolRecord(tool_number=1, station=1, comment="turning rough/finish"),
            ToolRecord(tool_number=2, station=2, diameter_mm=3.0, comment="centre drill"),
            ToolRecord(tool_number=3, station=3, diameter_mm=6.0, comment="6mm drill"),
            ToolRecord(tool_number=4, station=4, diameter_mm=10.0, comment="boring bar"),
            ToolRecord(tool_number=5, station=5, comment="parting tool"),
            ToolRecord(tool_number=6, station=6, comment="external thread"),
            ToolRecord(tool_number=7, station=7, comment="internal thread"),
            ToolRecord(tool_number=8, station=8, comment="spare turret station"),
            ToolRecord(tool_number=9, diameter_mm=8.0, comment="manual 8mm drill"),
            ToolRecord(tool_number=10, diameter_mm=10.0, comment="manual 10mm drill"),
            ToolRecord(tool_number=11, comment="manual tap"),
            ToolRecord(tool_number=12, comment="manual special tool"),
        ]
    )
