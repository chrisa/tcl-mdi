from __future__ import annotations

import json

from tcl_lathe_hmi.tools import (
    TOOL_TYPE_BORING_BAR,
    TOOL_TYPE_DRILL,
    TOOL_TYPE_EXTERNAL_THREAD,
    TOOL_TYPE_TURNING_LH,
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
    assert setup.table.get(1).tool_type == TOOL_TYPE_TURNING_LH
    assert setup.table.get(4).tool_type == TOOL_TYPE_EXTERNAL_THREAD
    assert setup.table.get(6).tool_type == TOOL_TYPE_DRILL
    assert setup.table.get(6).nominal_size_mm == 5.0
    assert setup.table.get(9).tool_type == TOOL_TYPE_BORING_BAR
    assert [setup.turret.station_for_tool(tool_number) for tool_number in range(1, 9)] == list(
        range(1, 9)
    )
    assert [setup.turret.station_for_tool(tool_number) for tool_number in range(9, 13)] == [
        None
    ] * 4


def test_tool_setup_json_round_trips_offsets_types_sizes_and_turret_assignments(tmp_path):
    path = tmp_path / "tools.json"
    setup = ToolSetup(table=sample_tool_table(), turret=sample_turret())
    setup.table.upsert(
        ToolRecord(
            tool_number=4,
            x_offset_mm=-1.25,
            z_offset_mm=3.5,
            tool_type=TOOL_TYPE_BORING_BAR,
            nominal_size_mm=14.0,
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
        tool_type=TOOL_TYPE_BORING_BAR,
        nominal_size_mm=14.0,
    )
    assert restored.turret.station_for_tool(4) == 2
    assert restored.turret.tool_for_station(2) == 4


def test_tool_setup_json_migrates_missing_tool_type_from_sample_defaults(tmp_path):
    path = tmp_path / "tools.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": [
                    {
                        "tool_number": 6,
                        "x_offset_mm": 0.25,
                        "z_offset_mm": -0.5,
                    }
                ],
                "turret": {"stations": {"1": 1}},
            }
        )
    )

    restored = ToolSetup.load(path)

    assert restored.table.get(6) == ToolRecord(
        tool_number=6,
        x_offset_mm=0.25,
        z_offset_mm=-0.5,
        tool_type=TOOL_TYPE_DRILL,
        nominal_size_mm=5.0,
    )


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
        tool_type=TOOL_TYPE_EXTERNAL_THREAD,
        nominal_size_mm=None,
    )

    restored = ToolManager(path=path)
    assert restored.load()
    assert restored.get_tool(4) == ToolRecord(
        tool_number=4,
        x_offset_mm=-0.1,
        z_offset_mm=1.25,
        tool_type=TOOL_TYPE_EXTERNAL_THREAD,
    )
    assert restored.station_for_tool(4) == 2


def test_tool_manager_finds_tools_by_type_and_nominal_size():
    manager = ToolManager()

    assert manager.first_tool_by_types([TOOL_TYPE_TURNING_LH]).tool_number == 1
    assert manager.find_tool_by_type(TOOL_TYPE_DRILL, nominal_size_mm=7.0).tool_number == 7
    assert manager.find_tool_by_type(TOOL_TYPE_DRILL, nominal_size_mm=6.0) is None


def test_legacy_linuxcnc_table_migrates_core_tool_setup():
    setup = setup_from_legacy_linuxcnc(
        "T9 P2 X-1.25 Y0.0 Z3.5 A0.0 B0.0 C0.0 U0.0 V0.0 W0.0 "
        "D0.4 I94.0 J154.0 Q1.0 ;boring bar\n"
    )

    assert setup.table.get(9) == ToolRecord(
        tool_number=9,
        x_offset_mm=-1.25,
        z_offset_mm=3.5,
        tool_type=TOOL_TYPE_BORING_BAR,
        nominal_size_mm=14.0,
    )
    assert setup.turret.station_for_tool(9) == 2
