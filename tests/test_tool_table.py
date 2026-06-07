from __future__ import annotations

import json

from tcl_lathe_hmi.tools import (
    ToolManager,
    ToolRecord,
    ToolSetup,
    sample_tool_setup,
    sample_tool_table,
    sample_turret,
)
from tcl_lathe_hmi.tools.table import setup_from_legacy_linuxcnc


def test_sample_tool_setup_has_twelve_tools_and_eight_turret_stations():
    setup = sample_tool_setup()

    assert [tool.tool_number for tool in setup.table.tools] == list(range(1, 13))
    assert [setup.turret.station_for_tool(tool_number) for tool_number in range(1, 9)] == list(
        range(1, 9)
    )
    assert [setup.turret.station_for_tool(tool_number) for tool_number in range(9, 13)] == [
        None
    ] * 4


def test_tool_setup_json_round_trips_offsets_descriptions_and_turret_assignments(tmp_path):
    path = tmp_path / "tools.json"
    setup = ToolSetup(table=sample_tool_table(), turret=sample_turret())
    setup.table.upsert(
        ToolRecord(
            tool_number=4,
            x_offset_mm=-1.25,
            z_offset_mm=3.5,
            description="boring bar",
        )
    )
    setup.turret.assign(4, 2)

    setup.save(path)
    restored = ToolSetup.load(path)

    assert json.loads(path.read_text())["version"] == 1
    assert restored.table.get(4) == ToolRecord(
        tool_number=4,
        x_offset_mm=-1.25,
        z_offset_mm=3.5,
        description="boring bar",
    )
    assert restored.turret.station_for_tool(4) == 2
    assert restored.turret.tool_for_station(2) == 4


def test_turret_assignment_keeps_one_tool_per_station():
    turret = sample_turret()

    turret.assign(4, 2)

    assert turret.station_for_tool(2) is None
    assert turret.station_for_tool(4) == 2
    assert turret.tool_for_station(4) is None
    assert turret.tool_for_station(2) == 4


def test_tool_manager_saves_assignment_and_offset_edits(tmp_path):
    path = tmp_path / "tools.json"
    manager = ToolManager(path=path)

    manager.update_tool(
        4,
        station=2,
        x_offset_mm=-0.1,
        z_offset_mm=1.25,
        description="finish",
    )

    restored = ToolManager(path=path)
    assert restored.load()
    assert restored.get_tool(4) == ToolRecord(
        tool_number=4,
        x_offset_mm=-0.1,
        z_offset_mm=1.25,
        description="finish",
    )
    assert restored.station_for_tool(4) == 2


def test_legacy_linuxcnc_table_migrates_core_tool_setup():
    setup = setup_from_legacy_linuxcnc(
        "T4 P2 X-1.25 Y0.0 Z3.5 A0.0 B0.0 C0.0 U0.0 V0.0 W0.0 "
        "D0.4 I94.0 J154.0 Q1.0 ;boring bar\n"
    )

    assert setup.table.get(4) == ToolRecord(
        tool_number=4,
        x_offset_mm=-1.25,
        z_offset_mm=3.5,
        description="boring bar",
    )
    assert setup.turret.station_for_tool(4) == 2
