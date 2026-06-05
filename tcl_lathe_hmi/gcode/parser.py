from __future__ import annotations

import re
from dataclasses import dataclass

from .actions import CanonicalAction, MoveAction, SpindleAction, ToolChangeAction


WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")
PAREN_COMMENT_RE = re.compile(r"\([^)]*\)")


class GCodeParseError(ValueError):
    def __init__(self, line_number: int, message: str):
        super().__init__(f"line {line_number}: {message}")
        self.line_number = line_number
        self.message = message


@dataclass(frozen=True)
class ParseResult:
    actions: list[CanonicalAction]
    final_x_mm: float
    final_z_mm: float


@dataclass
class _ParserState:
    units_per_program_unit: float = 1.0
    absolute: bool = True
    motion_mode: str = "rapid"
    feed: float | None = None
    spindle_rpm: float = 0.0
    x_mm: float = 0.0
    z_mm: float = 0.0
    tool_number: int | None = None


def parse_gcode(
    text: str,
    *,
    start_x_mm: float = 0.0,
    start_z_mm: float = 0.0,
) -> ParseResult:
    state = _ParserState(x_mm=start_x_mm, z_mm=start_z_mm)
    actions: list[CanonicalAction] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = _strip_comments(raw_line).strip()
        if not line:
            continue

        words = _words(line_number, line)
        if not words:
            continue

        letters = {letter for letter, _value in words}
        for letter, value in words:
            if letter == "G":
                _handle_g(line_number, value, state)

        for letter, value in words:
            if letter == "F":
                state.feed = value
            elif letter == "S":
                state.spindle_rpm = max(0.0, value)
            elif letter == "T":
                state.tool_number = int(value)

        for letter, value in words:
            if letter == "M":
                action = _handle_m(line_number, value, state, raw_line)
                if action is not None:
                    actions.append(action)

        if "X" in letters or "Z" in letters:
            start_x = state.x_mm
            start_z = state.z_mm
            target_x = _target_for_axis(words, "X", state.x_mm, state)
            target_z = _target_for_axis(words, "Z", state.z_mm, state)
            state.x_mm = target_x
            state.z_mm = target_z

            if state.motion_mode in {"arc_cw", "arc_ccw"}:
                for point_x, point_z in _linearized_arc_points(
                    words,
                    start_x,
                    start_z,
                    target_x,
                    target_z,
                    state,
                    clockwise=state.motion_mode == "arc_cw",
                ):
                    actions.append(
                        MoveAction(
                            line_number=line_number,
                            mode="feed",
                            target_x_mm=point_x,
                            target_z_mm=point_z,
                            feed=state.feed,
                            source=raw_line,
                        )
                    )
            else:
                actions.append(
                    MoveAction(
                        line_number=line_number,
                        mode="rapid" if state.motion_mode == "rapid" else "feed",
                        target_x_mm=target_x,
                        target_z_mm=target_z,
                        feed=state.feed,
                        source=raw_line,
                    )
                )

        unsupported = sorted(letters - {"G", "M", "X", "Z", "I", "K", "F", "S", "T", "N"})
        if unsupported:
            raise GCodeParseError(
                line_number,
                f"unsupported word(s): {', '.join(unsupported)}",
            )

    return ParseResult(actions=actions, final_x_mm=state.x_mm, final_z_mm=state.z_mm)


def _strip_comments(line: str) -> str:
    no_parens = PAREN_COMMENT_RE.sub("", line)
    return no_parens.split(";", 1)[0]


def _words(line_number: int, line: str) -> list[tuple[str, float]]:
    words: list[tuple[str, float]] = []
    pos = 0
    while pos < len(line):
        while pos < len(line) and line[pos].isspace():
            pos += 1
        if pos >= len(line):
            break
        match = WORD_RE.match(line, pos)
        if match is None:
            raise GCodeParseError(line_number, f"could not parse full line: {line!r}")
        words.append((match.group(1).upper(), float(match.group(2))))
        pos = match.end()
    if not words:
        raise GCodeParseError(line_number, f"could not parse line: {line!r}")
    return words


def _handle_g(line_number: int, value: float, state: _ParserState) -> None:
    code = int(value)
    if code == 0:
        state.motion_mode = "rapid"
    elif code == 1:
        state.motion_mode = "feed"
    elif code == 2:
        state.motion_mode = "arc_cw"
    elif code == 3:
        state.motion_mode = "arc_ccw"
    elif code == 18:
        return
    elif code == 20:
        state.units_per_program_unit = 25.4
    elif code == 21:
        state.units_per_program_unit = 1.0
    elif code == 90:
        state.absolute = True
    elif code == 91:
        state.absolute = False
    else:
        raise GCodeParseError(line_number, f"unsupported G-code: G{code}")


def _handle_m(
    line_number: int,
    value: float,
    state: _ParserState,
    raw_line: str,
) -> CanonicalAction | None:
    code = int(value)
    if code == 3:
        return SpindleAction(
            line_number=line_number,
            on=True,
            forward=True,
            rpm=state.spindle_rpm,
            source=raw_line,
        )
    if code == 4:
        return SpindleAction(
            line_number=line_number,
            on=True,
            forward=False,
            rpm=state.spindle_rpm,
            source=raw_line,
        )
    if code == 5:
        return SpindleAction(line_number=line_number, on=False, source=raw_line)
    if code == 6:
        return ToolChangeAction(
            line_number=line_number,
            tool_number=_int_word(raw_line, "I") or state.tool_number,
            turret_station=_int_word(raw_line, "K"),
            source=raw_line,
        )
    raise GCodeParseError(line_number, f"unsupported M-code: M{code}")


def _target_for_axis(
    words: list[tuple[str, float]],
    axis: str,
    current_mm: float,
    state: _ParserState,
) -> float:
    for letter, value in words:
        if letter == axis:
            axis_mm = value * state.units_per_program_unit
            if state.absolute:
                return axis_mm
            return current_mm + axis_mm
    return current_mm


def _linearized_arc_points(
    words: list[tuple[str, float]],
    start_x: float,
    start_z: float,
    target_x: float,
    target_z: float,
    state: _ParserState,
    *,
    clockwise: bool,
) -> list[tuple[float, float]]:
    import math

    center_x = start_x + _offset_word(words, "I", state)
    center_z = start_z + _offset_word(words, "K", state)
    start_angle = math.atan2(start_z - center_z, start_x - center_x)
    end_angle = math.atan2(target_z - center_z, target_x - center_x)
    radius = math.hypot(start_x - center_x, start_z - center_z)
    target_radius = math.hypot(target_x - center_x, target_z - center_z)
    if radius <= 1e-9 or abs(radius - target_radius) > max(0.25, radius * 0.05):
        return [(target_x, target_z)]

    if clockwise:
        if end_angle >= start_angle:
            end_angle -= math.tau
    elif end_angle <= start_angle:
        end_angle += math.tau

    sweep = end_angle - start_angle
    arc_length = abs(sweep) * radius
    steps = max(1, min(180, math.ceil(arc_length / 0.5)))
    return [
        (
            center_x + math.cos(start_angle + sweep * (index / steps)) * radius,
            center_z + math.sin(start_angle + sweep * (index / steps)) * radius,
        )
        for index in range(1, steps + 1)
    ]


def _offset_word(
    words: list[tuple[str, float]],
    axis: str,
    state: _ParserState,
) -> float:
    for letter, value in words:
        if letter == axis:
            return value * state.units_per_program_unit
    return 0.0


def _int_word(line: str, letter: str) -> int | None:
    for found, value in _words(0, _strip_comments(line)):
        if found == letter:
            return int(value)
    return None
