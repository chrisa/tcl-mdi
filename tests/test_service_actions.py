from __future__ import annotations

from tcl_lathe_hmi.backends.sim import SimBackend
from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.gcode import MoveAction, ToolChangeAction
from tcl_lathe_hmi.machine import MachineService
from tcl_lathe_hmi.tools import ToolRecord


def test_service_executes_move_action_as_delta():
    config = MachineConfig(sim_motion_time_s=0.01)
    service = MachineService(SimBackend(config))
    service.connect()

    action = MoveAction(
        line_number=3,
        mode="feed",
        target_x_mm=1.0,
        target_z_mm=-2.0,
        feed=100,
    )

    assert service.execute_action(action, default_feed=50, default_slew=61)
    assert service.state.busy

    service.backend.wait_idle(timeout_ms=500)
    service.poll()

    assert service.state.x_mm == 1.0
    assert service.state.z_mm == -2.0


def test_service_rejects_automatic_tool_change_when_current_station_unknown():
    service = MachineService(SimBackend(MachineConfig()))
    service.connect()

    action = ToolChangeAction(line_number=5, tool_number=4, turret_station=2)

    assert not service.execute_action(action)
    assert service.state.pending_tool is None
    assert service.state.pending_turret_station is None
    assert "current turret station unknown" in service.state.status_message


def test_service_executes_tool_change_when_current_station_known():
    config = MachineConfig(sim_tool_change_time_s=0.01)
    service = MachineService(SimBackend(config))
    service.connect()
    service.tool_table.upsert(
        ToolRecord(tool_number=4, station=2, x_offset_mm=-1.0, z_offset_mm=2.5)
    )
    assert service.set_turret_station(1)

    action = ToolChangeAction(line_number=5, tool_number=4, turret_station=2)

    assert service.execute_action(action)
    assert service.state.busy
    assert service.state.active_tool == 4
    assert service.state.turret_station == 2
    assert service.state.tool_x_offset_mm == -1.0
    assert service.state.tool_z_offset_mm == 2.5
    assert service.state.pending_tool is None

    service.backend.wait_idle(timeout_ms=500)
    service.poll()

    assert not service.state.busy


def test_service_executes_tool_change_from_table_station_without_confirmation():
    config = MachineConfig(sim_tool_change_time_s=0.01)
    service = MachineService(SimBackend(config))
    service.connect()
    assert service.set_turret_station(1)

    action = ToolChangeAction(line_number=5, tool_number=3)

    assert service.execute_action(action)
    assert service.state.busy
    assert service.state.active_tool == 3
    assert service.state.turret_station == 3
    assert service.state.pending_tool is None


def test_service_marks_non_turret_tool_pending_even_when_current_station_known():
    service = MachineService(SimBackend(MachineConfig()))
    service.connect()
    assert service.set_turret_station(1)

    action = ToolChangeAction(line_number=5, tool_number=9)

    assert not service.execute_action(action)
    assert service.state.pending_tool == 9
    assert service.state.pending_turret_station is None
    assert "confirm manual tool change" in service.state.status_message


def test_service_marks_out_of_range_station_pending_for_manual_confirmation():
    service = MachineService(SimBackend(MachineConfig()))
    service.connect()
    assert service.set_turret_station(1)

    action = ToolChangeAction(line_number=5, tool_number=12, turret_station=9)

    assert not service.execute_action(action)
    assert service.state.pending_tool == 12
    assert service.state.pending_turret_station == 9
    assert "confirm manual tool change" in service.state.status_message

    assert service.confirm_tool_change(12, 9)
    assert service.state.active_tool == 12
    assert service.state.turret_station is None


def test_confirm_tool_change_applies_active_offsets():
    service = MachineService(SimBackend(MachineConfig()))
    service.tool_table.upsert(
        ToolRecord(tool_number=4, station=2, x_offset_mm=-1.0, z_offset_mm=2.5)
    )

    assert service.confirm_tool_change(4, 2)

    assert service.state.active_tool == 4
    assert service.state.turret_station == 2
    assert service.state.tool_x_offset_mm == -1.0
    assert service.state.tool_z_offset_mm == 2.5
    assert service.state.work_x_mm == -1.0
    assert service.state.work_z_mm == 2.5


def test_move_action_uses_work_coordinates_with_tool_offsets():
    config = MachineConfig(sim_motion_time_s=0.01)
    service = MachineService(SimBackend(config))
    service.connect()
    service.tool_table.upsert(
        ToolRecord(tool_number=1, station=1, x_offset_mm=10.0, z_offset_mm=-5.0)
    )
    service.confirm_tool_change(1, 1)

    action = MoveAction(
        line_number=10,
        mode="rapid",
        target_x_mm=11.0,
        target_z_mm=-6.0,
    )

    assert service.execute_action(action)
    service.backend.wait_idle(timeout_ms=500)
    service.poll()

    assert service.state.x_mm == 1.0
    assert service.state.z_mm == -1.0
    assert service.state.work_x_mm == 11.0
    assert service.state.work_z_mm == -6.0
