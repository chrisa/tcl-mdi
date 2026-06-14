from __future__ import annotations

from dataclasses import replace

import pytest

from tcl_lathe_hmi.backends.sim import SimBackend
from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.gcode import ToolChangeAction
from tcl_lathe_hmi.machine import MachineService
from tcl_lathe_hmi.tools import ToolRecord


def test_service_rejects_commands_while_backend_is_busy():
    config = MachineConfig(sim_motion_time_s=0.05)
    backend = SimBackend(config)
    service = MachineService(backend)

    service.connect()
    assert service.state.can_accept_commands

    assert service.jog_delta(x_mm=1.0, mode="rapid")
    assert service.state.busy

    assert not service.jog_delta(z_mm=1.0, mode="rapid")
    assert "not ready" in service.state.status_message

    backend.wait_idle(timeout_ms=500)
    service.poll()
    assert service.state.x_mm == 1.0
    assert service.state.can_accept_commands


def test_service_single_step_holds_jog_until_approved():
    config = MachineConfig(sim_motion_time_s=0.01)
    service = MachineService(SimBackend(config))
    service.connect()

    assert service.set_command_mode("single_step")
    assert service.jog_delta(x_mm=1.0, mode="rapid")

    assert service.command_status.awaiting_approval
    assert "Jog" in service.command_status.current_label
    assert not service.state.busy
    assert service.state.x_mm == 0.0

    assert service.approve_pending_command()
    assert service.state.busy
    service.backend.wait_idle(timeout_ms=500)
    service.poll()

    assert service.state.x_mm == 1.0
    assert not service.command_status.awaiting_approval


def test_service_single_step_cancel_prevents_backend_submission():
    service = MachineService(SimBackend(MachineConfig()))
    service.connect()
    assert service.set_command_mode("single_step")
    assert service.jog_delta(z_mm=1.0, mode="rapid")

    cancel_generation = service.command_status.cancel_generation
    assert service.cancel_pending_command()

    assert service.command_status.cancel_generation == cancel_generation + 1
    assert not service.command_status.awaiting_approval
    assert service.state.z_mm == 0.0
    assert not service.approve_pending_command()
    assert service.state.z_mm == 0.0


def test_service_single_step_holds_spindle_until_approved():
    config = MachineConfig(sim_spindle_command_time_s=0.01)
    service = MachineService(SimBackend(config))
    service.connect()
    assert service.set_command_mode("single_step")

    assert service.set_spindle(on=True, rpm=1200, forward=False)

    assert service.command_status.awaiting_approval
    assert not service.state.spindle.commanded_on

    assert service.approve_pending_command()

    assert service.state.spindle.commanded_on
    assert service.state.spindle.target_rpm == 1200
    assert not service.state.spindle.forward


def test_service_single_step_holds_tool_change_until_approved():
    config = MachineConfig(sim_tool_change_time_s=0.01)
    service = MachineService(SimBackend(config))
    service.connect()
    service.upsert_tool(ToolRecord(tool_number=4, x_offset_mm=-1.0, z_offset_mm=2.5))
    assert service.assign_tool_station(4, 2)
    assert service.set_turret_station(1)
    assert service.set_command_mode("single_step")

    assert service.execute_action(ToolChangeAction(line_number=5, tool_number=4))

    assert service.command_status.awaiting_approval
    assert service.state.active_tool == 0
    assert service.state.turret_station == 1

    assert service.approve_pending_command()

    assert service.state.active_tool == 4
    assert service.state.turret_station == 2
    assert service.state.tool_x_offset_mm == -1.0
    assert service.state.tool_z_offset_mm == 2.5


def test_service_disconnect_resets_state():
    service = MachineService(SimBackend(MachineConfig()))

    service.connect()
    assert service.state.connected

    service.disconnect()
    assert not service.state.connected
    assert not service.state.busy
    assert not service.state.error


def test_service_persists_turret_station_and_active_tool_offsets(tmp_path):
    settings_path = tmp_path / "machine_state.json"
    service = MachineService(
        SimBackend(MachineConfig()),
        settings_path=settings_path,
    )
    service.upsert_tool(
        ToolRecord(tool_number=3, x_offset_mm=1.25, z_offset_mm=-0.5)
    )
    assert service.assign_tool_station(3, 5)

    assert service.set_turret_station(5)
    assert service.confirm_tool_change(3, 5)

    restored = MachineService(
        SimBackend(MachineConfig()),
        settings_path=settings_path,
    )

    assert restored.state.active_tool == 3
    assert restored.state.turret_station == 5
    assert restored.state.tool_x_offset_mm == 1.25
    assert restored.state.tool_z_offset_mm == -0.5


def test_service_teaches_z_offset_from_known_face_position():
    service = MachineService(SimBackend(MachineConfig()))
    service.confirm_tool_change(4, 2)
    service.state = replace(
        service.state,
        z_mm=-12.5,
        work_z_offset_mm=2.5,
        tool_z_offset_mm=0.0,
    )

    assert service.teach_tool_z(0.0)

    tool = service.tool_table.get(4)
    assert tool is not None
    assert tool.z_offset_mm == 10.0
    assert service.state.tool_z_offset_mm == 10.0


def test_service_teaches_x_offset_from_measured_diameter():
    service = MachineService(SimBackend(MachineConfig()))
    service.confirm_tool_change(4, 2)
    service.state = replace(
        service.state,
        x_mm=18.0,
        work_x_offset_mm=3.0,
        tool_x_offset_mm=0.0,
    )

    assert service.teach_tool_x(42.18)

    tool = service.tool_table.get(4)
    assert tool is not None
    assert tool.x_offset_mm == pytest.approx(21.18)
    assert service.state.tool_x_offset_mm == pytest.approx(21.18)


def test_service_persists_taught_tool_table_offsets(tmp_path):
    settings_path = tmp_path / "machine_state.json"
    service = MachineService(SimBackend(MachineConfig()), settings_path=settings_path)
    service.confirm_tool_change(4, 2)
    service.state = replace(service.state, x_mm=18.0, z_mm=-12.5)

    assert service.teach_tool_x(42.0)
    assert service.teach_tool_z(0.0)

    restored = MachineService(SimBackend(MachineConfig()), settings_path=settings_path)
    tool = restored.tool_table.get(4)

    assert tool is not None
    assert tool.x_offset_mm == 24.0
    assert tool.z_offset_mm == 12.5
    assert restored.state.active_tool == 4
    assert restored.state.tool_x_offset_mm == 24.0
    assert restored.state.tool_z_offset_mm == 12.5
