from __future__ import annotations

from tcl_lathe_hmi.backends.sim import SimBackend
from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.gcode import MoveAction, ToolChangeAction
from tcl_lathe_hmi.machine import MachineService


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


def test_service_rejects_tool_change_action_for_now():
    service = MachineService(SimBackend(MachineConfig()))
    service.connect()

    action = ToolChangeAction(line_number=5, tool_number=4, turret_station=2)

    assert not service.execute_action(action)
    assert "Tool change requested" in service.state.status_message
