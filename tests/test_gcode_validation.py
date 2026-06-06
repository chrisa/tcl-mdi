from __future__ import annotations

from dataclasses import dataclass

from tcl_lathe_hmi.gcode.actions import MoveAction, ToolChangeAction
from tcl_lathe_hmi.gcode.validation import preview_limit_error, tool_offset_warning


@dataclass(frozen=True)
class FakeTool:
    x_offset_mm: float
    z_offset_mm: float


def test_preview_limit_error_uses_preview_endpoint_and_context():
    actions = [
        MoveAction(
            line_number=12,
            mode="feed",
            target_x_mm=2.0,
            target_z_mm=-5.0,
        )
    ]
    calls: list[tuple[float, float, str]] = []

    def limit_check(x_mm: float, z_mm: float, context: str) -> str | None:
        calls.append((x_mm, z_mm, context))
        return "limit hit"

    assert (
        preview_limit_error(
            actions,
            start_x_mm=0.0,
            start_z_mm=0.0,
            limits_error_for_work_target=limit_check,
            context="Preview",
        )
        == "limit hit"
    )
    assert calls == [(2.0, -5.0, "Preview line 12")]


def test_tool_offset_warning_reports_missing_and_zero_offset_tools_once():
    actions = [
        ToolChangeAction(line_number=1, tool_number=1),
        ToolChangeAction(line_number=2, tool_number=2),
        ToolChangeAction(line_number=3, tool_number=1),
    ]
    tools = {1: FakeTool(x_offset_mm=0.0, z_offset_mm=0.0)}

    warning = tool_offset_warning(actions, get_tool=tools.get)

    assert warning == "Tool setup warning: missing table row T2; zero offsets T1"
