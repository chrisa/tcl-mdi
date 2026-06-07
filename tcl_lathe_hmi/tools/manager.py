from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .table import (
    ToolRecord,
    ToolSetup,
    ToolTable,
    Turret,
    sample_tool_setup,
    setup_from_legacy_linuxcnc,
)


class ToolManager:
    def __init__(
        self,
        *,
        path: str | Path | None = None,
        legacy_path: str | Path | None = None,
        setup: ToolSetup | None = None,
    ):
        self.path = Path(path).expanduser() if path is not None else None
        self.legacy_path = Path(legacy_path).expanduser() if legacy_path is not None else None
        self.setup = setup or sample_tool_setup()

    @property
    def tool_table(self) -> ToolTable:
        return self.setup.table

    @property
    def turret(self) -> Turret:
        return self.setup.turret

    def load(self) -> bool:
        if self.path is not None and self.path.exists():
            self.setup = ToolSetup.load(self.path)
            return True

        if self.legacy_path is not None and self.legacy_path.exists():
            self.setup = setup_from_legacy_linuxcnc(self.legacy_path.read_text())
            self.save()
            return True

        return False

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.setup.save(self.path)

    def get_tool(self, tool_number: int) -> ToolRecord | None:
        return self.tool_table.get(tool_number)

    def require_tool(self, tool_number: int) -> ToolRecord:
        return self.tool_table.ensure_tool(tool_number)

    def upsert_tool(self, tool: ToolRecord) -> ToolRecord:
        self.tool_table.upsert(tool)
        self.save()
        return tool

    def assign_tool_station(self, tool_number: int, station: int | None) -> None:
        self.require_tool(tool_number)
        self.turret.assign(tool_number, station)
        self.save()

    def clear_station(self, station: int) -> None:
        self.turret.assign(None, station)
        self.save()

    def set_tool_offsets(
        self,
        tool_number: int,
        *,
        x_offset_mm: float | None = None,
        z_offset_mm: float | None = None,
    ) -> ToolRecord:
        tool = self.tool_table.update_offsets(
            tool_number,
            x_offset_mm=x_offset_mm,
            z_offset_mm=z_offset_mm,
        )
        self.save()
        return tool

    def update_tool_description(self, tool_number: int, description: str) -> ToolRecord:
        tool = self.tool_table.update_description(tool_number, description)
        self.save()
        return tool

    def update_tool(
        self,
        tool_number: int,
        *,
        x_offset_mm: float,
        z_offset_mm: float,
        description: str,
        station: int | None,
    ) -> ToolRecord:
        current = self.require_tool(tool_number)
        updated = replace(
            current,
            x_offset_mm=x_offset_mm,
            z_offset_mm=z_offset_mm,
            description=description.strip(),
        )
        self.tool_table.upsert(updated)
        self.turret.assign(tool_number, station)
        self.save()
        return updated

    def station_for_tool(self, tool_number: int) -> int | None:
        self.require_tool(tool_number)
        return self.turret.station_for_tool(tool_number)

    def tool_for_station(self, station: int) -> int | None:
        return self.turret.tool_for_station(station)
