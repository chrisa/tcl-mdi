from __future__ import annotations

from tcl_lathe_hmi.tools import ToolRecord, ToolTable, sample_tool_table


def test_import_linuxcnc_tool_table_keeps_tool_and_station_separate():
    table = ToolTable.from_linuxcnc(
        "T4 P2 X-1.25 Y0.0 Z3.5 A0.0 B0.0 C0.0 U0.0 V0.0 W0.0 "
        "D0.4 I94.0 J154.0 Q1.0 ;boring bar\n"
    )

    tool = table.get(4)

    assert tool is not None
    assert tool.tool_number == 4
    assert tool.station == 2
    assert tool.x_offset_mm == -1.25
    assert tool.z_offset_mm == 3.5
    assert tool.diameter_mm == 0.4
    assert tool.front_angle_deg == 94.0
    assert tool.back_angle_deg == 154.0
    assert tool.orientation == 1
    assert tool.comment == "boring bar"


def test_export_linuxcnc_tool_table_is_reimportable():
    table = ToolTable(
        [
            ToolRecord(
                tool_number=2,
                station=5,
                x_offset_mm=-0.1,
                z_offset_mm=1.25,
                diameter_mm=0.2,
                comment="finish",
            )
        ]
    )

    exported = table.export_linuxcnc()
    imported = ToolTable.from_linuxcnc(exported)

    assert imported.get(2) == table.get(2)


def test_export_preserves_tools_without_turret_station():
    table = ToolTable([ToolRecord(tool_number=9, station=None, comment="manual drill")])

    exported = table.export_linuxcnc()
    imported = ToolTable.from_linuxcnc(exported)

    assert "P9" not in exported
    assert imported.get(9) == table.get(9)


def test_sample_tool_table_has_twelve_tools_and_eight_turret_stations():
    table = sample_tool_table()

    assert [tool.tool_number for tool in table.tools] == list(range(1, 13))
    assert [table.get(tool_number).station for tool_number in range(1, 9)] == list(range(1, 9))
    assert [table.get(tool_number).station for tool_number in range(9, 13)] == [None] * 4
