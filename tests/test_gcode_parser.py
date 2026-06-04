from __future__ import annotations

import pytest

from tcl_lathe_hmi.gcode import (
    GCodeParseError,
    MoveAction,
    SpindleAction,
    ToolChangeAction,
    build_preview,
    parse_gcode,
)


def test_parse_supported_lathe_program():
    result = parse_gcode(
        """
        (basic lathe path)
        G21 G90 G18
        S1200 M3
        G0 X1.0 Z0.0
        G1 X1.5 Z-5.0 F100
        M5
        """
    )

    assert len(result.actions) == 4
    assert isinstance(result.actions[0], SpindleAction)
    assert result.actions[0].on
    assert result.actions[0].rpm == 1200

    rapid = result.actions[1]
    assert isinstance(rapid, MoveAction)
    assert rapid.mode == "rapid"
    assert rapid.target_x_mm == 1.0
    assert rapid.target_z_mm == 0.0

    feed = result.actions[2]
    assert isinstance(feed, MoveAction)
    assert feed.mode == "feed"
    assert feed.target_x_mm == 1.5
    assert feed.target_z_mm == -5.0
    assert feed.feed == 100

    assert isinstance(result.actions[3], SpindleAction)
    assert not result.actions[3].on


def test_parse_incremental_and_inches():
    result = parse_gcode("G20 G91 G1 X0.1 Z-0.2 F5", start_x_mm=1.0, start_z_mm=2.0)
    action = result.actions[0]

    assert isinstance(action, MoveAction)
    assert action.target_x_mm == pytest.approx(3.54)
    assert action.target_z_mm == pytest.approx(-3.08)


def test_parse_tool_change_keeps_tool_and_station_separate():
    result = parse_gcode("T4 M6 K2")
    action = result.actions[0]

    assert isinstance(action, ToolChangeAction)
    assert action.tool_number == 4
    assert action.turret_station == 2


def test_parse_rejects_unsupported_gcode_before_execution():
    with pytest.raises(GCodeParseError, match="unsupported G-code"):
        parse_gcode("G2 X1 Z1 I0 K1")


def test_preview_contains_move_segments_only():
    result = parse_gcode("S1000 M3\nG0 X1 Z0\nG1 X2 Z-2 F100\nM5")
    preview = build_preview(result.actions)

    assert len(preview.segments) == 2
    assert preview.segments[0].mode == "rapid"
    assert preview.segments[1].mode == "feed"
    assert preview.min_z_mm == -2
    assert preview.max_x_mm == 2
