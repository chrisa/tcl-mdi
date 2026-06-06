from __future__ import annotations

from tcl_lathe_hmi.backends.sim import SimBackend
from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.gcode import MoveAction, ThreadSyncAction
from tcl_lathe_hmi.machine import MachineService
from tcl_lathe_hmi.tools import ToolRecord


def test_manual_jog_is_blocked_by_soft_limits():
    service = MachineService(
        SimBackend(MachineConfig()),
        config=MachineConfig(x_min_limit_mm=-1.0, x_max_limit_mm=1.0),
    )
    service.connect()

    assert not service.jog_delta(x_mm=2.0, mode="rapid")

    assert service.state.x_mm == 0.0
    assert "outside soft limits" in service.state.status_message


def test_program_move_is_blocked_by_soft_limits():
    service = MachineService(
        SimBackend(MachineConfig()),
        config=MachineConfig(z_min_limit_mm=-1.0, z_max_limit_mm=1.0),
    )
    service.connect()

    action = MoveAction(line_number=12, mode="feed", target_x_mm=0.0, target_z_mm=-2.0)

    assert not service.execute_action(action)
    assert "Line 12" in service.state.status_message
    assert "outside soft limits" in service.state.status_message


def test_thread_sync_move_is_blocked_by_soft_limits():
    service = MachineService(
        SimBackend(MachineConfig()),
        config=MachineConfig(z_min_limit_mm=-1.0, z_max_limit_mm=5.0),
    )
    service.connect()
    assert service.jog_delta(x_mm=0.0, z_mm=2.0, mode="rapid")
    service.backend.wait_idle(timeout_ms=500)
    service.poll()

    action = ThreadSyncAction(
        line_number=14,
        target_z_mm=-2.0,
        pitch_mm=1.5,
    )

    assert not service.execute_action(action)
    assert "Line 14" in service.state.status_message
    assert "outside soft limits" in service.state.status_message


def test_work_offsets_are_separate_from_tool_offsets():
    service = MachineService(SimBackend(MachineConfig()))
    service.tool_table.upsert(
        ToolRecord(tool_number=1, station=1, x_offset_mm=10.0, z_offset_mm=-5.0)
    )
    service.confirm_tool_change(1, 1)

    service.set_work_position(x_mm=12.5, z_mm=-4.0)

    assert service.state.tool_x_offset_mm == 10.0
    assert service.state.tool_z_offset_mm == -5.0
    assert service.state.work_x_offset_mm == 2.5
    assert service.state.work_z_offset_mm == 1.0
    assert service.state.work_x_mm == 12.5
    assert service.state.work_z_mm == -4.0

    service.zero_work_axis("X")
    assert service.state.work_x_mm == 0.0

    service.clear_work_offsets()
    assert service.state.work_x_offset_mm == 0.0
    assert service.state.work_z_offset_mm == 0.0


def test_display_mode_switches_display_coordinate_property():
    service = MachineService(SimBackend(MachineConfig()))
    service.tool_table.upsert(ToolRecord(tool_number=1, station=1, x_offset_mm=3.0))
    service.confirm_tool_change(1, 1)

    assert service.state.display_x_mm == 3.0

    service.set_display_mode("machine")
    assert service.state.display_mode == "machine"
    assert service.state.display_x_mm == 0.0


def test_homing_is_explicitly_unavailable_for_now():
    service = MachineService(SimBackend(MachineConfig()))

    assert not service.home_axis("X")
    assert "unavailable" in service.state.status_message
    assert not service.state.homed_x


def test_coordinate_and_limit_settings_are_persisted(tmp_path):
    settings_path = tmp_path / "machine_state.json"
    service = MachineService(SimBackend(MachineConfig()), settings_path=settings_path)

    service.set_display_mode("machine")
    service.set_work_position(x_mm=3.0, z_mm=-4.0)
    service.update_soft_limits(x_min=-5.0, x_max=6.0, z_min=-7.0, z_max=8.0)

    loaded = MachineService(SimBackend(MachineConfig()), settings_path=settings_path)

    assert loaded.state.display_mode == "machine"
    assert loaded.state.work_x_offset_mm == 3.0
    assert loaded.state.work_z_offset_mm == -4.0
    assert loaded.state.x_min_limit_mm == -5.0
    assert loaded.state.x_max_limit_mm == 6.0
    assert loaded.state.z_min_limit_mm == -7.0
    assert loaded.state.z_max_limit_mm == 8.0
