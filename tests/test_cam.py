from __future__ import annotations

import pytest

from tcl_lathe_hmi.cam import (
    CamGenerationError,
    CamValidationError,
    HoleSpec,
    LatheCamJob,
    StockSpec,
    TaperSpec,
    TurningSpec,
    generate_cam_program,
)
from tcl_lathe_hmi.cam.generator import _liblathe_commands_to_gcode
from tcl_lathe_hmi.cam.models import finished_profile_points
from tcl_lathe_hmi.gcode import MoveAction, parse_gcode


class FakeCommand:
    def __init__(self, movement: str, params: dict[str, float] | None = None):
        self.movement = movement
        self.params = params or {}

    def get_movement(self):
        return self.movement

    def getParams(self):
        return self.params


def test_cam_validation_rejects_bore_smaller_than_drill():
    job = LatheCamJob(
        hole=HoleSpec(
            drill_diameter_mm=8.0,
            bore_diameter_mm=7.0,
        )
    )

    with pytest.raises(CamValidationError, match="bore diameter"):
        job.validate()


def test_finished_profile_uses_display_diameter_coordinates():
    job = LatheCamJob(
        stock=StockSpec(diameter_mm=20.0, length_mm=60.0),
        turning=TurningSpec(target_diameter_mm=16.0, target_length_mm=50.0),
    )

    assert finished_profile_points(job) == [
        (0.0, 0.0),
        (16.0, 0.0),
        (16.0, -50.0),
        (0.0, -50.0),
    ]


def test_taper_profile_points_are_inside_turned_length():
    job = LatheCamJob(
        stock=StockSpec(diameter_mm=20.0, length_mm=60.0),
        turning=TurningSpec(target_diameter_mm=16.0, target_length_mm=60.0),
        taper=TaperSpec(
            enabled=True,
            start_diameter_mm=16.0,
            end_diameter_mm=12.0,
            start_z_mm=-10.0,
            end_z_mm=-40.0,
        ),
    )

    job.validate()

    assert finished_profile_points(job) == [
        (0.0, 0.0),
        (16.0, 0.0),
        (16.0, -10.0),
        (12.0, -40.0),
        (12.0, -60.0),
        (0.0, -60.0),
    ]


def test_liblathe_command_conversion_doubles_radius_x_and_linearizes_arcs():
    gcode = _liblathe_commands_to_gcode(
        [
            FakeCommand("G0", {"X": 10.0, "Z": 0.0}),
            FakeCommand("G3", {"X": 0.0, "Z": 10.0, "I": -10.0, "K": 0.0, "F": 40.0}),
        ]
    )

    assert gcode[0] == "G0 X20 Z0"
    assert len(gcode) > 2
    assert gcode[-1].startswith("G1 X0 Z10")


def test_generate_cam_program_parses_when_liblathe_is_available():
    try:
        program = generate_cam_program(LatheCamJob())
    except CamGenerationError as exc:
        pytest.skip(str(exc))

    assert "T1 M6 K1" in program.gcode
    assert "M5" in program.gcode
    assert program.part_outline

    parsed = parse_gcode(program.gcode)
    moves = [action for action in parsed.actions if isinstance(action, MoveAction)]
    assert moves
    assert max(action.target_x_mm for action in moves) > 20.0
