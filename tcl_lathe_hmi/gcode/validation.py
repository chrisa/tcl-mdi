from __future__ import annotations

from collections.abc import Callable, Sequence

from tcl_lathe_hmi.gcode.actions import CanonicalAction, ToolChangeAction
from tcl_lathe_hmi.gcode.preview import build_preview


def preview_limit_error(
    actions: Sequence[CanonicalAction],
    *,
    start_x_mm: float,
    start_z_mm: float,
    limits_error_for_work_target: Callable[[float, float, str], str | None],
    context: str,
) -> str | None:
    preview = build_preview(actions, start_x_mm=start_x_mm, start_z_mm=start_z_mm)
    for segment in preview.segments:
        error = limits_error_for_work_target(
            segment.end_x_mm,
            segment.end_z_mm,
            f"{context} line {segment.line_number}",
        )
        if error is not None:
            return error
    return None


def tool_offset_warning(
    actions: Sequence[CanonicalAction],
    *,
    get_tool: Callable[[int], object | None],
) -> str:
    zero_offset_tools: list[int] = []
    missing_tools: list[int] = []
    seen: set[int] = set()
    for action in actions:
        if not isinstance(action, ToolChangeAction) or action.tool_number is None:
            continue
        tool_number = action.tool_number
        if tool_number in seen:
            continue
        seen.add(tool_number)
        tool = get_tool(tool_number)
        if tool is None:
            missing_tools.append(tool_number)
        elif abs(getattr(tool, "x_offset_mm")) < 1e-9 and abs(getattr(tool, "z_offset_mm")) < 1e-9:
            zero_offset_tools.append(tool_number)
    parts: list[str] = []
    if missing_tools:
        parts.append("missing table row " + ", ".join(f"T{tool}" for tool in missing_tools))
    if zero_offset_tools:
        parts.append("zero offsets " + ", ".join(f"T{tool}" for tool in zero_offset_tools))
    return "Tool setup warning: " + "; ".join(parts) if parts else ""
