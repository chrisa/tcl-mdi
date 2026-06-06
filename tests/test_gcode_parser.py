from __future__ import annotations

import pytest

from tcl_lathe_hmi.gcode import (
    DwellAction,
    GCodeParseError,
    MoveAction,
    SpindleAction,
    ThreadSyncAction,
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
    result = parse_gcode("M06 I4 K2")
    action = result.actions[0]

    assert isinstance(action, ToolChangeAction)
    assert action.tool_number == 4
    assert action.turret_station == 2


def test_parse_legacy_t_word_tool_change():
    result = parse_gcode("T4 M6 K2")
    action = result.actions[0]

    assert isinstance(action, ToolChangeAction)
    assert action.tool_number == 4
    assert action.turret_station == 2


def test_parse_rejects_unsupported_gcode_before_execution():
    with pytest.raises(GCodeParseError, match="G04 requires F"):
        parse_gcode("G4 X1 Z1 I0 K1")


def test_parse_dwell_action_from_g04_f_seconds():
    result = parse_gcode("G04 F1.25")
    action = result.actions[0]

    assert isinstance(action, DwellAction)
    assert action.seconds == pytest.approx(1.25)


def test_rejects_old_host_canned_cycles():
    with pytest.raises(GCodeParseError, match="unsupported G-code: G81"):
        parse_gcode("G81 X2 Z20 I4 F80")


def test_parse_g33_thread_sync_action():
    result = parse_gcode("G21 G90\nG0 X16 Z0\nG33 Z-20 K1.5")

    assert len(result.actions) == 2
    action = result.actions[1]
    assert isinstance(action, ThreadSyncAction)
    assert action.target_z_mm == pytest.approx(-20.0)
    assert action.pitch_mm == pytest.approx(1.5)
    assert result.final_x_mm == pytest.approx(16.0)
    assert result.final_z_mm == pytest.approx(-20.0)


def test_parse_g33_supports_incremental_and_inches():
    result = parse_gcode("G20 G91\nG33 Z-0.5 K0.05", start_z_mm=2.0)
    action = result.actions[0]

    assert isinstance(action, ThreadSyncAction)
    assert action.target_z_mm == pytest.approx(2.0 - 12.7)
    assert action.pitch_mm == pytest.approx(1.27)


def test_parse_g33_requires_z_and_pitch():
    with pytest.raises(GCodeParseError, match="G33 requires Z target"):
        parse_gcode("G33 K1.5")
    with pytest.raises(GCodeParseError, match="G33 requires K pitch"):
        parse_gcode("G33 Z-20")
    with pytest.raises(GCodeParseError, match="Z-only"):
        parse_gcode("G33 X12 Z-20 K1.5")


def test_old_g70_g71_are_unit_aliases_only_in_old_dialect():
    result = parse_gcode("G70 G91 G1 X0.1\nG71 G1 X1.0", dialect="tcl_old")

    first, second = result.actions
    assert isinstance(first, MoveAction)
    assert first.target_x_mm == pytest.approx(2.54)
    assert isinstance(second, MoveAction)
    assert second.target_x_mm == pytest.approx(3.54)

    with pytest.raises(GCodeParseError, match="unsupported G-code: G70"):
        parse_gcode("G70", dialect="auto")


def test_parse_linearizes_xz_arc_moves():
    result = parse_gcode("G21 G90 G18\nG0 X1 Z0\nG3 X0 Z1 I-1 K0 F100")

    assert len(result.actions) > 2
    assert isinstance(result.actions[-1], MoveAction)
    assert result.actions[-1].mode == "feed"
    assert result.actions[-1].target_x_mm == pytest.approx(0.0)
    assert result.actions[-1].target_z_mm == pytest.approx(1.0)


def test_preview_contains_move_segments_only():
    result = parse_gcode("S1000 M3\nG0 X1 Z0\nG1 X2 Z-2 F100\nM5")
    preview = build_preview(result.actions)

    assert len(preview.segments) == 2
    assert preview.segments[0].mode == "rapid"
    assert preview.segments[1].mode == "feed"
    assert preview.min_z_mm == -2
    assert preview.max_x_mm == 2
